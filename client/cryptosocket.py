import os
import hashlib
from Crypto.Cipher import AES

def hash(*args):
    h = hashlib.sha256()
    for arg in args:
        h.update(arg)
    return h.digest()[0:16]

class CryptoMsgSocket:
    def __init__(self, sock, key, recv_first=False):
        self.secret_key = key
        self.session_key = os.urandom(16)
        self.r_session_key = None
        self.sock = sock

        if recv_first:
            self._recv_sesskey()
            self._send_sesskey()
            ack = self.sock.recv(2)
            if ack != b'OK':
                self.close()
                raise BrokenPipeError('No OK')
        else:
            self._send_sesskey()
            self._recv_sesskey()
            self.sock.send(b'OK')

    def _recv_sesskey(self):
        keys = self.sock.recv(32)
        if len(keys) != 32:
            self.close()
            raise BrokenPipeError('Unexpected stream length')
        self.r_session_key = keys[:16]
        auth = keys[16:]
        if auth != hash(self.secret_key, self.r_session_key):
            self.close()
            raise BrokenPipeError('Authentication failed')

    def _send_sesskey(self):
        self.sock.send(self.session_key)
        self.sock.send(hash(self.secret_key, self.session_key))

    def send(self, data):
        padding_amt = 16 - len(data) % 16
        data += bytes([padding_amt])*padding_amt
        aes = AES.new(self.secret_key, AES.MODE_CBC, self.r_session_key)
        data = aes.encrypt(data)

        length = len(data)//16
        length = bytes([length>>8, length&0xFF])
        auth = hash(self.secret_key, self.r_session_key, data, length)
        self.r_session_key = hash(self.secret_key, self.r_session_key)

        self.sock.send(auth + length + data)
    
    def recv(self):
        data = self.sock.recv(18)
        if len(data) != 18:
            raise BrokenPipeError('Unexpected stream length')

        auth = data[:16]
        length = data[16:]
        block_ct = (length[0] << 8) + length[1]
        data_len = block_ct*16
        ciphertext = self.sock.recv(data_len)

        if len(ciphertext) != data_len:
            raise BrokenPipeError('Unexpected stream length')
        
        if auth != hash(self.secret_key, self.session_key, ciphertext, length):
            raise BrokenPipeError('Signature is invalid')

        aes = AES.new(self.secret_key, AES.MODE_CBC, self.session_key)
        ciphertext = aes.decrypt(ciphertext)
        self.session_key = hash(self.secret_key, self.session_key)
        return ciphertext[:-ciphertext[-1]]

    def close(self):
        self.sock.close()
