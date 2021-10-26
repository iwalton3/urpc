import hashlib
import os
import cryptolib
import micropython
from uasync import CoroPromise

def hash(*args):
    h = hashlib.sha256()
    for arg in args:
        h.update(arg)
    return h.digest()[0:16]

class CryptoMsgSocket:
    def __init__(self, sock, key):
        self.secret_key = key
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
            raise EOFError('Unexpected stream length')
        self.r_session_key = keys[:16]
        auth = keys[16:]
        if auth != hash(self.secret_key, self.r_session_key):
            self.close()
            raise EOFError('Authentication failed')

    async def _send_sesskey(self):
        self.sock.send(self.session_key)
        self.sock.send(hash(self.secret_key, self.session_key))

    async def _recv_loop(self):
        try:
            while True:
                header = await self.sock.recv(18)
                if len(header) != 18:
                    if len(header) != 0:
                        self._close_err("header short read")
                    return

                length = header[16:]
                get_length = ((length[0] << 8) + length[1])*16
                ciphertext = bytearray(get_length)
                
                if not (await self.sock.readinto(ciphertext, get_length)) == get_length:
                    self._close_err("message short read")
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

    async def start(self, is_client=False):
        if self.on_msg is None:
            raise RuntimeError('on_msg is required to be set')
        if is_client:
            await self._recv_sesskey()
            await self._send_sesskey()
            ack = await self.sock.recv(2)
            if ack != b'OK':
                self.close()
                raise EOFError('No OK')
        else:
            await self._send_sesskey()
            await self._recv_sesskey()
            self.sock.send(b'OK')
        CoroPromise(self._recv_loop())