import asyncio
import json
from acryptosocket import CryptoMsgSocket

class URPCError(Exception):
    def __init__(self, error_name="URPCError", message="An unknown error occured."):
        self.error_name = error_name
        self.message = message
    def __str__(self):
        return str(self.error_name) + ": " + str(self.message)

class URPC:
    def __init__(self, address, secret_key, autopopulate=True):
        self.secret_key = secret_key
        self.sock = None
        self.should_populate = autopopulate
        self.address = address
        self.callbacks = {}
        self.cb_id = 0

    async def connect(self):
        s = await asyncio.open_connection(self.address, 80)        
        s = CryptoMsgSocket(s, self.secret_key)
        s.on_msg = self._on_msg
        s.on_eof = self._on_eof
        await s.start(True)
        self.sock = s

        if self.should_populate:
            await self.populate()
            self.should_populate = False

    async def populate(self):
        self.auto_reconnect = False
        for method in await self.call("_dir"):
            setattr(self, method, self._create_wrapper(method))

    def _create_wrapper(self, method):
        async def wrapper(*args, **kwargs):
            return await self.call(method, *args, **kwargs)
        wrapper.__name__ = method
        return wrapper

    def _on_msg(self, data):
        try:
            ident, *data = json.loads(data)
            callback = self.callbacks[ident]
            del self.callbacks[ident]
            callback(True, data)
        except:
            pass

    def _on_eof(self):
        callbacks = self.callbacks
        self.callbacks = {}
        for callback in callbacks.values():
            callback(False, BrokenPipeError('Connection lost'))

    async def _request(self, data):
        if self.sock is None:
            raise BrokenPipeError('Not connected')

        cb_id = self.cb_id
        self.cb_id += 1
        data = [cb_id] + data
        
        f = asyncio.Future()
        def callback(success, data):
            if f.cancelled():
                return
            if success:
                f.set_result(data)
            else:
                f.set_exception(data)
        self.callbacks[cb_id] = callback

        await self.sock.send(json.dumps(data).encode('ascii'))
        return await f

    async def disconnect(self):
        if self.sock:
            sock = self.sock
            self.sock = None
            await sock.close()
    
    async def call(self, name, *args, **kwargs):
        success, result = await self._request([name, list(args), kwargs])
        if success:
            return result
        else:
            error_name, error_msg = result
            raise URPCError(error_name, error_msg)
