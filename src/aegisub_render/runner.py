"""The file-level entry points: render a subtitle file, or list a script's features.

Everything here is about files and exit codes. Running the script itself is ScriptRender's job
(script_render.py), and choosing between Save and Export is the caller's: this module is the
caller the CLI uses.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Final, Literal

from aegisub_render.ass.entries import SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats import AssSubtitleFormat, serialize_subtitles
from aegisub_render.ass.parser import UnsupportedEncodingError, read_ass_file
from aegisub_render.ass.writer import serialize_ass, serialize_export, write_ass_file
from aegisub_render.automation import (
    AutomationContext,
    AutomationError,
    FilterArgs,
    FilterSpecError,
    parse_filter_chain,
)
from aegisub_render.automation.resolve import FilterResolver, ResolvedFilter
from aegisub_render.diff import text_lines, unified_diff
from aegisub_render.output import Output
from aegisub_render.script_render import (
    RenderEnvironment,
    RenderError,
    ScriptCancelled,
    ScriptError,
    ScriptFailed,
    ScriptRefused,
    ScriptRender,
    build_environment,
    resolve_script,
)
from aegisub_render.settings import Settings
from aegisub_render.video.context import VideoOptions, update_relative_paths
from aegisub_render.video.vfr import Framerate
from aegisub_render.worker import FeatureList

STDOUT: Final = "-"  # render() / offset() / measure() output path meaning standard output

__all__ = [
    "STDOUT",
    "AutomationError",
    "ExitCode",
    "RenderError",
    "RenderOptions",
    "ScriptCancelled",
    "ScriptError",
    "ScriptFailed",
    "ScriptRefused",
    "ScriptRender",
    "export_filter_chain",
    "garbage_export_filter_names",
    "list_features",
    "output_data",
    "read_input_ass",
    "render",
    "resolve_script",
    "run_export_filters",
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
    # the --export-filters chain, in ffmpeg's filtergraph syntax; several are joined with ','
    export_filters: list[str] = field(default_factory=list[str])
    # run the file's own [Aegisub Project Garbage] "Export Filters:" list; None asks
    # confirm_export_filters, and without that answer does not run them
    garbage_export_filters: bool | None = None
    confirm_export_filters: Callable[[list[str]], bool] | None = None
    scan_autoload: bool | None = None  # look through the autoload scripts for a filter name
    selection: list[int] | None = None
    force: bool = False
    video: VideoOptions = field(default_factory=VideoOptions)
    trace_level: int | None = None
    quiet: bool = False
    progress: bool | None = None
    tags: str | None = None  # TagMode for a non-ASS --output (see ass.formats.base.TagMode)
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
    env: RenderEnvironment | None = None,
    filter: str | None = None,
) -> ScriptRender:
    """A ScriptRender for the CLI's options, on a file read from `input_path`.

    `filter` runs one export filter of the script instead of a macro, as ScriptRender's own
    parameter does; the CLI does not use it (it has --export-filters, which also takes the
    built-in filters and more than one), but the byte-for-byte tests do.
    """
    return ScriptRender(
        ass,
        options.script,
        settings,
        subtitle_path=input_path,
        isolate=isolate,
        macro=options.macro,
        filter=filter,
        selection=options.selection,
        force=options.force,
        video=options.video,
        trace_level=options.trace_level,
        quiet=options.quiet,
        progress=options.progress,
        out=out,
        env=env,
    )


def output_data(
    ass: AssFile,
    is_filter: bool,
    save_ui_state: bool,
    keep_brand: bool,
    name: Path | None = None,
    tags: str | None = None,
    fps: Framerate | None = None,
) -> bytes:
    """What Aegisub writes after the script ran. Export filters only run in File > Export, which
    writes with ExportFile; a macro is followed by File > Save.

    `name` is where the result is going: an extension another format claims (.srt, .vtt, .ttxt,
    .txt, .transtation.txt) writes that format instead, with `tags` and `fps` for it. Aegisub
    does the same -- SubtitleFormat::GetWriter picks the format from the file name -- but it
    cannot run a macro and save as .srt in one step, so the combination is this tool's.
    """
    if name is not None and not AssSubtitleFormat.can_write_file(name):
        return serialize_subtitles(
            ass, name, export=is_filter, tags=tags, fps=fps, save_ui_state=save_ui_state, keep_brand=keep_brand
        )
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


def garbage_export_filter_names(ass: AssFile) -> list[str]:
    """The `Export Filters:` list of [Aegisub Project Garbage]: what Aegisub last had ticked.

    Aegisub's export dialog writes the names it ran, separated by `|`, and ticks them again the
    next time it is opened. They are plain names, not a filtergraph, so nothing in them is
    escaped and nothing here parses them.
    """
    return [name for name in ass.properties.export_filters.split("|") if name]


def export_filter_chain(options: RenderOptions, ass: AssFile) -> list[FilterArgs]:
    """The filters to run: --export-filters, or the file's own list if that is wanted.

    With neither, nothing runs. `options.garbage_export_filters` is True/False to decide
    outright, or None to leave it to `options.confirm_export_filters` (the CLI asks on a
    terminal); without such a callback an undecided None does not run them.
    """
    if options.export_filters:
        return parse_filter_chain(",".join(options.export_filters))
    if options.garbage_export_filters is False:
        return []
    names = garbage_export_filter_names(ass)
    if not names:
        return []
    wanted = options.garbage_export_filters
    if wanted is None:
        wanted = options.confirm_export_filters(names) if options.confirm_export_filters is not None else False
    return [FilterArgs(name) for name in names] if wanted else []


def resolve_export_filters(
    options: RenderOptions,
    settings: Settings,
    env: RenderEnvironment,
    out: Output,
    ass: AssFile,
) -> list[ResolvedFilter]:
    """Work out what to run (export_filter_chain) and who runs it (see automation.resolve)."""
    chain = export_filter_chain(options, ass)
    if not chain:
        return []
    script: Path | None = None
    if options.script is not None:
        script = resolve_script(options.script, env.paths, env.autoload)
    scan = options.scan_autoload if options.scan_autoload is not None else settings.scan_autoload
    resolver = FilterResolver(settings, env.paths, env.autoload, out=out, scan=scan)
    return resolver.resolve(chain, script)


def run_export_filters(
    ass: AssFile,
    resolved: list[ResolvedFilter],
    settings: Settings,
    options: RenderOptions,
    env: RenderEnvironment | None,
    out: Output,
    input_path: Path | None = None,
    fps: Framerate | None = None,
) -> None:
    """Run a resolved chain over `ass`, in the order it was written (AssExporter::Export).

    A built-in filter runs here; a Lua one gets a ScriptRender of its own, which is what
    Aegisub's LuaExportFilter does too -- and which is why `env` may only be None for a chain of
    built-in filters (`resolve_builtin_chain`), the case that needs no Aegisub installation.
    `fps` overrides the project's frame rate a filter is given. Raises AutomationError or ScriptError.
    """
    if fps is None:
        fps = env.video.timecodes if env is not None else Framerate()
    ctx = AutomationContext(fps=fps, subtitle_path=input_path, out=out)
    for item in resolved:
        if item.builtin is not None:
            item.builtin.from_args(item.args).process_filter(ass, ctx)
            continue
        if item.args.options or item.args.positional:
            raise AutomationError(
                f"{item.name}: this is a Lua export filter, which takes its settings from a dialog "
                "of its own; only the built-in filters take options here"
            )
        if env is None:
            raise AutomationError(f"{item.name}: a Lua export filter needs an Aegisub installation")
        with ScriptRender(
            ass,
            item.script,
            settings,
            subtitle_path=input_path,
            filter=item.name,
            video=options.video,
            trace_level=options.trace_level,
            quiet=options.quiet,
            progress=options.progress,
            out=out,
            env=env,
        ) as sr:
            sr.run()


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
        env = build_environment(ass, settings, subtitle_path=input_path, video=options.video)
        # The linked paths are written relative to where the result goes, which is this layer's
        # business (Aegisub does it when it saves, Project::SetSubtitlesFilename). It has to
        # happen before the worker starts: that is when the script gets its copy of the file.
        update_relative_paths(ass, env.video, env.paths, str(save_to.resolve().parent))
        resolved = resolve_export_filters(options, settings, env, out, ass)
        runner = script_render(ass, input_path, settings, options, out, env=env)
    except (RenderError, AutomationError, FilterSpecError, OSError) as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR
    for w in env.warnings:
        out.message(w)
    try:
        with runner:
            runner.run()
        run_export_filters(ass, resolved, settings, options, env, out, input_path)
    except ScriptError as e:
        return _report(e, out)
    except (RenderError, AutomationError, FilterSpecError) as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR
    data = output_data(
        ass,
        bool(resolved) or runner.is_filter,
        env.save_ui_state,
        options.keep_brand,
        output_path if isinstance(output_path, Path) else None,
        options.tags,
        env.video.timecodes,
    )
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
