"""The file-level entry points: render a subtitle file, or list a script's features.

Everything here is about files and exit codes. Running the script itself is ScriptRender's job
(script_render.py), and choosing between Save and Export is the caller's: this module is the
caller the CLI uses.
"""

import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Final, Literal

from aegisub_render.ass.entries import SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.parser import UnsupportedEncodingError, read_ass_file
from aegisub_render.ass.writer import serialize_ass, serialize_export, write_ass_file
from aegisub_render.diff import text_lines, unified_diff
from aegisub_render.output import Output
from aegisub_render.script_render import (
    RenderError,
    ScriptCancelled,
    ScriptError,
    ScriptFailed,
    ScriptRefused,
    ScriptRender,
    resolve_script,
)
from aegisub_render.settings import Settings
from aegisub_render.video.context import VideoOptions, update_relative_paths
from aegisub_render.worker import FeatureList

STDOUT: Final = "-"  # render() / offset() / measure() output path meaning standard output

__all__ = [
    "STDOUT",
    "ExitCode",
    "RenderError",
    "RenderOptions",
    "ScriptCancelled",
    "ScriptError",
    "ScriptFailed",
    "ScriptRefused",
    "ScriptRender",
    "list_features",
    "output_data",
    "read_input_ass",
    "render",
    "resolve_script",
    "script_render",
    "write_output",
]


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    CANCELLED = 2


@dataclass(slots=True)
class RenderOptions:
    script: str | None = None
    macro: str | None = None
    filter: str | None = None
    selection: list[int] | None = None
    force: bool = False
    video: VideoOptions = field(default_factory=VideoOptions)
    trace_level: int | None = None
    quiet: bool = False
    progress: bool | None = None
    dry_run: bool = False  # write nothing; print how the result differs from the input instead
    keep_brand: bool = False  # write the input's header comments unchanged (see writer.header_lines)


def read_input_ass(input_path: Path, max_input_lines: int) -> AssFile:
    try:
        ass = read_ass_file(input_path)
    except (UnsupportedEncodingError, SubtitleFormatParseError) as e:
        raise RenderError(f"could not read {input_path}: {e}") from None
    if ass.line_count() > max_input_lines:
        raise RenderError(
            f"{input_path} has {ass.line_count()} lines, more than the limit of {max_input_lines} (MAX_INPUT_LINES)"
        )
    return ass


def script_render(
    ass: AssFile,
    input_path: Path,
    settings: Settings,
    options: RenderOptions,
    out: Output,
    isolate: bool = True,
) -> ScriptRender:
    """A ScriptRender for the CLI's options, on a file read from `input_path`."""
    return ScriptRender(
        ass,
        options.script,
        settings,
        subtitle_path=input_path,
        isolate=isolate,
        macro=options.macro,
        filter=options.filter,
        selection=options.selection,
        force=options.force,
        video=options.video,
        trace_level=options.trace_level,
        quiet=options.quiet,
        progress=options.progress,
        out=out,
    )


def output_data(ass: AssFile, is_filter: bool, save_ui_state: bool, keep_brand: bool) -> bytes:
    """What Aegisub writes after the script ran. Export filters only run in File > Export, which
    writes with ExportFile; a macro is followed by File > Save."""
    if is_filter:
        return serialize_export(ass, save_ui_state, keep_brand)
    return serialize_ass(ass, save_ui_state, keep_brand)


def write_output(
    data: bytes,
    original: bytes,
    input_path: Path,
    output_path: Path | Literal["-"] | None,
    save_to: Path,
    dry_run: bool,
    out: Output,
) -> ExitCode:
    if dry_run:
        new_name = str(output_path if output_path is not None else input_path)
        out.diff(unified_diff(text_lines(original), text_lines(data), str(input_path), new_name))
        return ExitCode.OK
    if output_path == STDOUT:
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return ExitCode.OK
    try:
        write_ass_file(data, save_to)
    except OSError as e:
        out.error(f"aegisub-render: could not write {save_to}: {e}")
        return ExitCode.ERROR
    return ExitCode.OK


def _report(e: ScriptError, out: Output) -> ExitCode:
    """The script's own message, then the line that says nothing was written."""
    if e.message:
        out.error(e.message)
    if isinstance(e, ScriptRefused):
        return ExitCode.ERROR  # the message says why
    if isinstance(e, ScriptCancelled):
        out.error("aegisub-render: the script cancelled the operation; no output was written")
        return ExitCode.CANCELLED
    out.error("aegisub-render: the script failed; no output was written")
    return ExitCode.ERROR


def render(
    input_path: Path,
    output_path: Path | Literal["-"] | None,
    settings: Settings,
    options: RenderOptions | None = None,
    out: Output | None = None,
) -> ExitCode:
    """Run the script on `input_path` and save the result to `output_path`: a file, STDOUT, or None
    to replace the input. With `options.dry_run` nothing is written; how the result differs from
    the input is printed instead."""
    options = options or RenderOptions()
    out = out or Output(quiet=options.quiet, progress=options.progress)
    save_to = output_path if isinstance(output_path, Path) else input_path
    try:
        ass = read_input_ass(input_path, settings.max_input_lines)
        original = input_path.read_bytes() if options.dry_run else b""
        runner = script_render(ass, input_path, settings, options, out)
        # The linked paths are written relative to where the result goes, which is this layer's
        # business (Aegisub does it when it saves, Project::SetSubtitlesFilename). It has to
        # happen before the worker starts: that is when the script gets its copy of the file.
        update_relative_paths(ass, runner.video, runner.paths, str(save_to.resolve().parent))
    except (RenderError, OSError) as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR
    for w in runner.warnings:
        out.message(w)
    with runner:
        try:
            runner.run()
        except ScriptError as e:
            return _report(e, out)
        except RenderError as e:
            out.error(f"aegisub-render: {e}")
            return ExitCode.ERROR
        data = output_data(ass, runner.is_filter, runner.save_ui_state, options.keep_brand)
    return write_output(data, original, input_path, output_path, save_to, options.dry_run, out)


def list_features(
    input_path: Path, settings: Settings, options: RenderOptions | None = None, out: Output | None = None
) -> FeatureList | None:
    options = options or RenderOptions()
    out = out or Output(quiet=options.quiet, progress=False)
    try:
        ass = read_input_ass(input_path, settings.max_input_lines)
        runner = script_render(ass, input_path, settings, options, out)
        update_relative_paths(ass, runner.video, runner.paths)  # as saving in place would write them
    except (RenderError, OSError) as e:
        out.error(f"aegisub-render: {e}")
        return None
    for w in runner.warnings:
        out.message(w)
    with runner:
        try:
            return runner.features()
        except ScriptError as e:
            if e.message:
                out.error(e.message)
            return None
        except RenderError as e:
            out.error(f"aegisub-render: {e}")
            return None
