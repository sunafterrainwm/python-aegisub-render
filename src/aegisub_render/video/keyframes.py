"""Keyframe files (libaegisub/common/keyframe.cpp)."""

import re
from collections.abc import Callable
from pathlib import Path


class KeyframeFormatError(Exception):
    pass


_INT_PREFIX = re.compile(r"[ \t\n\v\f\r]*([+-]?\d+)")


def _agi_keyframes(lines: list[str]) -> list[int]:
    # file >> fps_str >> fps, then line_iterator<int> over the rest of the stream
    rest = "\n".join(lines)
    m = re.match(r"\s*\S+\s+[^\s]*", rest)
    if m:
        rest = rest[m.end() :]
    out: list[int] = []
    for line in rest.split("\n"):
        im = _INT_PREFIX.match(line)
        if im:
            out.append(int(im.group(1)))
    return out


def _enumerated(lines: list[str], func: Callable[[str], str]) -> list[int]:
    count = 0
    ret: list[int] = []
    for line in lines:
        c = func(line).lower()
        if c == "i":
            ret.append(count)
            count += 1
        elif c in ("p", "b"):
            count += 1
    return ret


def _xvid(line: str) -> str:
    return line[0] if line else ""


def _divx(line: str) -> str:
    for ch in "IPB":
        if ch in line:
            return ch
    return ""


def _x264(line: str) -> str:
    pos = line.find("type:")
    if pos < 0 or pos + 5 >= len(line):
        return ""
    return line[pos + 5]


def _wwxd(lines: list[str]) -> list[int]:
    ret: list[int] = []
    for line in lines:
        if not line or line[0] == "#":
            continue
        m = re.match(r"\s*([+-]?\d+)\s*(\S)", line)
        if not m:
            raise KeyframeFormatError("WWXD keyframe file not in qpfile format")
        if m.group(2) == "I":
            ret.append(int(m.group(1)))
    return ret


def load_keyframes(path: Path) -> list[int]:
    text = path.read_bytes().decode("utf-8", "replace")
    lines = [ln.removesuffix("\r") for ln in text.split("\n")]
    header, rest = (lines[0], lines[1:]) if lines else ("", [])
    if header == "# keyframe format v1":
        return _agi_keyframes(rest)
    if header.startswith(("# XviD 2pass stat file", "# ffmpeg 2-pass log file, using xvid codec",
                          "# avconv 2-pass log file, using xvid codec")):
        return _enumerated(rest, _xvid)
    if header.startswith("##map version"):
        return _enumerated(rest, _divx)
    if header.startswith("#options:"):
        return _enumerated(rest, _x264)
    if header.startswith("# WWXD log file, using qpfile format"):
        return _wwxd(rest)
    raise KeyframeFormatError("File header does not match any known formats")
