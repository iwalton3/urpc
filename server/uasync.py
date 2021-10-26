import machine
import micropython

class Promise():
    def __init__(self, wrapper):
        self._resolve_callbacks = []
        self._reject_callbacks = []
        self.exception = None
        self.result = None
        self.has_result = False
        self.iter_sent = False

        wrapper(self.set_result, self.set_exception)
    
    def __iter__(self):
        return self
    
    def __next__(self, value=None):
        if self.iter_sent:
            raise StopIteration(value)
        else:
            self.iter_sent = True
            return self

    def send(self, value):
        self.__next__(value)

    def then(self, resolve):
        self._resolve_callbacks.append(resolve)
        if self.has_result:
            self._send_result()
        return self
    
    def catch(self, reject):
        self._reject_callbacks.append(reject)
        if self.has_result:
            self._send_result()
        return self
    
    def set_result(self, result=None):
        self.result = result
        self.has_result = True
        self._send_result()

    def set_exception(self, exception=None):
        self.exception = exception
        self.has_result = True
        self._send_result()

    def _send_result(self):
        if self.has_result:
            if self.exception:
                reject_callbacks = self._reject_callbacks
                self._reject_callbacks = []
                for reject in reject_callbacks:
                    try:
                        micropython.schedule(reject, self.exception)
                    except RuntimeError:
                        reject(self.exception)
            else:
                resolve_callbacks = self._resolve_callbacks
                self._resolve_callbacks = []
                for resolve in resolve_callbacks:
                    try:
                        micropython.schedule(resolve, self.result)
                    except RuntimeError:
                        resolve(self.result)

class CoroPromise(Promise):
    def __init__(self, coro):
        def notify_coro(success, result, resolve, reject):
            try:
                if success:
                    f = coro.send(result)
                else:
                    f = coro.throw(result)
                if not isinstance(f, Promise):
                    if hasattr(f, 'send'):
                        f = CoroPromise(f)
                    else:
                        raise RuntimeError(f"{f} needs to be an Promise or coro")

                def my_resolve(result=None):
                    notify_coro(True, result, resolve, reject)
                def my_throw(result=None):
                    notify_coro(False, result, resolve, reject)

                f.then(my_resolve).catch(my_throw)
            except StopIteration as ex:
                resolve(ex.value)
            except Exception as ex:
                reject(ex)

        def wrapper(resolve, reject):
            notify_coro(True, None, resolve, reject)

        super().__init__(wrapper)

def is_coroutine(obj):
    return hasattr(obj, 'send')

def is_awaitable(obj):
    return hasattr(obj, 'send') or isinstance(obj, Promise)

def delay(ms):
    timer = machine.Timer(-1) # software timer
    def handler(resolve, reject):
        timer.init(period=ms, mode=machine.Timer.ONE_SHOT, callback=resolve)
    return Promise(handler)
