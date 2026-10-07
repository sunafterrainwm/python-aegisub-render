"""Small helpers reproducing C/C++ semantics that Aegisub's behaviour depends on."""

import math
import re

INT32_MIN = -(2**31)
INT64_MIN = -(2**63)

_FLOAT_RE = r"[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)"
_FLOAT_FULL = re.compile(_FLOAT_RE, re.IGNORECASE)
_FLOAT_PREFIX = re.compile(r"[ \t\n\v\f\r]*(" + _FLOAT_RE + ")", re.IGNORECASE)
_INT_FULL = re.compile(r"[+-]?\d+")


def cdiv(a: int, b: int) -> int:
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def cmod(a: int, b: int) -> int:
    """C integer remainder (sign follows the dividend)."""
    return a - cdiv(a, b) * b


def wrap_int32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 2**32 if v >= 2**31 else v


def lua_tointeger(n: float) -> int:
    """LuaJIT (GC64) lua_tointeger: a C cast of the double to int64_t."""
    if math.isnan(n) or n >= 2.0**63 or n < -(2.0**63):
        return INT64_MIN  # what cvttsd2si produces for out-of-range values
    return int(n)


def lua_tolong(n: float) -> int:
    """lua_tointeger stored into a C `long`/`int` (32-bit on Windows)."""
    return wrap_int32(lua_tointeger(n))


def mid[T: (int, float)](lo: T, v: T, hi: T) -> T:
    """agi::util::mid: clamp v into [lo, hi]."""
    return lo if v < lo else hi if v > hi else v


def atoi(s: str) -> int:
    """C atoi: optional whitespace, optional sign, digits; stops at the first non-digit."""
    i, n = 0, len(s)
    while i < n and s[i] in " \t\n\v\f\r":
        i += 1
    neg = False
    if i < n and s[i] in "+-":
        neg = s[i] == "-"
        i += 1
    v = 0
    while i < n and "0" <= s[i] <= "9":
        v = v * 10 + ord(s[i]) - 48
        i += 1
    return wrap_int32(-v if neg else v)


def atof(s: str) -> float:
    """C atof (strtod prefix parse); 0.0 when nothing parses."""
    m = _FLOAT_PREFIX.match(s)
    if not m:
        return 0.0
    return float(m.group(1))


def strtol_hex_prefix(s: str) -> int:
    """strtol(s, nullptr, 16) on a string positioned at the first hex digit."""
    v = 0
    for c in s:
        d = "0123456789abcdef".find(c.lower())
        if d < 0:
            break
        v = v * 16 + d
        if v > 2**31 - 1:
            return 2**31 - 1  # LONG_MAX on overflow
    return v


def format_g(v: float) -> str:
    """printf("%g") / iostream default float formatting (precision 6)."""
    return "%g" % v


def boost_lexical_int(s: str) -> int:
    """boost::lexical_cast<int>: the whole string must be an optionally signed decimal integer."""
    if not _INT_FULL.fullmatch(s):
        raise ValueError(f"bad lexical cast: {s!r}")
    v = int(s)
    if not (INT32_MIN <= v < 2**31):
        raise ValueError(f"bad lexical cast: {s!r}")
    return v


def boost_lexical_double(s: str) -> float:
    """boost::lexical_cast<double>: the whole string must be a number."""
    if not _FLOAT_FULL.fullmatch(s):
        raise ValueError(f"bad lexical cast: {s!r}")
    return float(s)


def try_parse_int(s: str) -> int | None:
    """agi::util::try_parse(int): boost::spirit::qi::int_ over the whole string."""
    if not _INT_FULL.fullmatch(s):
        return None
    v = int(s)
    return v if INT32_MIN <= v < 2**31 else None


def try_parse_double(s: str) -> float | None:
    """agi::util::try_parse(double): boost::spirit::qi::double_ over the whole string."""
    if not _FLOAT_FULL.fullmatch(s):
        return None
    return float(s)


def trim(s: str) -> str:
    """boost::trim / agi::Trim (ASCII whitespace)."""
    return s.strip(" \t\n\v\f\r")


def trim_left(s: str) -> str:
    return s.lstrip(" \t\n\v\f\r")
