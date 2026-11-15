"""agi::vfr::Framerate (libaegisub/common/vfr.cpp)."""

import bisect
import math
import re
from pathlib import Path

from aegisub_render._crt import cdiv
from aegisub_render.cutil import try_parse_double

DEFAULT_DENOMINATOR = 1_000_000_000
MAX_TIMECODES = 10_000_000

START = "start"
END = "end"
EXACT = "exact"


class FramerateError(Exception):
    pass


def _i32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 2**32 if v >= 2**31 else v


class Framerate:
    def __init__(self) -> None:
        self.denominator = 0
        self.numerator = 0
        self.last = 0
        self.timecodes: list[int] = []
        self.drop = False

    # -- constructors

    @staticmethod
    def from_fps(fps: float) -> Framerate:
        if not math.isfinite(fps) or fps < 0:
            raise FramerateError("FPS must be greater than zero")
        if fps > 1000:
            raise FramerateError("FPS must not be greater than 1000")
        f = Framerate()
        f.denominator = DEFAULT_DENOMINATOR
        f.numerator = int(fps * DEFAULT_DENOMINATOR)
        f.timecodes = [0]
        return f

    @staticmethod
    def from_ratio(numerator: int, denominator: int, drop: bool = True) -> Framerate:
        if numerator <= 0 or denominator <= 0:
            raise FramerateError("Numerator and denominator must both be greater than zero")
        if numerator // denominator > 1000:
            raise FramerateError("FPS must not be greater than 1000")
        f = Framerate()
        f.numerator = numerator
        f.denominator = denominator
        f.drop = drop and numerator % denominator != 0
        f.timecodes = [0]
        return f

    @staticmethod
    def from_timecodes(timecodes: list[int]) -> Framerate:
        f = Framerate()
        f.timecodes = list(timecodes)
        f._set_from_timecodes()
        return f

    def _set_from_timecodes(self) -> None:
        tc = self.timecodes
        if len(tc) <= 1:
            raise FramerateError("Must have at least two timecodes to do anything useful")
        if any(tc[i] > tc[i + 1] for i in range(len(tc) - 1)):
            raise FramerateError("Timecodes are out of order")
        if tc[0] == tc[-1]:
            raise FramerateError("Timecodes are all identical")
        front = tc[0]
        if front:
            self.timecodes = [t - front for t in tc]
        self.denominator = DEFAULT_DENOMINATOR
        self.numerator = cdiv((len(self.timecodes) - 1) * self.denominator * 1000, self.timecodes[-1])
        self.last = (len(self.timecodes) - 1) * self.denominator * 1000

    @staticmethod
    def from_file(path: Path) -> Framerate:
        text = path.read_bytes().decode("utf-8", "replace")
        lines = [ln.removesuffix("\r") for ln in text.split("\n")]
        if not lines:
            raise FramerateError("Unknown timecode file format")
        first = lines[0]
        rest = lines[1:]
        if first == "# timecode format v2":
            f = Framerate()
            for line in rest:
                v = _line_int(line)
                if v is None:
                    continue
                if len(f.timecodes) >= MAX_TIMECODES:
                    raise FramerateError("Timecode file exceeds the 10000000 entry limit")
                f.timecodes.append(v)
            f._set_from_timecodes()
            return f
        if first == "# timecode format v1" or first[:7] == "Assume ":
            if first.startswith("#"):
                if not rest:
                    raise FramerateError("Unknown timecode file format")
                first, rest = rest[0], rest[1:]
            return _v1_parse(rest, first)
        raise FramerateError(f"Unknown timecode file format: {first}")

    # -- queries

    def is_loaded(self) -> bool:
        return self.numerator > 0

    def fps(self) -> float:
        return self.numerator / self.denominator

    def frame_at_time(self, ms: int, kind: str = EXACT) -> int:
        if kind == START:
            return self.frame_at_time(ms - 1) + 1
        if kind == END:
            return self.frame_at_time(ms - 1)
        if ms < 0:
            return _i32(cdiv(cdiv(ms * self.numerator, self.denominator) - 999, 1000))
        if ms > self.timecodes[-1]:
            return _i32(
                cdiv(
                    (ms + 1) * self.numerator - self.last - cdiv(self.numerator, 2) + (1000 * self.denominator - 1),
                    1000 * self.denominator,
                )
                + len(self.timecodes)
                - 2
            )
        # distance(lower_bound(rbegin, rend, ms, greater<int>()), rend) - 1
        # == number of timecodes <= ms, minus one
        return bisect.bisect_right(self.timecodes, ms) - 1

    def time_at_frame(self, frame: int, kind: str = EXACT) -> int:
        if kind == START:
            prev = self.time_at_frame(frame - 1)
            cur = self.time_at_frame(frame)
            return _i32(prev + cdiv(cur - prev + 1, 2))
        if kind == END:
            cur = self.time_at_frame(frame)
            nxt = self.time_at_frame(frame + 1)
            return _i32(cur + cdiv(nxt - cur + 1, 2))
        if self.numerator == 0:
            return 0
        if frame < 0:
            return _i32(cdiv(frame * self.denominator * 1000, self.numerator))
        if frame >= len(self.timecodes):
            frames_past_end = frame - len(self.timecodes) + 1
            return _i32(
                cdiv(frames_past_end * 1000 * self.denominator + self.last + cdiv(self.numerator, 2), self.numerator)
            )
        return self.timecodes[frame]


_INT_PREFIX = re.compile(r"[ \t\n\v\f\r]*([+-]?\d+)")


def _line_int(line: str) -> int | None:
    """agi::line_iterator<int>: `stream >> int` (a prefix parse); lines that fail are skipped."""
    m = _INT_PREFIX.match(line)
    if not m:
        return None
    v = int(m.group(1))
    return v if -(2**31) <= v < 2**31 else None


def _v1_parse(lines: list[str], assume_line: str) -> Framerate:
    fps_string = assume_line[7:].strip(" \t\r\n\f\v")
    fps = try_parse_double(fps_string)
    if fps is None or not math.isfinite(fps) or fps <= 0:
        raise FramerateError("Assumed FPS must be greater than zero")
    if fps > 1000:
        raise FramerateError("Assumed FPS must not be greater than 1000")

    ranges: list[tuple[int, int, float]] = []
    for line in lines:
        if not line or line[0] == "#":
            continue
        parts = line.split(",")
        try:
            if len(parts) != 3:
                raise ValueError
            start, end, rfps = int(parts[0]), int(parts[1]), float(parts[2])
        except ValueError:
            raise FramerateError(f"Malformed line: {line}") from None
        if start < 0 or end < 0:
            raise FramerateError("Cannot specify frame rate for negative frames.")
        if end < start:
            raise FramerateError("End frame must be greater than or equal to start frame")
        if end > MAX_TIMECODES - 2:
            raise FramerateError(f"V1 timecode range exceeds the {MAX_TIMECODES} frame limit")
        if not math.isfinite(rfps) or rfps <= 0:
            raise FramerateError("FPS must be greater than zero")
        if rfps > 1000:
            raise FramerateError("FPS must be at most 1000")
        ranges.append((start, end, rfps))
    ranges.sort(key=lambda r: r[0])

    timecodes: list[int] = []

    def append(t: float) -> None:
        if not math.isfinite(t) or t < 0 or t > 2**31 - 1 - 0.5:
            raise FramerateError("V1 timecode exceeds the supported timestamp range")
        timecodes.append(int(t + 0.5))

    time_ms = 0.0
    frame = 0
    for start, end, rfps in ranges:
        if frame > start:
            raise FramerateError("Override ranges must not overlap")
        while frame < start:
            append(time_ms)
            time_ms += 1000.0 / fps
            frame += 1
        while frame <= end:
            append(time_ms)
            time_ms += 1000.0 / rfps
            frame += 1
    append(time_ms)
    last_time = time_ms * fps * DEFAULT_DENOMINATOR
    if not math.isfinite(last_time) or last_time >= 2.0**63:
        raise FramerateError("V1 timecode exceeds the supported timestamp range")

    f = Framerate()
    f.denominator = DEFAULT_DENOMINATOR
    f.timecodes = timecodes
    f.last = int(last_time)
    f.numerator = int(fps * DEFAULT_DENOMINATOR)
    return f
