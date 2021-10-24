from urpc import rpc, Promise

@rpc.rpc()
def test_func(a, b):
    return a + b

# Async functions can't have their name determined
# so you have to pass it into the decorator
@rpc.rpc("test_func2")
async def test_func2():
    def t(resolve, reject):
        reject(RuntimeError("bad news"))
    await Promise(t)

@rpc.rpc("alternate_name")
def test_func2(a, b):
    return a - b

# You can also add a handler for HTTP get requests
# You get one handler. It encodes the output in json. You have access to query string args.
@rpc.http()
def http_handler(query_string_args):
    return ["some", {"json": True}, "values"]
