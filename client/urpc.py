import socket
import json
from cryptosocket import CryptoMsgSocket

class URPCError(Exception):
    def __init__(self, error_name="URPCError", message="An unknown error occured."):
        self.error_name = error_name
        self.message = message
    def __str__(self):
        return str(self.error_name) + ": " + str(self.message)

class URPC:
    def __init__(self, address, secret_key, connect=True, autopopulate=True):
        self.secret_key = secret_key
        self.sock = None
        self.should_populate = autopopulate
        self.address = address
        self.auto_reconnect = True
        
        if connect:
            self.connect()

    def populate(self):
        old_reconnect = self.auto_reconnect
        self.auto_reconnect = False
        for method in self.call("_dir"):
            setattr(self, method, self._create_wrapper(method))
        self.auto_reconnect = old_reconnect

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.address, 80))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        
        self.sock = CryptoMsgSocket(s, self.secret_key, True)

        if not self.is_populated:
            self.populate()
            self.should_populate = False

    def _create_wrapper(self, method):
        def wrapper(*args, **kwargs):
            return self.call(method, *args, **kwargs)
        wrapper.__name__ = method
        return wrapper

    def _request(self, data):
        if self.sock is None:
            if self.auto_reconnect:
                self.connect()
            else:
                raise BrokenPipeError('Not connected')

        data = [1] + data
        self.sock.send(json.dumps(data).encode('ascii'))
        return json.loads(self.sock.recv())[1:]

    def disconnect(self):
        self.sock.close()
        self.sock = None
    
    def call(self, name, *args, **kwargs):
        try:
            success, result = self._request([name, list(args), kwargs])
        except (BrokenPipeError, ConnectionResetError):
            if self.auto_reconnect:
                self.connect()
                success, result = self._request([name, list(args), kwargs])
            else:
                raise
        if success:
            return result
        else:
            error_name, error_msg = result
            raise URPCError(error_name, error_msg)
