"""Shift subtitle dialogue times by a timestamp or frame count without starting Aegisub."""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aegisub_render.ass.time import clamp_time
from aegisub_render.ass.writer import serialize_ass
from aegisub_render.cli import (
    add_base_args,
    add_output_args,
    add_video_args,
    build_render_options,
    may_replace,
)
from aegisub_render.macro import register_command
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths
from aegisub_render.runner import ExitCode, RenderError, RenderOptions, read_input_ass, write_output
from aegisub_render.settings import Settings, read_aegisub_options
from aegisub_render.video.context import build_video_context, update_relative_paths
from aegisub_render.video.vfr import START, Framerate

_TIME_OFFSET_RE = re.compile(r"^([+-])?(?:(?:(?:(\d+):)?(\d+):)?(\d+))?\.(\d+)$")
_FRAME_OFFSET_RE = re.compile(r"^([+-])?(\d+)$")


@dataclass(slots=True, frozen=True)
class Offset:
    amount: int  # milliseconds when by_frames is False, frame count when by_frames is True
    by_frames: bool = False


def parse_offset(spec: str) -> Offset:
    """Parse a timestamp offset (`[+-][[[hh:]mm:]ss].ms`) or frame offset (`[+-]frames`)."""
    m = _TIME_OFFSET_RE.fullmatch(spec)
    if m:
        sign, hh, mm, ss, frac = m.groups()
        total_s = (int(hh or 0) * 60 + int(mm or 0)) * 60 + int(ss or 0)
        ms = total_s * 1000 + int(frac[:3].ljust(3, "0"))
        return Offset(-ms if sign == "-" else ms, by_frames=False)
    m = _FRAME_OFFSET_RE.fullmatch(spec)
    if m:
        return Offset(int(spec), by_frames=True)
    raise ValueError(f"bad offset {spec!r} (expected [+-][[[hh:]mm:]ss].ms or [+-]frames)")


def parse_offset_arg(spec: str) -> Offset:
    try:
        return parse_offset(spec)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def shift_time_by_frames(ms: int, delta_frames: int, timecodes: Framerate) -> int:
    """Shift `ms` by `delta_frames`: new frame `i = frame_at_time(ms, START) + delta_frames`,
    mapped back to `0:00:00.00` when `i <= 0` and `(time_of_frame(i - 1) + time_of_frame(i)) / 2`
    (`TimeAtFrame(i, START)`) otherwise."""
    i = timecodes.frame_at_time(ms, START) + delta_frames
    if i <= 0:
        return 0
    return clamp_time(timecodes.time_at_frame(i, START))


def offset(
    input_path: Path,
    output_path: Path | Literal["-"] | None,
    shift: Offset | str,
    settings: Settings | None = None,
    options: RenderOptions | None = None,
    out: Output | None = None,
) -> ExitCode:
    """Shift all dialogue lines in `input_path` by `shift` (a timestamp or frame count) without
    starting Aegisub or the Lua worker process, and save the result to `output_path`."""
    settings = settings or Settings()
    options = options or RenderOptions()
    out = out or Output(quiet=options.quiet, progress=False)
    if isinstance(shift, str):
        try:
            shift = parse_offset(shift)
        except ValueError as e:
            out.error(f"aegisub-render: {e}")
            return ExitCode.ERROR
    save_to = output_path if isinstance(output_path, Path) else input_path
    try:
        paths = AegisubPaths.create(settings.aegisub_root, settings.load_appdata)
        resolved_input = input_path.resolve()
        paths.set_token("?script", str(resolved_input.parent))
        aeg = read_aegisub_options(paths)
        ass = read_input_ass(resolved_input, settings.max_input_lines)
        original = input_path.read_bytes() if options.dry_run else b""
    except (RenderError, OSError) as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR

    vopts = options.video
    vopts.open_audio = aeg.open_audio
    video = build_video_context(ass, paths, vopts)
    for w in video.warnings:
        out.message(w)
    # the linked paths are written relative to where this is saved (Aegisub does it on save)
    update_relative_paths(ass, video, paths, str(save_to.resolve().parent))

    if shift.by_frames:
        if not video.timecodes.is_loaded():
            out.error("aegisub-render: cannot shift by frames without a video, timecodes, or --fps")
            return ExitCode.ERROR
        for line in ass.events:
            line.start = shift_time_by_frames(line.start, shift.amount, video.timecodes)
            line.end = shift_time_by_frames(line.end, shift.amount, video.timecodes)
    else:
        for line in ass.events:
            line.start = clamp_time(line.start + shift.amount)
            line.end = clamp_time(line.end + shift.amount)

    data = serialize_ass(ass, aeg.save_ui_state, options.keep_brand)
    return write_output(data, original, input_path, output_path, save_to, options.dry_run, out)


def _configure_offset(p: argparse.ArgumentParser) -> None:
    add_base_args(p)
    p.add_argument(
        "offset",
        type=parse_offset_arg,
        help="time ([+-][[[hh:]mm:]ss].ms) or frame ([+-]frames) offset",
    )
    p.add_argument("--max-input-lines", type=int, help="limit on lines in the input file")
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    add_output_args(p)
    add_video_args(p)


@register_command(
    "offset",
    help="shift subtitle times by a timestamp or frame count without starting Aegisub",
    configure=_configure_offset,
)
def _run_offset(args: argparse.Namespace, settings: Settings) -> ExitCode:
    options = build_render_options(args, settings)
    out = Output(quiet=options.quiet, progress=options.progress)
    output: Path | Literal["-"] | None = args.output
    if isinstance(output, Path) and not options.dry_run and output.exists() and not may_replace(output, args.override):
        return ExitCode.ERROR
    return offset(args.input, output, args.offset, settings, options, out)


__all__ = ["Offset", "offset", "parse_offset", "parse_offset_arg", "shift_time_by_frames"]
