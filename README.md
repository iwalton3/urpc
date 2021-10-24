# URPC

This is an experimental AES-encrypted RPC API for ESP 8266.

## Usage

The `server` folder contains a sample ESP 8266 project. Simply set the values in `config.py` and flash.

```python
# The project automatically sets up urpc for you
# In your main:

from urpc import rpc

@rpc.rpc()
def test_func(a, b):
    return a + b

@rpc.rpc("alternate_name")
def test_func2(a, b):
    return a - b

# You can also add a handler for HTTP get requests
# You get one handler. It encodes the output in json. You have access to query string args.
@rpc.http()
def http_handler(query_string_args):
    return ["some", {"json": True}, "values"]
```

The `client` folder contains the client, which you use like this:

```python
import urpc
rpc = urpc.URPC('192.168.x.x', b'SECRET_KEY')

# will autoconnect unless you pass False as last param
rpc.test_func(1, 2)
rpc.disconnect()

# rpc.connect()
```

You need https://pypi.org/project/pycrypto/ for the `client`.

Uses umsgpack from: https://github.com/peterhinch/micropython-msgpack
I have bundled umsgpack into one file and made some tweaks for easier use in this project.
