"""agi::Time (libaegisub/ass/time.cpp)."""

from aegisub_render._crt import wrap_int32
from aegisub_render.cutil import mid

MAX_TIME = 10 * 60 * 60 * 1000 - 6


def clamp_time(ms: int) -> int:
    """agi::Time(int): clamp to the representable range."""
    return mid(0, ms, MAX_TIME)


def parse_time(text: str) -> int:
    """agi::Time(std::string_view)."""
    time = 0
    after_decimal = -1
    current = 0
    for c in text:
        if c == ":":
            time = wrap_int32(time * 60 + current)
            current = 0
        elif c in ".,":
            time = wrap_int32((time * 60 + current) * 1000)
            current = 0
            after_decimal = 100
        elif not ("0" <= c <= "9"):
            continue
        elif after_decimal < 0:
            current = wrap_int32(current * 10 + (ord(c) - 48))
        else:
            time = wrap_int32(time + (ord(c) - 48) * after_decimal)
            after_decimal //= 10
    if after_decimal < 0:
        time = wrap_int32((time * 60 + current) * 1000)
    return mid(0, time, MAX_TIME)


def time_int(ms: int) -> int:
    """agi::Time::operator int(): always round up for 5ms since the range is [start, stop)."""
    return (ms + 5) - (ms + 5) % 10


def ass_formatted(ms: int, ms_precision: bool = False) -> str:
    t = ms if ms_precision else time_int(ms)
    s = (
        f"{t // 3600000}:{(t % 3600000) // 600000}{(t % 600000) // 60000}:"
        f"{(t % 60000) // 10000}{(t % 10000) // 1000}.{(t % 1000) // 100}{(t % 100) // 10}"
    )
    if ms_precision:
        s += str(t % 10)
    return s


def srt_formatted(ms: int) -> str:
    """agi::Time::GetSrtFormatted: "0H:MM:SS,mmm", on the exact time (no 10ms rounding)."""
    return (
        f"0{ms // 3600000}:{(ms % 3600000) // 600000}{(ms % 600000) // 60000}:"
        f"{(ms % 60000) // 10000}{(ms % 10000) // 1000},"
        f"{(ms % 1000) // 100}{(ms % 100) // 10}{ms % 10}"
    )


def vtt_formatted(ms: int) -> str:
    """A WebVTT timestamp, "0H:MM:SS.mmm" -- GetSrtFormatted with a full stop.

    Not an Aegisub function: WebVTT is not one of its formats. The hours field is always written
    (the spec allows it to be left out, but every player accepts it) and is two digits, which it
    always is here because agi::Time tops out just short of ten hours.
    """
    return srt_formatted(ms).replace(",", ".")
