from urpc import rpc, Promise, delay, simple_http_request

@rpc.rpc()
def test_func(a, b):
    return a + b

# Async functions can't have their name determined
# so you have to pass it into the decorator
@rpc.rpc("test_func2")
async def test_func2():
    await delay(5000) # non-blocking

    def t(resolve, reject):
        reject(RuntimeError("bad news"))
    await Promise(t)

@rpc.rpc("get_my_ip")
async def get_my_ip():
    # non-blocking
    result = await simple_http_request("icanhazip.com")
    return result.strip()

@rpc.rpc("alternate_name")
def test_func2(a, b):
    return a - b

@rpc.rpc(pass_util=True)
def print_on_eof(util, to_print):
    def callback():
        print(to_print)
    util.on_eof(callback)

# You can also add a handler for HTTP get requests
# You get one handler. It encodes the output in json. You have access to query string args.
@rpc.http()
def http_handler(query_string_args):
    return ["some", {"json": True}, "values"]
