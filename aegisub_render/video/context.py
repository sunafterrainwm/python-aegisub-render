"""What Aegisub would have loaded alongside the subtitles (src/project.cpp LoadUnloadFiles).

Emulates "App/Auto/Load Linked Files" answering yes: every linked file that exists is loaded,
in Aegisub's order (video, then timecodes, then keyframes, then audio), and the project
properties are rewritten the way Project::UpdateRelativePaths and VideoController do it,
except that links which could not be loaded are kept instead of dropped.
"""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath

from aegisub_render.ass.file import AssFile
from aegisub_render.cutil import mid, try_parse_double, try_parse_int
from aegisub_render.paths import AegisubPaths, is_dummy_path, make_preferred
from aegisub_render.video.keyframes import KeyframeFormatError, load_keyframes
from aegisub_render.video.probe import parse_dummy, probe_video
from aegisub_render.video.vfr import Framerate, FramerateError
from aegisub_render.wsl import translate


@dataclass(slots=True)
class VideoOptions:
    video: str | None = None  # overrides the project's Video File
    no_video: bool = False
    video_size: tuple[int, int] | None = None
    fps: str | None = None
    timecodes: str | None = None
    keyframes: str | None = None
    use_cache: bool = True
    refresh_cache: bool = False
    open_audio: bool = True  # Aegisub option Video/Open Audio


@dataclass(slots=True)
class VideoContext:
    loaded: bool = False
    width: int = 0
    height: int = 0
    ar_value: float = 1.0
    ar_type: int = 0
    timecodes: Framerate = field(default_factory=Framerate)
    keyframes: list[int] = field(default_factory=list[int])
    warnings: list[str] = field(default_factory=list[str])


def parse_framerate(text: str) -> Framerate:
    """DummyVideoProvider::TryParseFramerate (also used for --fps)."""
    d = try_parse_double(text)
    if d is not None:
        return Framerate.from_fps(d)
    parts = text.split("/")
    if len(parts) != 2:
        raise FramerateError(f"cannot parse frame rate: {text}")
    num, den = try_parse_int(parts[0]), try_parse_int(parts[1])
    if num is None or den is None:
        raise FramerateError(f"cannot parse frame rate: {text}")
    return Framerate.from_ratio(num, den)


def _generic_relative(path: str, base: str) -> str:
    """Path::MakeRelative(path, "?script").generic_string()."""
    if not path or not base:
        return path.replace("\\", "/")
    if is_dummy_path(path):
        return path.replace("\\", "/")
    if PureWindowsPath(path).drive and not PureWindowsPath(base).drive:
        return path.replace("\\", "/")  # a Windows path kept outside Windows (under WSL): another drive
    p, b = PurePath(path), PurePath(base)
    if p.drive and p.drive.lower() != b.drive.lower():
        return str(p).replace("\\", "/")
    pp, bp = list(p.parts), list(b.parts)
    i = 0
    while i < len(pp) and i < len(bp) and pp[i] == bp[i]:
        i += 1
    rel = [".."] * (len(bp) - i) + pp[i:]
    return "/".join(rel)


def build_video_context(
    ass: AssFile, paths: AegisubPaths, opts: VideoOptions, save_dir: str | None = None
) -> VideoContext:
    """`save_dir` is the directory the result will be saved in (default: the script's, ?script)."""
    ctx = VideoContext()
    props = ass.properties
    script_dir = paths.get("?script")

    def absolute(p: str) -> str:
        return paths.make_absolute(translate(p), "?script")  # under WSL, X:\\... is /mnt/x/...

    def exists(p: str) -> bool:
        return bool(p) and Path(p).exists()

    video = "" if opts.no_video else absolute(opts.video if opts.video is not None else props.video_file)
    timecodes = absolute(opts.timecodes if opts.timecodes is not None else props.timecodes_file)
    keyframes = absolute(opts.keyframes if opts.keyframes is not None else props.keyframes_file)
    audio = absolute(props.audio_file)

    if video and not exists(video) and not is_dummy_path(video):
        ctx.warnings.append(f"aegisub-render: linked video not found, continuing without video: {video}\n")
        video = ""
    if timecodes and not exists(timecodes):
        ctx.warnings.append(f"aegisub-render: linked timecodes file not found: {timecodes}\n")
        timecodes = ""
    if keyframes and not exists(keyframes):
        ctx.warnings.append(f"aegisub-render: linked keyframes file not found: {keyframes}\n")
        keyframes = ""
    if audio and not exists(audio) and not is_dummy_path(audio):
        audio = ""

    loaded_video = ""
    has_audio = False
    frame_count = 1
    if video:
        try:
            if is_dummy_path(video):
                spec = parse_dummy(video)
                ctx.width, ctx.height = spec.width, spec.height
                ctx.timecodes = parse_framerate(spec.fps)
                ctx.keyframes = []
                frame_count = spec.frames
            else:
                info = probe_video(Path(video), opts.use_cache, opts.refresh_cache)
                ctx.width, ctx.height = info.width, info.height
                has_audio = info.has_audio
                if len(info.timestamps) < 2:
                    ctx.timecodes = Framerate.from_fps(25.0)
                else:
                    ctx.timecodes = Framerate.from_timecodes(info.timestamps)
                ctx.keyframes = list(info.keyframes)
                frame_count = max(1, len(info.timestamps))
            ctx.loaded = True
            loaded_video = make_preferred(video)
            paths.set_token("?video", loaded_video if not is_dummy_path(video) else "")
        except (OSError, ValueError, FramerateError) as e:
            ctx.warnings.append(f"aegisub-render: could not open video {video}: {e}\n")

    if ctx.loaded:
        # Project::LoadUnloadFiles after DoLoadVideo: restore the aspect ratio mode
        default_ar = ctx.width / ctx.height if ctx.height else 1.0
        fixed = {0: default_ar, 1: 4.0 / 3.0, 2: 16.0 / 9.0, 3: 2.35}
        if props.ar_mode in fixed:
            ctx.ar_type, ctx.ar_value = props.ar_mode, mid(0.5, fixed[props.ar_mode], 5.0)
        elif props.ar_mode == 4 and math.isfinite(props.ar_value):
            ctx.ar_type, ctx.ar_value = 4, mid(0.5, props.ar_value, 5.0)
        else:
            ctx.ar_type, ctx.ar_value = 0, mid(0.5, default_ar, 5.0)
        props.ar_mode, props.ar_value = ctx.ar_type, ctx.ar_value
        # VideoController::JumpToFrame clamps the stored position to the video
        props.video_position = max(0, min(props.video_position, frame_count - 1))

    loaded_timecodes = ""
    if opts.fps is not None:
        try:
            ctx.timecodes = parse_framerate(opts.fps)
        except FramerateError as e:
            ctx.warnings.append(f"aegisub-render: bad --fps: {e}\n")
    elif timecodes:
        try:
            ctx.timecodes = Framerate.from_file(Path(timecodes))
            loaded_timecodes = make_preferred(timecodes)
        except (OSError, FramerateError) as e:
            ctx.warnings.append(f"aegisub-render: failed to parse timecodes file {timecodes}: {e}\n")

    loaded_keyframes = ""
    if keyframes:
        try:
            ctx.keyframes = load_keyframes(Path(keyframes))
            loaded_keyframes = make_preferred(keyframes)
        except (OSError, KeyframeFormatError) as e:
            ctx.warnings.append(f"aegisub-render: failed to parse keyframes file {keyframes}: {e}\n")

    loaded_audio = make_preferred(audio) if audio else ""
    if not loaded_audio and loaded_video and opts.open_audio and has_audio:
        loaded_audio = loaded_video
    if loaded_audio:
        paths.set_token("?audio", loaded_audio if not is_dummy_path(loaded_audio) else "")

    if opts.video_size is not None:
        ctx.loaded = True
        ctx.width, ctx.height = opts.video_size
        if ctx.ar_type == 0:
            ctx.ar_value = mid(0.5, ctx.width / ctx.height, 5.0)

    # Project::SetSubtitlesFilename -> UpdateRelativePaths (on save, relative to where it is saved)
    base = save_dir if save_dir is not None else script_dir
    same_dir = os.path.normcase(base) == os.path.normcase(script_dir)

    def saved_path(loaded: str, linked: str) -> str:
        """Aegisub writes what is loaded, so a link it could not load is dropped. Here such a link
        (a missing file, --no-video, --fps) is kept: verbatim, or rebased when saved elsewhere."""
        if loaded:
            return _generic_relative(loaded, base)
        if not linked or same_dir or is_dummy_path(linked):
            return linked
        # a Windows drive path stays one (it is only translated to look for the file under WSL)
        return _generic_relative(linked if PureWindowsPath(linked).drive else absolute(linked), base)

    props.audio_file = saved_path(loaded_audio, props.audio_file)
    props.video_file = saved_path(loaded_video, props.video_file)
    props.timecodes_file = saved_path(loaded_timecodes, props.timecodes_file)
    props.keyframes_file = saved_path(loaded_keyframes, props.keyframes_file)
    return ctx
