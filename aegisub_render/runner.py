"""Public entry points: render a subtitle file, or list a script's features."""

import multiprocessing
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path, PureWindowsPath
from typing import Final, Literal

from aegisub_render.ass.entries import SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.parser import UnsupportedEncodingError, read_ass_file
from aegisub_render.ass.writer import serialize_ass, serialize_export, write_ass_file
from aegisub_render.diff import text_lines, unified_diff
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths, join
from aegisub_render.settings import Settings, automation_dirs, read_aegisub_options
from aegisub_render.video.context import VideoOptions, build_video_context
from aegisub_render.worker import FeatureList, Job, Result, worker_main

STDOUT: Final = "-"  # render() output path meaning standard output


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


class RenderError(Exception):
    pass


@dataclass(slots=True)
class Prepared:
    job: Job
    save_ui_state: bool


def resolve_script(name: str, paths: AegisubPaths, autoload_option: str) -> Path:
    """A path, or a file name looked up in ?user then ?data autoload directories."""
    if any(sep in name for sep in "/\\") or PureWindowsPath(name).is_absolute():
        p = Path(name)
        if not p.is_file():
            raise RenderError(f"script not found: {name}")
        return p.resolve()
    for d in automation_dirs(paths, autoload_option):
        candidate = Path(join(d, name))
        if candidate.is_file():
            return candidate.resolve()
    raise RenderError(f"script {name!r} not found in the autoload directories ({autoload_option})")


def prepare(
    input_path: Path, settings: Settings, options: RenderOptions, out: Output, save_to: Path | None = None
) -> Prepared:
    """`save_to` is where the result will be saved (default: the input), which the linked file paths
    are made relative to."""
    root = settings.aegisub_root
    if not (root / "automation").is_dir():
        raise RenderError(f"{root} does not look like an Aegisub installation (no automation directory)")
    paths = AegisubPaths.create(root, settings.load_appdata)
    input_path = input_path.resolve()
    paths.set_token("?script", str(input_path.parent))
    aeg = read_aegisub_options(paths)

    try:
        ass = read_ass_file(input_path)
    except (UnsupportedEncodingError, SubtitleFormatParseError) as e:
        raise RenderError(f"could not read {input_path}: {e}") from None
    if ass.line_count() > settings.max_input_lines:
        raise RenderError(
            f"{input_path} has {ass.line_count()} lines, more than the limit of "
            f"{settings.max_input_lines} (MAX_INPUT_LINES)"
        )

    vopts = options.video
    vopts.open_audio = aeg.open_audio
    save_dir = str(save_to.resolve().parent) if save_to is not None else None
    video = build_video_context(ass, paths, vopts, save_dir)
    for w in video.warnings:
        out.message(w)

    script = resolve_script(options.script or settings.default_script, paths, aeg.autoload)
    include_dirs = [str(script.parent), *automation_dirs(paths, aeg.include)]

    job = Job(
        script_path=script,
        include_dirs=include_dirs,
        path_tokens=dict(paths.tokens),
        ass=ass,
        video=video,
        subtitle_filename=input_path.name,
        trace_level=options.trace_level if options.trace_level is not None else settings.trace_level,
        memory_limit=settings.memory_limit_bytes,
        max_output_lines=settings.max_output_lines,
        font_dirs=list(settings.font_dirs),
        quiet=options.quiet,
        progress=options.progress,
        macro=options.macro
        if options.macro is not None
        else (None if options.filter is not None else settings.default_macro),
        filter=options.filter,
        selection=options.selection,
        force=options.force,
    )
    return Prepared(job, aeg.save_ui_state)


def output_data(prepared: Prepared, ass: AssFile) -> bytes:
    """What Aegisub writes after the script ran. Export filters only run in File > Export, which
    writes with ExportFile; a macro is followed by File > Save."""
    if prepared.job.filter is not None:
        return serialize_export(ass, prepared.save_ui_state)
    return serialize_ass(ass, prepared.save_ui_state)


def run_job(job: Job, timeout: float) -> Result:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=worker_main, args=(child_conn, job), daemon=True)
    proc.start()
    child_conn.close()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.join()
                return Result(
                    "error", f"aegisub-render: the script ran longer than {timeout:g} seconds and was stopped"
                )
            if parent_conn.poll(min(remaining, 0.25)):
                try:
                    result: Result = parent_conn.recv()
                except EOFError:
                    break
                proc.join()
                return result
            if not proc.is_alive():
                break
    except BaseException:
        proc.kill()
        proc.join()
        raise
    proc.join()
    return Result("error", f"aegisub-render: the Lua worker process died (exit code {proc.exitcode})")


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
        prepared = prepare(input_path, settings, options, out, save_to=save_to)
        original = input_path.read_bytes() if options.dry_run else b""
    except (RenderError, OSError) as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR
    result = run_job(prepared.job, settings.timeout_seconds)
    if result.message:
        out.error(result.message)
    if result.status == "refused":  # the message says why
        return ExitCode.ERROR
    if result.status == "cancel":
        out.error("aegisub-render: the script cancelled the operation; no output was written")
        return ExitCode.CANCELLED
    if result.status != "ok" or result.ass is None:
        out.error("aegisub-render: the script failed; no output was written")
        return ExitCode.ERROR
    data = output_data(prepared, result.ass)
    if options.dry_run:
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


def list_features(
    input_path: Path, settings: Settings, options: RenderOptions | None = None, out: Output | None = None
) -> FeatureList | None:
    options = options or RenderOptions()
    out = out or Output(quiet=options.quiet, progress=False)
    try:
        prepared = prepare(input_path, settings, options, out)
    except RenderError as e:
        out.error(f"aegisub-render: {e}")
        return None
    prepared.job.list_only = True
    result = run_job(prepared.job, settings.timeout_seconds)
    if result.message:
        out.error(result.message)
    return result.features
