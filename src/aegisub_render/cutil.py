"""Pure-Python helpers reproducing C++ library behaviour (boost::lexical_cast, boost::spirit,
agi::util). Functions that are plain C runtime behaviour live in the compiled `_crt` module."""

import re

INT32_MIN = -(2**31)

_FLOAT_RE = r"[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)"
_FLOAT_FULL = re.compile(_FLOAT_RE, re.IGNORECASE)
_INT_FULL = re.compile(r"[+-]?\d+")


def mid[T: (int, float)](lo: T, v: T, hi: T) -> T:
    """agi::util::mid: clamp v into [lo, hi]."""
    return lo if v < lo else hi if v > hi else v


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


def float_to_string(value: float, precision: int = 3) -> str:
    """float_to_string (src/utils.cpp): "%.3f" with the trailing zeros (and a bare ".") trimmed.

    Python's own float formatting rounds the way the UCRT's printf does (to nearest, ties to
    even), so this does not have to go through _crt.
    """
    s = "%.*f" % (precision, value)
    if "." not in s:  # precision 0: there is nothing to trim (the C++ would eat real digits)
        return s
    pos = len(s.rstrip("0"))
    if pos and s[pos - 1] == ".":
        pos -= 1
    return s[:pos] if pos else s
