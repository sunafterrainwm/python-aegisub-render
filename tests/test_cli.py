"""End-to-end runs through the CLI and the worker process."""

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from aegisub_render.settings import Settings
from aegisub_render.video.probe import cache_path

from .conftest import MINIMAL_ASS

TEMPLATE_ASS = MINIMAL_ASS.replace(
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,template syl,{\\pos($scenter,$smiddle)\\an5}\n",
)


ROOT = Path(__file__).resolve().parent.parent


def cli(*args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "aegisub-render.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8-sig")
    return path


def script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "s.lua"
    p.write_text(
        textwrap.dedent(
            """
            script_name = "s"
            aegisub.register_macro("m", "", function(subs)
            %s
            end)
            """
        )
        % body,
        encoding="utf-8",
    )
    return p


def test_render_kara_templater(tmp_path: Path, settings: Settings) -> None:
    src = write(tmp_path / "in.ass", TEMPLATE_ASS)
    out = tmp_path / "out.ass"
    r = cli("render", str(src), "-o", str(out))
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8-sig")
    # line 1: syllable 0 + "first " + "line"; line 2 (no \k tags): syllable 0 + the whole text
    assert text.count(",fx,") == 5
    assert "Default-furigana" in text


def test_error_writes_nothing(tmp_path: Path, settings: Settings) -> None:
    src = write(tmp_path / "in.ass", MINIMAL_ASS)
    out = tmp_path / "out.ass"
    r = cli("render", str(src), "-o", str(out), "--script", str(script(tmp_path, "error('kaboom')")), "--macro", "m")
    assert r.returncode == 1
    assert "kaboom" in r.stderr and "Lua reported a runtime error" in r.stderr
    assert not out.exists()

    r = cli("render", str(src), "-o", str(out), "--script", str(script(tmp_path, "aegisub.cancel()")), "--macro", "m")
    assert r.returncode == 2
    assert not out.exists()


def test_timeout_kills_the_worker(tmp_path: Path, settings: Settings) -> None:
    src = write(tmp_path / "in.ass", MINIMAL_ASS)
    out = tmp_path / "out.ass"
    loop = script(tmp_path, "local x = 0 while true do x = x + 1 end")
    r = cli("render", str(src), "-o", str(out), "--script", str(loop), "--macro", "m", "--timeout", "3")
    assert r.returncode == 1
    assert "longer than 3 seconds" in r.stderr
    assert not out.exists()


def test_unknown_macro_lists_available(tmp_path: Path, settings: Settings) -> None:
    src = write(tmp_path / "in.ass", MINIMAL_ASS)
    r = cli("render", str(src), "-o", str(tmp_path / "o.ass"), "--macro", "nope")
    assert r.returncode == 1
    assert "'Apply karaoke template'" in r.stderr


def test_list(tmp_path: Path, settings: Settings) -> None:
    src = write(tmp_path / "in.ass", MINIMAL_ASS)
    r = cli("list", str(src))
    assert r.returncode == 0, r.stderr
    assert "Apply karaoke template" in r.stdout and "Karaoke template" in r.stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed to make a test video")
def test_real_video_probe_and_cache(tmp_path: Path, settings: Settings) -> None:
    video = tmp_path / "clip.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=24000/1001",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-g",
            "12",
            "-keyint_min",
            "12",
            "-sc_threshold",
            "0",
            "-bf",
            "2",
            "-an",
            str(video),
        ],
        check=True,
        timeout=60,
    )
    ass = MINIMAL_ASS.replace("[V4+ Styles]", "[Aegisub Project Garbage]\nVideo File: clip.mkv\n\n[V4+ Styles]")
    src = write(tmp_path / "in.ass", ass)
    body = """
        local w, h, ar, art = aegisub.video_size()
        assert(w == 320 and h == 240 and art == 0, "size")
        local kf = aegisub.keyframes()
        assert(kf[1] == 0 and kf[2] == 12, "keyframes " .. tostring(kf[2]))
        assert(aegisub.ms_from_frame(1) == 21, "ms_from_frame " .. tostring(aegisub.ms_from_frame(1)))
        assert(aegisub.frame_from_ms(1000) == 24, "frame_from_ms " .. tostring(aegisub.frame_from_ms(1000)))
    """
    s = script(tmp_path, body)
    cache_path(video).unlink(missing_ok=True)
    out = tmp_path / "out.ass"
    r = cli("render", str(src), "-o", str(out), "--script", str(s), "--macro", "m")
    assert r.returncode == 0, r.stderr
    cached = json.loads(cache_path(video).read_text(encoding="utf-8"))
    assert cached["width"] == 320 and cached["keyframes"][:2] == [0, 12]
    text = out.read_text(encoding="utf-8-sig")
    assert "Video File: clip.mkv" in text
    assert "Video AR Value: 1.333333" in text
    # a second run is served from the cache
    r = cli("render", str(src), "-o", str(out), "--script", str(s), "--macro", "m")
    assert r.returncode == 0, r.stderr
