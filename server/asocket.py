import socket
from uasync import Promise

class AsyncSocket:
    def __init__(self, sock):
        self.sock = sock
        self.current_task = None
        self.ready = False
        self.sock.setblocking(False)

        def ready_callback(sock):
            self.ready = True
            if self.current_task:
                task = self.current_task
                self.current_task = None
                task()
        self.sock.setsockopt(socket.SOL_SOCKET, 20, ready_callback)

    def send(self, data):
        self.sock.setblocking(True)
        self.sock.send(data)
        self.sock.setblocking(False)
    
    def sendall(self, data):
        self.sock.setblocking(True)
        self.sock.sendall(data)
        self.sock.setblocking(False)

    def close(self):
        self.sock.close()

    def wait(self):
        def wrapper(resolve, _reject):
            if self.ready:
                resolve()
            if self.current_task is not None:
                raise RuntimeError("Cannot have multiple socket waits")
            self.current_task = resolve
        return Promise(wrapper)

    async def _readinto(self, buffer, length):
        if not self.ready:
            await self.wait()
        read = self.sock.readinto(buffer, length)
        if read == length or read == 0:
            return read
        if read is None:
            read = 0
        self.ready = False
        read += await self.readinto(buffer[read:], length-read)
        return read

    def readinto(self, buffer, length):
        if not isinstance(buffer, memoryview):
            buffer = memoryview(buffer)
        return self._readinto(buffer, length)

    async def recv(self, length):
        buffer = memoryview(bytearray(length))
        read = await self._readinto(buffer, length)
        if read < length:
            buffer = buffer[:read]
        return buffer

def connect(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1])
    return AsyncSocket(s)

async def simple_http_request(address, path="/"):
    s = connect(address, 80)
    try:
        s.send(f"GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n".encode('ascii'))
        result = await s.recv(1024)
    finally:
        s.close()
    return bytes(result).split(b"\r\n\r\n", 1)[-1].decode('utf-8')
