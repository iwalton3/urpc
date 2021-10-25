try:
  import usocket as socket
except:
  import socket

import json
import time
import hashlib
import os
import cryptolib
import msgpack
import gc
import machine
import config
import micropython

def hash(*args):
    h = hashlib.sha256()
    for arg in args:
        h.update(arg)
    return h.digest()[0:16]

class ByteAcc:
    def __init__(self):
        self.data = bytearray()
    
    def write(self, data):
        self.data.extend(data)

async def read_blocks(conn, blocks):
    length = blocks*16
    data = bytearray(length)
    return data, (await conn.readinto(data, length)) == length

class Promise():
    def __init__(self, wrapper):
        self._resolve_callbacks = []
        self._reject_callbacks = []
        self.exception = None
        self.result = None
        self.has_result = False
        self.iter_sent = False

        wrapper(self.set_result, self.set_exception)
    
    def __iter__(self):
        return self
    
    def __next__(self, value=None):
        if self.iter_sent:
            raise StopIteration(value)
        else:
            self.iter_sent = True
            return self

    def send(self, value):
        self.__next__(value)

    def then(self, resolve):
        self._resolve_callbacks.append(resolve)
        if self.has_result:
            self._send_result()
        return self
    
    def catch(self, reject):
        self._reject_callbacks.append(reject)
        if self.has_result:
            self._send_result()
        return self
    
    def set_result(self, result=None):
        self.result = result
        self.has_result = True
        self._send_result()

    def set_exception(self, exception=None):
        self.exception = exception
        self.has_result = True
        self._send_result()

    def _send_result(self):
        if self.has_result:
            if self.exception:
                reject_callbacks = self._reject_callbacks
                self._reject_callbacks = []
                for reject in reject_callbacks:
                    #reject(self.exception)
                    # this prevents stack overflows
                    micropython.schedule(reject, self.exception)
            else:
                resolve_callbacks = self._resolve_callbacks
                self._resolve_callbacks = []
                for resolve in resolve_callbacks:
                    # resolve(self.result)
                    micropython.schedule(resolve, self.result)

class CoroPromise(Promise):
    def __init__(self, coro):
        def notify_coro(success, result, resolve, reject):
            try:
                if success:
                    f = coro.send(result)
                else:
                    f = coro.throw(result)
                if not isinstance(f, Promise):
                    if hasattr(f, 'send'):
                        f = CoroPromise(f)
                    else:
                        raise RuntimeError(f"{f} needs to be an Promise or coro")

                def my_resolve(result=None):
                    notify_coro(True, result, resolve, reject)
                def my_throw(result=None):
                    notify_coro(False, result, resolve, reject)

                f.then(my_resolve).catch(my_throw)
            except StopIteration as ex:
                resolve(ex.value)
            except Exception as ex:
                reject(ex)

        def wrapper(resolve, reject):
            notify_coro(True, None, resolve, reject)

        super().__init__(wrapper)

def is_awaitable(obj):
    return hasattr(obj, 'send') or isinstance(obj, Promise)

def sched_wrap(coro):
    def wrapper(*args, **kwargs):
        def handler(resolve, reject):
            def async_task(_):
                CoroPromise(coro(*args, **kwargs)).then(resolve).catch(reject)
            micropython.schedule(async_task, None)
        return Promise(handler)
    return wrapper

def promisify(coro):
    def wrapper(*args, **kwargs):
        return CoroPromise(coro(*args, **kwargs))
    return wrapper

def delay(ms):
    timer = machine.Timer(-1) # software timer
    def handler(resolve, reject):
        timer.init(period=ms, mode=machine.Timer.ONE_SHOT, callback=resolve)
    return Promise(handler)

class AsyncSocket:
    def __init__(self, sock):
        self.sock = sock
        self.current_task = None
        self.ready = False
        self.sock.setblocking(False)

        def ready_callback(sock):
            self.ready = True
            if self.current_task:
                task = self.current_task
                self.current_task = None
                task()
        self.sock.setsockopt(socket.SOL_SOCKET, 20, ready_callback)

    def send(self, data):
        self.sock.setblocking(True)
        self.sock.send(data)
        self.sock.setblocking(False)
    
    def sendall(self, data):
        self.sock.setblocking(True)
        self.sock.sendall(data)
        self.sock.setblocking(False)

    def close(self):
        self.sock.close()

    def wait(self):
        def wrapper(resolve, _reject):
            if self.ready:
                resolve()
            if self.current_task is not None:
                raise RuntimeError("Cannot have multiple socket waits")
            self.current_task = resolve
        return Promise(wrapper)

    async def _readinto(self, buffer, length):
        if not self.ready:
            await self.wait()
        read = self.sock.readinto(buffer, length)
        if read == length or read == 0:
            return read
        if read is None:
            read = 0
        self.ready = False
        read += await self.readinto(buffer[read:], length-read)
        return read

    def readinto(self, buffer, length):
        if not isinstance(buffer, memoryview):
            buffer = memoryview(buffer)
        return self._readinto(buffer, length)

    async def recv(self, length):
        buffer = memoryview(bytearray(length))
        read = await self._readinto(buffer, length)
        if read < length:
            buffer = buffer[:read]
        return buffer

class CryptoMsgSocket:
    def __init__(self, sock, key, session_life=600):
        self.secret_key = key
        self.session_life = session_life
        self.session_exp = None
        self.session_key = os.urandom(16)
        self.r_session_key = None
        self.on_msg = None
        self.on_err = None
        self.on_eof = None
        self.sock = sock

    def close(self):
        self.on_err = None
        if self.on_eof is not None:
            self.on_eof()
            self.on_eof = None
        self.sock.close()

    def _close_err(self, reason):
        if self.on_err is not None:
            self.on_err(reason)
            self.on_err = None

    async def _recv_sesskey(self):
        keys = await self.sock.recv(32)
        if len(keys) != 32:
            self.close()
            raise BrokenPipeError('Unexpected stream length')
        self.r_session_key = keys[:16]
        auth = keys[16:]
        if auth != hash(self.secret_key, self.r_session_key):
            self.close()
            raise BrokenPipeError('Authentication failed')

    async def _send_sesskey(self):
        self.session_exp = time.time() + self.session_life

        self.sock.send(self.session_key)
        self.sock.send(hash(self.secret_key, self.session_key))

    @promisify
    async def _recv_loop(self):
        try:
            while True:
                header = await self.sock.recv(18)
                if len(header) != 18:
                    if len(header) != 0:
                        self._close_err("header short read")
                    return

                length = header[16:]
                ciphertext, ok = await read_blocks(self.sock, (length[0] << 8) + length[1])
                if not ok:
                    self._close_err("message short read")
                    return

                if time.time() < self.session_exp:
                    self.session_exp = time.time() + self.session_life
                else:
                    self._close_err("session life expired")
                    return
                
                if header[:16] != hash(self.secret_key, self.session_key, ciphertext, length):
                    self._close_err("invalid authentication header")
                    return

                aes = cryptolib.aes(self.secret_key, 2, self.session_key)
                aes.decrypt(ciphertext, ciphertext)
                micropython.schedule(self.on_msg, memoryview(ciphertext)[:-ciphertext[-1]])
                self.session_key = hash(self.secret_key, self.session_key)
        finally:
            self.close()

    def send(self, data):
        padding_amt = 16 - len(data) % 16
        data.extend(bytes([padding_amt])*padding_amt)

        aes = cryptolib.aes(self.secret_key, 2, self.r_session_key)
        aes.encrypt(data, data)

        length = len(data)//16
        length = bytes([length>>8, length&0xFF])
        auth = hash(self.secret_key, self.r_session_key, data, length)
        self.r_session_key = hash(self.secret_key, self.r_session_key)

        self.sock.send(auth + length)
        self.sock.send(data)

    def set_on_msg(self, on_msg):
        self.on_msg = on_msg

    def set_on_err(self, on_err):
        self.on_err = on_err
    
    def set_on_eof(self, on_eof):
        self.on_eof = on_eof

    async def start(self, is_client=False, socket_inited=True):
        if self.on_msg is None:
            raise RuntimeError('on_msg is required to be set')
        if is_client:
            self.sock.send(b'CRS')
            await self._recv_sesskey()
            await self._send_sesskey()
            ack = await self.sock.recv(2)
            if ack != b'OK':
                self.close()
                raise BrokenPipeError('No OK')
        else:
            if not socket_inited:
                magic = await self.sock.recv(3)
                if magic != b'CRS':
                    self.close()
                    raise BrokenPipeError('Bad protocol')
            await self._send_sesskey()
            await self._recv_sesskey()
            self.sock.send(b'OK')
        self._recv_loop()


def connect(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1])
    return AsyncSocket(s)

async def simple_http_request(address, path="/"):
    s = connect(address, 80)
    try:
        s.send(f"GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n".encode('ascii'))
        result = await s.recv(1024)
    finally:
        s.close()
    return bytes(result).split(b"\r\n\r\n", 1)[-1].decode('utf-8')

class URPC:
    def rpc(self, name=None):
        def deco(func):
            nonlocal name
            if name is None:
                # This is dirty but works
                name = str(func).split(' ')[1]
            self.rpc_handlers[name] = func
            return func
        return deco
    
    def http(self):
        def deco(func):
            self.rpc_handlers['http'] = func
            self.http_request = func
            return func
        return deco

    def __init__(self, secret_key):
        self.http_request = None
        self.rpc_handlers = {}
        self.secret_key = secret_key

        async def handler_async(s):
            should_close = True
            try:
                conn, _ = s.accept()
                conn = AsyncSocket(conn)
                try:
                    request = await conn.recv(3)

                    if request == b'GET':
                        # Handle basic GET requests
                        request = bytes(await conn.recv(1024))
                        qs = request.split(b'\r\n')[0].split(b' ')[1].split(b'?')
                        args = {}
                        if len(qs) > 1:
                            qs = qs[1].decode('ascii').split('&')
                            for pair in qs:
                                k, v = pair.split('=')
                                args[k] = v
                        if self.http_request:
                            result = self.http_request(args)
                            if is_awaitable(result):
                                result = await result
                            response = json.dumps(result)
                        else:
                            response = b'OK'
                        conn.send('HTTP/1.1 200 OK\n')
                        conn.send('Content-Type: text/html\n')
                        conn.send('Connection: close\n\n')
                        conn.sendall(response)
                    elif request == b'CRS':
                        conn = CryptoMsgSocket(conn, self.secret_key)
                        @sched_wrap
                        async def on_msg(msg):
                            identifier, handler_name, args, kwargs = msgpack.loads(msg)

                            success = True
                            try:
                                data = self.rpc_handlers[handler_name](*args, **kwargs)
                                if is_awaitable(data):
                                    data = await data
                            except Exception as ex:
                                success = False
                                data = [type(ex).__name__, str(ex)]

                            data_acc = ByteAcc()
                            msgpack.dump([identifier, success, data], data_acc)
                            conn.send(data_acc.data)
                        
                        conn.set_on_msg(on_msg)
                        await conn.start()
                        should_close = False
                finally:
                    if should_close:
                        conn.close()
            except Exception as ex:
                print("Unexpected Error:", str(ex))
                import sys
                sys.print_exception(ex)

        def handler(s):
            return CoroPromise(handler_async(s))

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        async def try_create_server():
            for i in range(5):
                try:
                    s.bind(('', 80))
                    print("URPC bound to port 80")
                    break
                except OSError as e:
                    print("Socket error, will retry 5 times...", str(e))
                    await delay(1000*2**i)
            s.listen(5)
            s.setsockopt(socket.SOL_SOCKET, 20, handler)
        CoroPromise(try_create_server())

        @self.rpc("_dir")
        def _dir():
            return list(self.rpc_handlers.keys())
        
        if config.ENABLE_MGMT_API:
            @self.rpc("reset")
            def reset():
                s.close()
                machine.reset()
            
            @self.rpc("soft_reset")
            def soft_reset():
                s.close()
                machine.soft_reset()
            
            @self.rpc("eval")
            def _eval(code):
                return eval(code)
            
            @self.rpc("exec")
            def _exec(code):
                return exec(code)
            
            @self.rpc("ls")
            def ls():
                return os.listdir()
            
            @self.rpc("rm")
            def rm(filename):
                return os.remove(filename)
            
            @self.rpc("put")
            def put(filename, contents, mode='wb'):
                with open(filename, mode) as fh:
                    fh.write(contents)
            
            @self.rpc("get")
            def get(filename, mode='rb'):
                with open(filename, mode) as fh:
                    return fh.read()

            @self.rpc("start_webrepl")
            def start_webrepl(password, port=8266):
                import webrepl
                webrepl.start(port=port, password=password)


            @self.rpc("stop_webrepl")
            def stop_webrepl():
                import webrepl
                webrepl.stop()

rpc = URPC(config.SECRET_KEY)
