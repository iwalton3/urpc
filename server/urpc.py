import usocket as socket
import json
import config

from uasync import CoroPromise, is_awaitable, delay
from asocket import AsyncSocket
from cryptosocket import CryptoMsgSocket

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

    def __init__(self, secret_key):
        self.http_request = None
        self.rpc_handlers = {}
        self.secret_key = secret_key

        async def handler_async(s):
            try:
                conn, _ = s.accept()
                conn = AsyncSocket(conn)
                conn = CryptoMsgSocket(conn, self.secret_key)

                async def on_msg_async(msg):
                    identifier, handler_name, args, kwargs = json.loads(msg)

                    success = True
                    try:
                        data = self.rpc_handlers[handler_name](*args, **kwargs)
                        if is_awaitable(data):
                            data = await data
                    except Exception as ex:
                        success = False
                        data = [type(ex).__name__, str(ex)]

                    conn.send(bytearray(json.dumps([identifier, success, data])))
                
                def on_msg(msg):
                    CoroPromise(on_msg_async(msg))

                conn.on_msg = on_msg
                await conn.start()
            except Exception as ex:
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

rpc = URPC(config.SECRET_KEY)
