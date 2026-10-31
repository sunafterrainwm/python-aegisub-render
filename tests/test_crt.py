"""C runtime behaviour computed by the C runtime (aegisub_render/_crt.pyx), including the edge
cases a Python emulation got wrong."""

import math
import sys

from aegisub_render import _crt

WINDOWS = sys.platform == "win32"


def test_atoi() -> None:
    """The platform's C library, as Aegisub links it: the MSVC UCRT saturates on overflow, glibc
    returns (int)strtol (64-bit long, truncated)."""
    assert _crt.atoi(" -12abc") == -12
    assert _crt.atoi("99999999999") == (2**31 - 1 if WINDOWS else _crt.wrap_int32(99999999999))
    assert _crt.atoi("-99999999999") == (-(2**31) if WINDOWS else _crt.wrap_int32(-99999999999))
    assert _crt.atoi("x") == 0


def test_strtol_hex() -> None:
    assert _crt.strtol_hex("ff&") == 255
    assert _crt.strtol_hex("0x1F") == 31  # strtol accepts the 0x prefix in base 16
    assert _crt.strtol_hex("FFFFFFFFFF") == (2**31 - 1 if WINDOWS else 0xFFFFFFFFFF)  # long: 32 or 64 bits


def test_printf_formats() -> None:
    assert _crt.format_g(48.0) == "48"
    assert _crt.format_g(37.5) == "37.5"
    assert _crt.format_g(1234567.0) == "1.23457e+06"
    assert _crt.format_g(math.inf) == "inf"
    assert _crt.format_f(16 / 9) == "1.777778"


def test_lua_integer_conversions() -> None:
    assert _crt.lua_tointeger(-2.7) == -2
    assert _crt.lua_tointeger(math.nan) == -(2**63)  # cvttsd2si's "integer indefinite"
    assert _crt.lua_tolong(2.0**33 + 5) == (5 if WINDOWS else 2**33 + 5)  # long is 32-bit on Windows only
    assert _crt.wrap_int32(2**31) == -(2**31)


def test_c_integer_division() -> None:
    assert _crt.cdiv(-7, 2) == -3
    assert _crt.cmod(-7, 2) == -1
