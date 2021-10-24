from urpc import rpc

@rpc.rpc()
def test_func(a, b):
    return a + b
