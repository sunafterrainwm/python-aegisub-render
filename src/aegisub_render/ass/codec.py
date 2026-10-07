"""libaegisub/ass/string_codec.cpp and uuencode.cpp.

Strings are Python ``str`` holding UTF-8 decoded with ``surrogateescape`` so that arbitrary
bytes round-trip; these codecs operate on the underlying bytes exactly like the C++ code.
"""

from aegisub_render.textenc import from_bytes, to_bytes


def inline_string_encode(value: str) -> str:
    out = bytearray()
    for c in to_bytes(value):
        if c <= 0x1F or c in (0x23, 0x2C, 0x3A, 0x7C):
            out += b"#%02X" % c
        else:
            out.append(c)
    return from_bytes(bytes(out))


def inline_string_decode(value: str) -> str:
    data = to_bytes(value)
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] != 0x23 or i + 2 >= len(data):
            out.append(data[i])
        else:
            hexpart = data[i + 1 : i + 3].decode("latin-1")
            v = 0
            for ch in hexpart:  # strtol(buff, nullptr, 16): stops at the first invalid digit
                d = "0123456789abcdef".find(ch.lower())
                if d < 0:
                    break
                v = v * 16 + d
            out.append(v & 0xFF)
            i += 2
        i += 1
    return from_bytes(bytes(out))


def uuencode(data: bytes, insert_linebreaks: bool) -> str:
    size = len(data)
    ret = bytearray()
    written = 0
    for pos in range(0, size, 3):
        src = data[pos : pos + 3].ljust(3, b"\0")
        dst = (
            src[0] >> 2,
            ((src[0] & 0x3) << 4) | ((src[1] & 0xF0) >> 4),
            ((src[1] & 0xF) << 2) | ((src[2] & 0xC0) >> 6),
            src[2] & 0x3F,
        )
        for i in range(min(size - pos + 1, 4)):
            ret.append(dst[i] + 33)
            if insert_linebreaks:
                written += 1
                if written == 80 and pos + 3 < size:
                    written = 0
                    ret += b"\r\n"
    return ret.decode("latin-1")


def uudecode(text: str) -> bytes:
    data = to_bytes(text)
    ret = bytearray()
    length = len(data)
    pos = 0
    while pos + 1 < length:
        nbytes = 0
        src = [0, 0, 0, 0]
        i = 0
        while i < 4 and pos < length:
            c = data[pos]
            if c and c not in (0x0A, 0x0D):
                src[i] = (c - 33) & 0xFF
                i += 1
                nbytes += 1
            pos += 1
        if nbytes > 1:
            ret.append(((src[0] << 2) | (src[1] >> 4)) & 0xFF)
        if nbytes > 2:
            ret.append((((src[1] & 0xF) << 4) | (src[2] >> 2)) & 0xFF)
        if nbytes > 3:
            ret.append((((src[2] & 0x3) << 6) | src[3]) & 0xFF)
    return bytes(ret)
