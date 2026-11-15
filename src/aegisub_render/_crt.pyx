# cython: language_level=3, cdivision=True, embedsignature=True, embedsignature.format=python, binding=True
"""C behaviour Aegisub's output depends on, computed by the C runtime itself.

These used to be Python emulations; here they are the MSVC UCRT functions and MSVC x64
conversions that Aegisub 3.5.0 (and the LuaJIT linked into it) actually use, so edge cases such
as overflow saturation in atoi/strtol, "0x" prefixes, or how inf/nan print come out the same.
"""

from libc.stdint cimport int32_t, int64_t, uint32_t, uint64_t
from libc.stdio cimport snprintf
from libc.stdlib cimport atof as c_atof
from libc.stdlib cimport atoi as c_atoi
from libc.stdlib cimport strtol as c_strtol


cdef inline int64_t _int64(object v) except? -1:
    """Wrap an arbitrary Python int to int64_t (two's complement)."""
    return <int64_t><uint64_t>(v & 0xFFFFFFFFFFFFFFFF)


cdef inline bytes _c_string(str s):
    """The bytes of a std::string as C sees them (UTF-8; stops at an embedded NUL)."""
    return s.encode("utf-8", "surrogateescape")


def cdiv(a: int, b: int) -> int:
    """int64_t a / b: truncates toward zero."""
    cdef int64_t x = _int64(a)
    cdef int64_t y = _int64(b)
    if y == 0:
        raise ZeroDivisionError("integer division by zero")
    return x / y


def cmod(a: int, b: int) -> int:
    """int64_t a % b: the sign follows the dividend."""
    cdef int64_t x = _int64(a)
    cdef int64_t y = _int64(b)
    if y == 0:
        raise ZeroDivisionError("integer modulo by zero")
    return x % y


def wrap_int32(v: int) -> int:
    """The value an int (32-bit) ends up with when a wider integer is stored into it."""
    return <int32_t><uint32_t>(v & 0xFFFFFFFF)


def lua_tointeger(n: float) -> int:
    """LuaJIT (GC64) lua_tointeger on a number: the C cast `(lua_Integer)n` (cvttsd2si, so NaN and
    out-of-range values give INT64_MIN)."""
    cdef double d = n
    return <int64_t>d


def lua_tolong(n: float) -> int:
    """`long v = lua_tointeger(L, i)` as in Aegisub's get_int_field: long is 32-bit on Windows."""
    cdef double d = n
    return <long><int64_t>d


def atoi(s: str) -> int:
    """C atoi (MSVC saturates to INT_MIN/INT_MAX on overflow)."""
    return c_atoi(_c_string(s))


def atof(s: str) -> float:
    """C atof (strtod in the "C" locale)."""
    return c_atof(_c_string(s))


def strtol_hex(s: str) -> int:
    """C strtol(s, nullptr, 16) (MSVC saturates to LONG_MIN/LONG_MAX)."""
    return c_strtol(_c_string(s), NULL, 16)


def format_g(v: float) -> str:
    """printf("%g") / the default iostream float format (precision 6), as agi::format renders %g."""
    cdef char buf[64]
    cdef int n = snprintf(buf, sizeof(buf), b"%g", <double>v)
    return buf[:n].decode("ascii")


def format_f(v: float) -> str:
    """std::to_string(double), i.e. printf("%f")."""
    cdef char buf[400]
    cdef int n = snprintf(buf, sizeof(buf), b"%f", <double>v)
    return buf[:n].decode("ascii")
