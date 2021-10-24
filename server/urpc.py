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

session_life = 600 # 10 minutes

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
    
    def __next__(self):
        if self.iter_sent:
            raise StopIteration()
        else:
            self.iter_sent = True
            return self

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
                if hasattr(f, 'send'):
                    f = CoroPromise(f)
                elif not isinstance(f, Promise):
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

# msgpack tends to have tall stacks
# so we use the scheduler to give it as much room as possible
def msgpack_loads_async(obj):
    def async_task(resolve):
        resolve(msgpack.loads(obj))
    def handler(resolve, _reject):
        micropython.schedule(async_task, resolve)
    return Promise(handler)


def msgpack_dump_async(obj, fp):
    def async_task(resolve):
        msgpack.dump(obj, fp)
        resolve()
    def handler(resolve, _reject):
        micropython.schedule(async_task, resolve)
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
                    elif request == b'RPC':
                        session_key = os.urandom(16)
                        session_exp = time.time() + session_life

                        conn.send(session_key)
                        conn.send(hash(self.secret_key, session_key))
                        r_session_key = await conn.recv(16)
                        if len(r_session_key) != 16:
                            return
                        auth = await conn.recv(16)
                        if len(auth) != 16:
                            return
                        if auth != hash(self.secret_key, r_session_key):
                            return
                        
                        conn.send(b'OK')

                        while True:
                            auth = await conn.recv(16)
                            if len(auth) != 16:
                                return
                            
                            length = await conn.recv(2)
                            if len(length) != 2:
                                return
                            ciphertext, ok = await read_blocks(conn, (length[0] << 8) + length[1])
                            if not ok:
                                return
                            if time.time() < session_exp:
                                session_exp = time.time() + session_life
                            else:
                                return
                            
                            if auth != hash(self.secret_key, session_key, ciphertext, length):
                                return

                            aes = cryptolib.aes(self.secret_key, 2, session_key)
                            aes.decrypt(ciphertext, ciphertext)
                            ciphertext = memoryview(ciphertext)[:-ciphertext[-1]]
                            handler_name, args, kwargs = await msgpack_loads_async(ciphertext)
                            session_key = hash(self.secret_key, session_key)

                            del ciphertext
                            gc.collect()

                            success = True
                            try:
                                data = self.rpc_handlers[handler_name](*args, **kwargs)
                                if is_awaitable(data):
                                    data = await data
                            except Exception as ex:
                                success = False
                                data = [type(ex).__name__, str(ex)]

                            data_acc = ByteAcc()
                            await msgpack_dump_async([success, data], data_acc)
                            data = data_acc.data

                            padding_amt = 16 - len(data) % 16
                            data.extend(bytes([padding_amt])*padding_amt)

                            aes = cryptolib.aes(self.secret_key, 2, r_session_key)
                            aes.encrypt(data, data)

                            length = len(data)//16
                            length = bytes([length>>8, length&0xFF])
                            auth = hash(self.secret_key, r_session_key, data, length)
                            r_session_key = hash(self.secret_key, r_session_key)

                            conn.send(auth)
                            conn.send(length)
                            conn.send(data)

                            del data
                            del auth
                            gc.collect()
                finally:
                    conn.close()
            except Exception as ex:
                print("Unexpected Error:", str(ex))
                import sys
                sys.print_exception(ex)

        def handler(s):
            return CoroPromise(handler_async(s))

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for _ in range(5):
            try:
                s.bind(('', 80))
                print("URPC bound to port 80")
                break
            except OSError as e:
                print("Socket error, will retry 5 times...", str(e))
                time.sleep(1)
        s.listen(5)
        s.setsockopt(socket.SOL_SOCKET, 20, handler)

        @self.rpc("_dir")
        def _dir():
            return list(self.rpc_handlers.keys())
        
        if config.ENABLE_MGMT_API:
            @self.rpc("reset")
            def reset():
                machine.reset()
            
            @self.rpc("soft_reset")
            def soft_reset():
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
