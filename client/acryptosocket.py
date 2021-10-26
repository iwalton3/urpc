import os
import hashlib
import asyncio
from Crypto.Cipher import AES

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
        self.r, self.w = sock

    async def start(self, recv_first=False):
        if self.on_msg is None:
            raise RuntimeError('on_msg is required to be set')
        if recv_first:
            await self._recv_sesskey()
            await self._send_sesskey()
            ack = await self.r.read(2)
            if ack != b'OK':
                await self.close()
                raise BrokenPipeError('No OK')
        else:
            await self._send_sesskey()
            await self._recv_sesskey()
            self.w.write(b'OK')
            await self.w.drain()
        asyncio.create_task(self._recv_loop())

    async def _recv_sesskey(self):
        keys = await self.r.read(32)
        if len(keys) != 32:
            await self.close()
            raise BrokenPipeError('Unexpected stream length')
        self.r_session_key = keys[:16]
        auth = keys[16:]
        if auth != hash(self.secret_key, self.r_session_key):
            await self.close()
            raise BrokenPipeError('Authentication failed')

    async def _send_sesskey(self):
        self.w.write(self.session_key + hash(self.secret_key, self.session_key))
        await self.w.drain()

    async def send(self, data):
        padding_amt = 16 - len(data) % 16
        data += bytes([padding_amt])*padding_amt
        aes = AES.new(self.secret_key, AES.MODE_CBC, self.r_session_key)
        data = aes.encrypt(data)

        length = len(data)//16
        length = bytes([length>>8, length&0xFF])
        auth = hash(self.secret_key, self.r_session_key, data, length)
        self.r_session_key = hash(self.secret_key, self.r_session_key)

        self.w.write(auth + length + data)
        await self.w.drain()

    async def _recv_loop(self):
        try:
            while True:
                data = await self.r.read(18)
                if len(data) != 18:
                    raise BrokenPipeError('Unexpected stream length')

                auth = data[:16]
                length = data[16:]
                block_ct = (length[0] << 8) + length[1]
                data_len = block_ct*16
                ciphertext = await self.r.read(data_len)

                if len(ciphertext) != data_len:
                    raise BrokenPipeError('Unexpected stream length')
                
                if auth != hash(self.secret_key, self.session_key, ciphertext, length):
                    raise BrokenPipeError('Signature is invalid')

                aes = AES.new(self.secret_key, AES.MODE_CBC, self.session_key)
                ciphertext = aes.decrypt(ciphertext)
                self.session_key = hash(self.secret_key, self.session_key)
                self.on_msg(ciphertext[:-ciphertext[-1]])
        except BrokenPipeError as ex:
            self._close_err(ex)
        finally:
            await self.close()

    def _close_err(self, reason):
        if self.on_err is not None:
            self.on_err(reason)
            self.on_err = None

    async def close(self):
        self.on_err = None
        if self.on_eof is not None:
            self.on_eof()
            self.on_eof = None
        self.w.close()
        await self.w.wait_closed()

