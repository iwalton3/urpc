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

def read_blocks(conn, blocks):
    length = blocks*16
    data = bytearray(length)
    return data, conn.readinto(data, length) == length

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
        self.session_key = None
        self.session_exp = None
        self.r_session_key = None

        def handler(s):
            try:
                conn, _ = s.accept()
                try:
                    request = conn.recv(3)

                    if request == b'GET':
                        # Handle basic GET requests
                        request = conn.recv(1024)
                        qs = request.split(b'\r\n')[0].split(b' ')[1].split(b'?')
                        args = {}
                        if len(qs) > 1:
                            qs = qs[1].decode('ascii').split('&')
                            for pair in qs:
                                k, v = pair.split('=')
                                args[k] = v
                        if self.http_request:
                            response = json.dumps(self.http_request(args))
                        else:
                            response = b'OK'
                        conn.send('HTTP/1.1 200 OK\n')
                        conn.send('Content-Type: text/html\n')
                        conn.send('Connection: close\n\n')
                        conn.sendall(response)
                    elif request == b'RPC':
                        self.session_key = os.urandom(16)
                        self.session_exp = time.time() + session_life

                        conn.send(self.session_key)
                        conn.send(hash(self.secret_key, self.session_key))
                        self.r_session_key = conn.recv(16)
                        if len(self.r_session_key) != 16:
                            return
                        auth = conn.recv(16)
                        if len(auth) != 16:
                            return
                        if auth != hash(self.secret_key, self.r_session_key):
                            return
                        
                        conn.send(b'OK')

                        while True:
                            auth = conn.recv(16)
                            if len(auth) != 16:
                                return
                            
                            length = conn.recv(2)
                            if len(length) != 2:
                                return
                            ciphertext, ok = read_blocks(conn, (length[0] << 8) + length[1])
                            if not ok:
                                return
                            if time.time() < self.session_exp:
                                self.session_exp = time.time() + session_life
                            else:
                                return
                            
                            if auth != hash(self.secret_key, self.session_key, ciphertext, length):
                                return

                            aes = cryptolib.aes(self.secret_key, 2, self.session_key)
                            aes.decrypt(ciphertext, ciphertext)
                            ciphertext = memoryview(ciphertext)[:-ciphertext[-1]]
                            handler_name, args, kwargs = msgpack.loads(ciphertext)
                            self.session_key = hash(self.secret_key, self.session_key)

                            del ciphertext
                            gc.collect()

                            success = True
                            try:
                                data = self.rpc_handlers[handler_name](*args, **kwargs)
                            except Exception as ex:
                                success = False
                                data = (type(ex).__name__, str(ex))

                            data_acc = ByteAcc()
                            msgpack.dump((success, data), data_acc)
                            data = data_acc.data

                            padding_amt = 16 - len(data) % 16
                            data.extend(bytes([padding_amt])*padding_amt)

                            aes = cryptolib.aes(self.secret_key, 2, self.r_session_key)
                            aes.encrypt(data, data)

                            length = len(data)//16
                            length = bytes([length>>8, length&0xFF])
                            auth = hash(self.secret_key, self.r_session_key, data, length)
                            self.r_session_key = hash(self.secret_key, self.r_session_key)

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

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for _ in range(5):
            try:
                s.bind(('', 80))
                break
            except OSError as e:
                print("Socket error, will retry 5 times...", str(e))
                time.sleep(1)
        s.listen(5)
        s.setsockopt(socket.SOL_SOCKET, 20, handler)

        @self.rpc("_dir")
        def _dir():
            return list(self.rpc_handlers.keys())
        
        @self.rpc("reset")
        def reset():
            machine.reset()
        
        @self.rpc("soft_reset")
        def soft_reset():
            machine.soft_reset()
        
        @self.rpc("eval")
        def _eval(code):
            return eval(code)
        
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
