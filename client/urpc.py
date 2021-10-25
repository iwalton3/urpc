import socket
import select
import hashlib
import umsgpack
import os
from Crypto.Cipher import AES

class URPCError(Exception):
    def __init__(self, error_name="URPCError", message="An unknown error occured."):
        self.error_name = error_name
        self.message = message
    def __str__(self):
        return str(self.error_name) + ": " + str(self.message)

def hash(*args):
    h = hashlib.sha256()
    for arg in args:
        h.update(arg)
    return h.digest()[0:16]

class URPC:
    def __init__(self, address, secret_key, connect=True):
        self.secret_key = secret_key
        self.r_session_key = None
        self.sock = None
        self.is_populated = False
        self.address = address
        
        if connect:
            self.connect()

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.address, 80))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.session_key = os.urandom(16)

        # Start handshake
        s.send(b'RPC')

        # Validate server
        data = s.recv(32)
        if len(data) != 32:
            s.close()
            raise EOFError('Unexpected stream length')
        self.r_session_key = data[:16]
        auth = data[16:]
        if auth != hash(self.secret_key, self.r_session_key):
            s.close()
            raise ValueError('Got a bad authorization header')
        
        # Prove client
        s.send(self.session_key + hash(self.secret_key, self.session_key))

        # Should get an OK back
        if s.recv(2) != b'OK':
            s.close()
            raise EOFError('Handshake failed')
        
        self.sock = s

        if not self.is_populated:
            for method in self.call("_dir"):
                setattr(self, method, self._create_wrapper(method))
            self.is_populated = True

    def is_connected(self):
        if self.sock is None:
            return False
        try:
            ready_to_read, ready_to_write, in_error = \
                select.select([self.sock,], [self.sock,], [], 5)
        except select.error:
            self.sock.close()
            self.sock = None
            return False
        return True

    def _create_wrapper(self, method):
        def wrapper(*args, **kwargs):
            return self.call(method, *args, **kwargs)
        wrapper.__name__ = method
        return wrapper

    def _request(self, data):
        if not self.is_connected():
            self.connect()

        data = umsgpack.dumps(data)
        padding_amt = 16 - len(data) % 16
        data += bytes([padding_amt])*padding_amt
        aes = AES.new(self.secret_key, AES.MODE_CBC, self.r_session_key)
        data = aes.encrypt(data)

        length = len(data)//16
        length = bytes([length>>8, length&0xFF])
        auth = hash(self.secret_key, self.r_session_key, data, length)
        self.r_session_key = hash(self.secret_key, self.r_session_key)

        self.sock.send(auth + length + data)

        data = self.sock.recv(18)
        if len(data) != 18:
            raise EOFError('Unexpected stream length')

        auth = data[:16]
        length = data[16:]
        block_ct = (length[0] << 8) + length[1]
        data_len = block_ct*16
        ciphertext = self.sock.recv(data_len)

        if len(ciphertext) != data_len:
            raise EOFError('Unexpected stream length')
        
        if auth != hash(self.secret_key, self.session_key, ciphertext, length):
            raise EOFError('Signature is invalid')

        aes = AES.new(self.secret_key, AES.MODE_CBC, self.session_key)
        ciphertext = aes.decrypt(ciphertext)
        ciphertext = ciphertext[:-ciphertext[-1]]
        self.session_key = hash(self.secret_key, self.session_key)

        return umsgpack.loads(ciphertext)
        
    def disconnect(self):
        self.sock.close()
        self.sock = None
    
    def call(self, name, *args, **kwargs):
        success, result = self._request([name, list(args), kwargs])
        if success:
            return result
        else:
            error_name, error_msg = result
            raise URPCError(error_name, error_msg)
