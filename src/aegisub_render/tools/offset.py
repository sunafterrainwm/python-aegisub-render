"""Shift subtitle dialogue times by a timestamp or frame count without starting Aegisub.

Two ways in: the `macro offset` subcommand, which reads and writes the file itself, and the
built-in `Shift Times` export filter, which does the same to a file someone else is holding
(`--export-filters "Shift Times=+0:00:01.50"`).
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self, override

from aegisub_render.ass.file import AssFile
from aegisub_render.ass.time import clamp_time
from aegisub_render.ass.writer import serialize_ass
from aegisub_render.automation import (
    AutomationContext,
    AutomationError,
    ExportFilter,
    FilterArgs,
    register_filter,
)
from aegisub_render.cli import (
    add_base_args,
    add_output_args,
    add_video_args,
    build_render_options,
    may_replace,
)
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths
from aegisub_render.runner import ExitCode, RenderError, RenderOptions, read_input_ass, write_output
from aegisub_render.settings import Settings, read_aegisub_options
from aegisub_render.tools import register_command
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


def shift_events(file: AssFile, shift: Offset, timecodes: Framerate | None = None) -> None:
    """Shift every dialogue line of `file` by `shift`, in place.

    A frame offset needs `timecodes` (the video's, --timecodes or --fps); a time offset does not.
    Times are clamped to the range agi::Time can hold, as everywhere else.
    """
    if shift.by_frames:
        if timecodes is None or not timecodes.is_loaded():
            raise AutomationError("cannot shift by frames without a video, timecodes, or --fps")
        for line in file.events:
            line.start = shift_time_by_frames(line.start, shift.amount, timecodes)
            line.end = shift_time_by_frames(line.end, shift.amount, timecodes)
    else:
        for line in file.events:
            line.start = clamp_time(line.start + shift.amount)
            line.end = clamp_time(line.end + shift.amount)


class ShiftTimesFilter(ExportFilter):
    """`macro offset` as an export filter, so it can be part of an --export-filters chain.

    Not one of Aegisub's: it is the Timing > Shift Times dialog, which Aegisub has no export
    filter for. `by` is what `macro offset` takes as its argument.
    """

    name: ClassVar[str] = "Shift Times"
    description: ClassVar[str] = (
        "Shift every line by a timestamp ([+-][[[hh:]mm:]ss].ms) or a frame count ([+-]frames)."
    )
    priority: ClassVar[int] = 0
    positional: ClassVar[tuple[str, ...]] = ("by",)

    def __init__(self, by: str = "") -> None:
        if not by:
            raise AutomationError(
                f"{self.name}: needs the amount to shift by, e.g. "
                f"'{self.name}=+0:00:01.50' or '{self.name}=-24' (frames)"
            )
        try:
            self.offset = parse_offset(by)
        except ValueError as e:
            raise AutomationError(f"{self.name}: {e}") from None

    @classmethod
    @override
    def from_args(cls, args: FilterArgs) -> Self:
        """Like the base class, except that a timestamp's own colons need no escaping.

        `Shift Times=+0:00:01.50` would otherwise be three positional options, `:` being what
        separates them. This filter takes exactly one, so they are joined back together;
        quoting it (`Shift Times='+0:00:01.50'`) works as well, as it does anywhere.
        """
        if len(args.positional) > 1 and "by" not in args.options:
            args = FilterArgs(args.name, [":".join(args.positional)], dict(args.options))
        return super().from_args(args)

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        shift_events(file, self.offset, ctx.fps)


register_filter(ShiftTimesFilter)


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

    try:
        shift_events(ass, shift, video.timecodes)
    except AutomationError as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR

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


__all__ = [
    "Offset",
    "ShiftTimesFilter",
    "offset",
    "parse_offset",
    "parse_offset_arg",
    "shift_events",
    "shift_time_by_frames",
]
