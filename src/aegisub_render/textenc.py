"""Byte <-> str conversion used everywhere a C++ std::string or a Lua string crosses into Python.

Aegisub and Lua treat strings as UTF-8 bytes; Python code keeps them as ``str`` decoded with
``surrogateescape`` so that invalid UTF-8 survives a round trip unchanged.
"""


def from_bytes(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def to_bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def to_wide(text: str) -> str:
    """Text as Windows would see it after agi::charset::ConvertW (invalid bytes become U+FFFD)."""
    return to_bytes(text).decode("utf-8", "replace")
