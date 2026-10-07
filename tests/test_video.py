"""Frame rates, timecodes and keyframes (ports of libaegisub vfr.cpp / keyframe.cpp)."""

from pathlib import Path

import pytest

from aegisub_render.video.context import parse_framerate
from aegisub_render.video.keyframes import load_keyframes
from aegisub_render.video.probe import parse_dummy
from aegisub_render.video.vfr import END, START, Framerate, FramerateError


def test_cfr() -> None:
    fr = Framerate.from_fps(25.0)
    assert fr.is_loaded()
    assert fr.time_at_frame(0) == 0
    assert fr.time_at_frame(25) == 1000
    assert fr.frame_at_time(1000) == 25
    assert fr.frame_at_time(1000, START) == 25
    assert fr.frame_at_time(1001, START) == 26
    assert fr.time_at_frame(25, START) == 980
    assert fr.time_at_frame(25, END) == 1020
    assert fr.frame_at_time(-1) == -1


def test_ntsc_ratio() -> None:
    fr = parse_framerate("24000/1001")
    assert fr.numerator == 24000 and fr.denominator == 1001
    assert fr.time_at_frame(24) == 1001


def test_vfr() -> None:
    fr = Framerate.from_timecodes([0, 40, 80, 100, 120])
    assert fr.frame_at_time(85) == 2
    assert fr.frame_at_time(100) == 3
    assert fr.time_at_frame(3) == 100
    assert fr.time_at_frame(3, START) == 90
    with pytest.raises(FramerateError):
        Framerate.from_timecodes([5])


def test_timecode_files(tmp_path: Path) -> None:
    v2 = tmp_path / "v2.txt"
    v2.write_text("# timecode format v2\n0\n41.708\n83.417\n125\n", encoding="utf-8")
    fr = Framerate.from_file(v2)
    assert fr.timecodes == [0, 41, 83, 125]

    v1 = tmp_path / "v1.txt"
    v1.write_text("# timecode format v1\nAssume 25\n2,3,50\n", encoding="utf-8")
    fr = Framerate.from_file(v1)
    assert fr.timecodes == [0, 40, 80, 100, 120]  # frames 0-1 at 25 fps, 2-3 at 50 fps, then the end


def test_keyframe_files(tmp_path: Path) -> None:
    kf = tmp_path / "kf.txt"
    kf.write_text("# keyframe format v1\nfps 0\n0\n12\n40\n", encoding="utf-8")
    assert load_keyframes(kf) == [0, 12, 40]
    x264 = tmp_path / "x264.log"
    x264.write_text("#options: x\nin:0 out:0 type:I\nin:1 out:1 type:P\nin:2 out:2 type:i\n", encoding="utf-8")
    assert load_keyframes(x264) == [0, 2]


def test_dummy_spec() -> None:
    d = parse_dummy("?dummy:23.976000:40000:1920:1080:47:163:254:")
    assert (d.fps, d.frames, d.width, d.height) == ("23.976000", 40000, 1920, 1080)
    with pytest.raises(ValueError, match="Too few fields"):
        parse_dummy("?dummy:23.976:100:1:1")
