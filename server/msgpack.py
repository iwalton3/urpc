# umsgpack https://github.com/peterhinch/micropython-msgpack/
# Adapted for MicroPython by Peter Hinch
# Copyright (c) 2021 Peter Hinch Released under the MIT License see LICENSE

import struct
import collections
import io

# Base Exception classes
class PackException(Exception):
    "Base class for exceptions encountered during packing."


class UnpackException(Exception):
    "Base class for exceptions encountered during unpacking."


# Packing error
class UnsupportedTypeException(PackException):
    "Object type not supported for packing."


# Unpacking error
class InsufficientDataException(UnpackException):
    "Insufficient data to unpack the serialized object."


class InvalidStringException(UnpackException):
    "Invalid UTF-8 string encountered during unpacking."


class ReservedCodeException(UnpackException):
    "Reserved code encountered during unpacking."


class UnhashableKeyException(UnpackException):
    """
    Unhashable key encountered during map unpacking.
    The serialized map cannot be deserialized into a Python dictionary.
    """


class DuplicateKeyException(UnpackException):
    "Duplicate key encountered during map unpacking."

class Ext:
    """
    The Ext class facilitates creating a serializable extension object to store
    an application-defined type and data byte array.
    """

    def __init__(self, type, data):
        """
        Construct a new Ext object.
        Args:
            type: application-defined type integer
            data: application-defined data byte array
        TypeError:
            Type is not an integer.
        ValueError:
            Type is out of range of -128 to 127.
        TypeError:
            Data is not type 'bytes'.
        Example:
        >>> foo = umsgpack.Ext(0x05, b"\x01\x02\x03")
        >>> umsgpack.dumps({u"special stuff": foo, u"awesome": True})
        '\x82\xa7awesome\xc3\xadspecial stuff\xc7\x03\x05\x01\x02\x03'
        >>> bar = umsgpack.loads(_)
        >>> print(bar["special stuff"])
        Ext Object (Type: 0x05, Data: 01 02 03)
        >>>
        """
        # Check type is type int
        if not isinstance(type, int):
            raise TypeError("ext type is not type integer")
        if not (-128 <= type <= 127):
            raise ValueError("ext type value {:d} is out of range (-128 to 127)".format(type))
        # Check data is type bytes
        elif not isinstance(data, bytes):
            raise TypeError("ext data is not type \'bytes\'")
        self.type = type
        self.data = data

    def __eq__(self, other):
        """
        Compare this Ext object with another for equality.
        """
        return (isinstance(other, self.__class__) and
                self.type == other.type and
                self.data == other.data)

    def __ne__(self, other):
        """
        Compare this Ext object with another for inequality.
        """
        return not self.__eq__(other)

    def __str__(self):
        """
        String representation of this Ext object.
        """
        s = "Ext Object (Type: {:d}, Data: ".format(self.type)
        s += " ".join(["0x{:02}".format(ord(self.data[i:i + 1]))
                       for i in xrange(min(len(self.data), 8))])
        if len(self.data) > 8:
            s += " ..."
        s += ")"
        return s

    def __hash__(self):
        """
        Provide a hash of this Ext object.
        """
        return hash((self.type, self.data))

ext_class_to_type = {}
ext_type_to_class = {}

def ext_serializable(ext_type):
    """
    Return a decorator to register a class for automatic packing and unpacking
    with the specified Ext type code. The application class should implement a
    `packb()` method that returns serialized bytes, and an `unpackb()` class
    method or static method that accepts serialized bytes and returns an
    instance of the application class.
    Args:
        ext_type: application-defined Ext type code
    Raises:
        TypeError:
            Ext type is not an integer.
        ValueError:
            Ext type is out of range of -128 to 127.
        ValueError:
            Ext type or class already registered.
    """
    def wrapper(cls):
        if not isinstance(ext_type, int):
            raise TypeError("Ext type is not type integer")
        elif not (-128 <= ext_type <= 127):
            raise ValueError("Ext type value {:d} is out of range of -128 to 127".format(ext_type))
        elif ext_type in ext_type_to_class:
            raise ValueError("Ext type {:d} already registered with class {:s}".format(ext_type, repr(ext_type_to_class[ext_type])))
        elif cls in ext_class_to_type:
            raise ValueError("Class {:s} already registered with Ext type {:d}".format(repr(cls), ext_type))

        ext_type_to_class[ext_type] = cls
        ext_class_to_type[cls] = ext_type

        return cls

    return wrapper

try:
    complex()
    have_complex = True
except:
    have_complex = False

def mpext(obj, options):
    if have_complex and isinstance(obj, complex):
        return Complex(obj)
    if isinstance(obj, set):
        return Set(obj)
    if isinstance(obj, tuple):
        return Tuple(obj)
    return obj

@ext_serializable(0x50)
class Complex:
    def __init__(self, c):
        self.c = c

    def __str__(self):
        return "Complex({})".format(self.c)

    def packb(self):
        return struct.pack(">ff", self.c.real, self.c.imag)

    @staticmethod
    def unpackb(data):
        return complex(*struct.unpack(">ff", data))

@ext_serializable(0x51)
class Set:
    def __init__(self, s):
        self.s = s

    def __str__(self):
        return "Set({})".format(self.s)

    def packb(self):  # Must change to list otherwise get infinite recursion
        return dumps(list(self.s))

    @staticmethod
    def unpackb(data):
        return set(loads(data))

@ext_serializable(0x52)
class Tuple:
    def __init__(self, s):
        self.s = s

    def __str__(self):
        return "Tuple({})".format(self.s)

    def packb(self):
        return dumps(list(self.s))  # Infinite recursion

    @staticmethod
    def unpackb(data):
        return tuple(loads(data))

# Auto-detect system float precision
_float_precision = "single" if len(str(1/3)) < 13 else "double"

def _fail():  # Debug code should never be called.
    raise Exception('Logic error')

def _pack_integer(obj, fp):
    if obj < 0:
        if obj >= -32:
            fp.write(struct.pack("b", obj))
        elif obj >= -2**(8 - 1):
            fp.write(b"\xd0")
            fp.write(struct.pack("b", obj))
        elif obj >= -2**(16 - 1):
            fp.write(b"\xd1")
            fp.write(struct.pack(">h", obj))
        elif obj >= -2**(32 - 1):
            fp.write(b"\xd2")
            fp.write(struct.pack(">i", obj))
        elif obj >= -2**(64 - 1):
            fp.write(b"\xd3")
            fp.write(struct.pack(">q", obj))
        else:
            raise UnsupportedTypeException("huge signed int")
    else:
        if obj < 128:
            fp.write(struct.pack("B", obj))
        elif obj < 2**8:
            fp.write(b"\xcc")
            fp.write(struct.pack("B", obj))
        elif obj < 2**16:
            fp.write(b"\xcd")
            fp.write(struct.pack(">H", obj))
        elif obj < 2**32:
            fp.write(b"\xce")
            fp.write(struct.pack(">I", obj))
        elif obj < 2**64:
            fp.write(b"\xcf")
            fp.write(struct.pack(">Q", obj))
        else:
            raise UnsupportedTypeException("huge unsigned int")


def _pack_nil(obj, fp):
    fp.write(b"\xc0")


def _pack_boolean(obj, fp):
    fp.write(b"\xc3" if obj else b"\xc2")


def _pack_float(obj, fp, options):
    fpr = options.get('force_float_precision', _float_precision)
    if fpr == "double":
        fp.write(b"\xcb")
        fp.write(struct.pack(">d", obj))
    elif fpr == "single":
        fp.write(b"\xca")
        fp.write(struct.pack(">f", obj))
    else:
        raise ValueError("invalid float precision")


def _pack_string(obj, fp):
    obj = bytes(obj, 'utf-8')  # Preferred MP encode method
    obj_len = len(obj)
    if obj_len < 32:
        fp.write(struct.pack("B", 0xa0 | obj_len))
    elif obj_len < 2**8:
        fp.write(b"\xd9")
        fp.write(struct.pack("B", obj_len))
    elif obj_len < 2**16:
        fp.write(b"\xda")
        fp.write(struct.pack(">H", obj_len))
    elif obj_len < 2**32:
        fp.write(b"\xdb")
        fp.write(struct.pack(">I", obj_len))
    else:
        raise UnsupportedTypeException("huge string")
    fp.write(obj)

def _pack_binary(obj, fp):
    obj_len = len(obj)
    if obj_len < 2**8:
        fp.write(b"\xc4")
        fp.write(struct.pack("B", obj_len))
    elif obj_len < 2**16:
        fp.write(b"\xc5")
        fp.write(struct.pack(">H", obj_len))
    elif obj_len < 2**32:
        fp.write(b"\xc6")
        fp.write(struct.pack(">I", obj_len))
    else:
        raise UnsupportedTypeException("huge binary string")
    fp.write(obj)

def _pack_ext(obj, fp, tb = b'\x00\xd4\xd5\x00\xd6\x00\x00\x00\xd7\x00\x00\x00\x00\x00\x00\x00\xd8'):
    od = obj.data
    obj_len = len(od)
    ot = obj.type & 0xff
    code = tb[obj_len] if obj_len <= 16 else 0
    if code:
        fp.write(int.to_bytes(code, 1, 'big'))
        fp.write(struct.pack("B", ot))
    elif obj_len < 2**8:
        fp.write(b"\xc7")
        fp.write(struct.pack("BB", obj_len, ot))
    elif obj_len < 2**16:
        fp.write(b"\xc8")
        fp.write(struct.pack(">HB", obj_len, ot))
    elif obj_len < 2**32:
        fp.write(b"\xc9")
        fp.write(struct.pack(">IB", obj_len, ot))
    else:
        raise UnsupportedTypeException("huge ext data")
    fp.write(od)

def _pack_array(obj, fp, options):
    obj_len = len(obj)
    if obj_len < 16:
        fp.write(struct.pack("B", 0x90 | obj_len))
    elif obj_len < 2**16:
        fp.write(b"\xdc")
        fp.write(struct.pack(">H", obj_len))
    elif obj_len < 2**32:
        fp.write(b"\xdd")
        fp.write(struct.pack(">I", obj_len))
    else:
        raise UnsupportedTypeException("huge array")

    for e in obj:
        dump(e, fp, options)

def _pack_map(obj, fp, options):
    obj_len = len(obj)
    if obj_len < 16:
        fp.write(struct.pack("B", 0x80 | obj_len))
    elif obj_len < 2**16:
        fp.write(b"\xde")
        fp.write(struct.pack(">H", obj_len))
    elif obj_len < 2**32:
        fp.write(b"\xdf")
        fp.write(struct.pack(">I", obj_len))
    else:
        raise UnsupportedTypeException("huge array")

    for k, v in obj.items():
        dump(k, fp, options)
        dump(v, fp, options)

def _utype(obj):
    raise UnsupportedTypeException("unsupported type: {:s}".format(str(type(obj))))

# Pack with unicode 'str' type, 'bytes' type
def dump(obj, fp, options={}):
    # return packable object if supported in umsgpack_ext, else return obj
    obj = mpext(obj, options)  
    ext_handlers = options.get("ext_handlers")

    if obj is None:
        _pack_nil(obj, fp)
    elif ext_handlers and obj.__class__ in ext_handlers:
        _pack_ext(ext_handlers[obj.__class__](obj), fp)
    elif obj.__class__ in ext_class_to_type:
        try:
            _pack_ext(Ext(ext_class_to_type[obj.__class__], obj.packb()), fp)
        except AttributeError:
            raise NotImplementedError("Ext class {:s} lacks packb()".format(repr(obj.__class__)))
    elif isinstance(obj, bool):
        _pack_boolean(obj, fp)
    elif isinstance(obj, int):
        _pack_integer(obj, fp)
    elif isinstance(obj, float):
        _pack_float(obj, fp, options)
    elif isinstance(obj, str):
        _pack_string(obj, fp)
    elif isinstance(obj, bytes):
        _pack_binary(obj, fp)
    elif isinstance(obj, (list, tuple)):
        _pack_array(obj, fp, options)
    elif isinstance(obj, dict):
        _pack_map(obj, fp, options)
    elif isinstance(obj, Ext):
        _pack_ext(obj, fp)
    elif ext_handlers:
        # Linear search for superclass
        t = next((t for t in ext_handlers.keys() if isinstance(obj, t)), None)
        if t:
            _pack_ext(ext_handlers[t](obj), fp)
        else:
            _utype(obj)  # UnsupportedType
    elif ext_class_to_type:
        # Linear search for superclass
        t = next((t for t in ext_class_to_type if isinstance(obj, t)), None)
        if t:
            try:
                _pack_ext(Ext(ext_class_to_type[t], obj.packb()), fp)
            except AttributeError:
                _utype(obj)
        else:
            _utype(obj)
    else:
        _utype(obj)

# Interface to __init__.py

def dumps(obj, options={}):
    fp = io.BytesIO()
    dump(obj, fp, options)
    return fp.getvalue()

def _read_except(fp, n):
    if n == 0:
        return b""

    data = fp.read(n)
    if len(data) == 0:
        raise InsufficientDataException()

    while len(data) < n:
        chunk = fp.read(n - len(data))
        if len(chunk) == 0:
            raise InsufficientDataException()

        data += chunk

    return data

def _re0(s, fp, n):
    return struct.unpack(s, _read_except(fp, n))[0]

def _unpack_integer(code, fp):
    ic = ord(code)
    if (ic & 0xe0) == 0xe0:
        return struct.unpack("b", code)[0]
    if (ic & 0x80) == 0x00:
        return struct.unpack("B", code)[0]
    ic -= 0xcc
    off = ic << 1
    try:
        s = "B >H>I>Qb >h>i>q"[off : off + 2]
    except IndexError:
        _fail()
    return _re0(s.strip(), fp, 1 << (ic & 3))


def _unpack_float(code, fp):
    ic = ord(code)
    if ic == 0xca:
        return _re0(">f", fp, 4)
    if ic == 0xcb:
        return _re0(">d", fp, 8)
    _fail()


def _unpack_string(code, fp, options):
    ic = ord(code)
    if (ic & 0xe0) == 0xa0:
        length = ic & ~0xe0
    elif ic == 0xd9:
        length = _re0("B", fp, 1)
    elif ic == 0xda:
        length = _re0(">H", fp, 2)
    elif ic == 0xdb:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    data = _read_except(fp, length)
    try:
        return str(data, 'utf-8')  # Preferred MP way to decode
    except:  # MP does not have UnicodeDecodeError
        if options.get("allow_invalid_utf8"):
            return data  # MP Remove InvalidString class: subclass of built-in class
        raise InvalidStringException("unpacked string is invalid utf-8")


def _unpack_binary(code, fp):
    ic = ord(code)
    if ic == 0xc4:
        length = _re0("B", fp, 1)
    elif ic == 0xc5:
        length = _re0(">H", fp, 2)
    elif ic == 0xc6:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    return _read_except(fp, length)


def _unpack_ext(code, fp, options):
    ic = ord(code)
    n = b'\xd4\xd5\xd6\xd7\xd8'.find(code)
    length = 0 if n < 0 else 1 << n
    if not length:
        if ic == 0xc7:
            length = _re0("B", fp, 1)
        elif ic == 0xc8:
            length = _re0(">H", fp, 2)
        elif ic == 0xc9:
            length = _re0(">I", fp, 4)
        else:
            _fail()

    ext_type = _re0("b", fp, 1)
    ext_data = _read_except(fp, length)

    # Create extension object
    ext = Ext(ext_type, ext_data)

    # Unpack with ext handler, if we have one
    ext_handlers = options.get("ext_handlers")
    if ext_handlers and ext.type in ext_handlers:
        return ext_handlers[ext.type](ext)
    # Unpack with ext classes, if type is registered
    if ext_type in ext_type_to_class:
        try:
            return ext_type_to_class[ext_type].unpackb(ext_data)
        except AttributeError:
            raise NotImplementedError("Ext class {:s} lacks unpackb()".format(repr(ext_type_to_class[ext_type])))

    return ext

def _unpack_array(code, fp, options):
    ic = ord(code)
    if (ic & 0xf0) == 0x90:
        length = (ic & ~0xf0)
    elif ic == 0xdc:
        length = _re0(">H", fp, 2)
    elif ic == 0xdd:
        length = _re0(">I", fp, 4)
    else:
        _fail()
    g = (load(fp, options) for i in range(length))  # generator
    return tuple(g) if options.get('use_tuple') else list(g)


def _deep_list_to_tuple(obj):
    if isinstance(obj, list):
        return tuple([_deep_list_to_tuple(e) for e in obj])
    return obj


def _unpack_map(code, fp, options):
    ic = ord(code)
    if (ic & 0xf0) == 0x80:
        length = (ic & ~0xf0)
    elif ic == 0xde:
        length = _re0(">H", fp, 2)
    elif ic == 0xdf:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    d = {} if not options.get('use_ordered_dict') \
        else collections.OrderedDict()
    for _ in range(length):
        # Unpack key
        k = load(fp, options)

        if isinstance(k, list):
            # Attempt to convert list into a hashable tuple
            k = _deep_list_to_tuple(k)
        try:
            hash(k)
        except:
            raise UnhashableKeyException(
                "unhashable key: \"{:s}\"".format(str(k)))
        if k in d:
            raise DuplicateKeyException(
                "duplicate key: \"{:s}\" ({:s})".format(str(k), str(type(k))))

        # Unpack value
        v = load(fp, options)

        try:
            d[k] = v
        except TypeError:
            raise UnhashableKeyException(
                "unhashable key: \"{:s}\"".format(str(k)))
    return d


def load(fp, options={}):
    code = _read_except(fp, 1)
    ic = ord(code)
    if (ic <= 0x7f) or (0xcc <= ic <= 0xd3) or (0xe0 <= ic <= 0xff):
        return _unpack_integer(code, fp)
    if ic <= 0xc9:
        if ic <= 0xc3:
            if ic <= 0x8f:
                return _unpack_map(code, fp, options)
            if ic <= 0x9f:
                return _unpack_array(code, fp, options)
            if ic <= 0xbf:
                return _unpack_string(code, fp, options)
            if ic == 0xc1:
                raise ReservedCodeException("got reserved code: 0xc1")
            return (None, 0, False, True)[ic - 0xc0]
        if ic <= 0xc6:
            return _unpack_binary(code, fp)
        return _unpack_ext(code, fp, options)
    if ic <= 0xcb:
        return _unpack_float(code, fp)
    if ic <= 0xd8:
        return _unpack_ext(code, fp, options)
    if ic <= 0xdb:
        return _unpack_string(code, fp, options)
    if ic <= 0xdd:
        return _unpack_array(code, fp, options)
    return _unpack_map(code, fp, options)

# Interface to __init__.py

def loads(s, options={}):
    if not isinstance(s, (bytes, bytearray, memoryview)):
        raise TypeError("packed data must be type 'bytes' or 'bytearray'")
    return load(io.BytesIO(s), options)
