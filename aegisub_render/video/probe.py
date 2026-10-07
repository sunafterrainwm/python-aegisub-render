"""Video metadata the way Aegisub's FFMS2 video provider sees it (src/video_provider_ffmpegsource.cpp).

PyAV demuxes the packets; the frame list is ordered by PTS like FFMS2's index, timestamps are
`PTS * TimeBase->Num / TimeBase->Den` in milliseconds and keyframes come from the packet flags.
Results are cached in %TEMP%/aegisub-render/<sha256(absolute path)>.json.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from aegisub_render._crt import cdiv
from aegisub_render.cutil import try_parse_int


@dataclass(slots=True)
class VideoInfo:
    path: str
    size: int
    mtime_ns: int
    width: int
    height: int
    dar: float
    has_audio: bool
    timestamps: list[int]
    keyframes: list[int]


def cache_path(video: Path) -> Path:
    digest = hashlib.sha256(str(video.resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "aegisub-render" / f"{digest}.json"


def _load_cache(video: Path, st: os.stat_result) -> VideoInfo | None:
    p = cache_path(video)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        info = VideoInfo(**data)
    except OSError, ValueError, TypeError:
        return None
    if info.path != str(video.resolve()) or info.size != st.st_size or info.mtime_ns != st.st_mtime_ns:
        return None
    return info


def _store_cache(info: VideoInfo) -> None:
    p = cache_path(Path(info.path))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(info)), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _probe(video: Path, st: os.stat_result) -> VideoInfo:
    import av  # imported lazily: only needed when a real video is probed

    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        has_audio = len(container.streams.audio) > 0
        tb = stream.time_base
        if not tb:
            raise ValueError("video stream has no time base")
        num, den = tb.numerator * 1000, tb.denominator
        pts_list: list[int] = []
        key_flags: list[bool] = []
        for packet in container.demux(stream):
            if packet.size == 0:
                continue
            pts = packet.pts if packet.pts is not None else packet.dts
            if pts is None:
                continue
            pts_list.append(int(pts))
            key_flags.append(bool(packet.is_keyframe))
        cc = stream.codec_context
        width, height = int(cc.width), int(cc.height)
        sar = stream.sample_aspect_ratio
        if sar and sar.numerator > 0 and sar.denominator > 0 and height > 0:
            dar = width * sar.numerator / (height * sar.denominator)
        else:
            dar = width / height if height else 1.0

    order = sorted(range(len(pts_list)), key=lambda i: pts_list[i])
    timestamps = [int(cdiv(pts_list[i] * num, den)) for i in order]
    keyframes = [n for n, i in enumerate(order) if key_flags[i]]
    return VideoInfo(
        str(video.resolve()), st.st_size, st.st_mtime_ns, width, height, dar, has_audio, timestamps, keyframes
    )


def probe_video(video: Path, use_cache: bool = True, refresh: bool = False) -> VideoInfo:
    st = video.stat()
    if use_cache and not refresh:
        cached = _load_cache(video, st)
        if cached is not None:
            return cached
    info = _probe(video, st)
    if use_cache:
        _store_cache(info)
    return info


@dataclass(slots=True)
class DummySpec:
    fps: str
    frames: int
    width: int
    height: int


def parse_dummy(spec: str) -> DummySpec:
    """CreateDummyVideoProvider: ?dummy:fps:frames:width:height:r:g:b:pattern."""
    if not spec.replace("\\", "/").startswith("?dummy"):
        raise ValueError("not a dummy video")
    toks = spec.replace("\\", "/")[7:].split(":")
    if len(toks) != 8:
        raise ValueError("Too few fields in dummy video parameter list")
    values: list[int] = []
    names = ("framecount", "width", "height", "red colour", "green colour", "blue colour")
    for name, tok in zip(names, toks[1:7], strict=True):
        v = try_parse_int(tok)
        if v is None:
            raise ValueError(f"Unable to parse {name} field in dummy video parameter list")
        values.append(v)
    frames, width, height = values[0], values[1], values[2]
    if frames <= 0:
        raise ValueError("Dummy video frame count must be positive")
    if width <= 0 or height <= 0:
        raise ValueError("Dummy video resolution must be positive")
    return DummySpec(toks[0], frames, width, height)
