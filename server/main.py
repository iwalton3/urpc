from uasync import Promise, delay
from asocket import simple_http_request
from urpc import rpc

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

# stack limitation:
# test() returns 9 and test2()'s delegate returns 20

# Note that you can only have a few micropython.schedule calls
# in flight at any given time. Hence why I don't auto-wrap functions
# with it amymore.
@rpc.rpc()
def test():
    try:
        return test() + 1
    except:
        return 0

@rpc.rpc("test2")
def test2():
    def delegate(resolve):
        resolve(test())
    def call_delegate(resolve, reject):
        import micropython
        micropython.schedule(delegate, resolve)
    return Promise(call_delegate)

# I get 21250 from a raw repl
# Between 15750 and 20500 with an rpc session
@rpc.rpc("test3")
def test3():
    import gc
    i=0
    while True:
        try:
            a = bytes(i+250)
            del a
            gc.collect()
            i += 250
        except:
            return i
