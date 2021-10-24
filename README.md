# URPC

This is an experimental AES-encrypted RPC API for ESP 8266.

## Usage

The `server` folder contains a sample ESP 8266 project. Simply set the values in `config.py` and flash.

The `client` folder contains the client, which you use like this:

```python
import urpc
rpc = urpc.URPC('192.168.x.x', b'SECRET_KEY')

# will autoconnect unless you pass False as last param
rpc.test_func(1, 2)
rpc.disconnect()

# rpc.connect()
```

Uses umsgpack from: https://github.com/peterhinch/micropython-msgpack
