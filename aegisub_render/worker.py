"""The child process that owns the Lua state.

Running Lua in its own process lets the parent enforce the wall-clock limit by killing it,
which also covers JIT-compiled loops and C functions that a Lua hook could never interrupt.
"""

from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from pathlib import Path

from aegisub_render.ass.file import AssFile
from aegisub_render.host.script import HostConfig, Macro, RunStatus, ScriptHost, ScriptInfo, ScriptLoadError
from aegisub_render.lua.state import LuaError
from aegisub_render.output import Output, use_utf8_for_pipes
from aegisub_render.paths import AegisubPaths
from aegisub_render.video.context import VideoContext


@dataclass(slots=True)
class Job:
    script_path: Path
    include_dirs: list[str]
    path_tokens: dict[str, str]
    ass: AssFile
    video: VideoContext
    subtitle_filename: str | None
    trace_level: int
    memory_limit: int
    max_output_lines: int
    font_dirs: list[Path]
    quiet: bool
    progress: bool | None
    list_only: bool = False
    macro: str | None = None
    filter: str | None = None
    selection: list[int] | None = None  # 1-based dialogue line numbers; None = all dialogue lines
    force: bool = False


@dataclass(slots=True)
class FeatureList:
    script_name: str
    script_description: str
    script_author: str
    script_version: str
    macros: list[tuple[str, str]]
    filters: list[tuple[str, str, int]]


@dataclass(slots=True)
class Result:
    status: str  # "ok" | "error" | "cancel"
    message: str = ""
    ass: AssFile | None = None
    features: FeatureList | None = None
    memory_peak: int = 0
    memory_limit_hit: bool = False
    warnings: list[str] = field(default_factory=list[str])


class JobError(Exception):
    pass


def _select_rows(ass: AssFile, selection: list[int] | None) -> tuple[list[int], int]:
    """selected_rows(): 1-based subtitle-object indices of the selected dialogue lines."""
    offset = len(ass.info) + len(ass.styles)
    count = len(ass.events)
    if selection is None:
        rows = [offset + i + 1 for i in range(count)]
    else:
        bad = [n for n in selection if not (1 <= n <= count)]
        if bad:
            raise JobError(f"--select: dialogue line {bad[0]} does not exist (the file has {count} dialogue lines)")
        rows = sorted({offset + n for n in selection})
    return rows, rows[0] if rows else 0


def _pick_macro(info: ScriptInfo, name: str) -> Macro:
    for m in info.macros:
        if m.name == name:
            return m
    available = ", ".join(repr(m.name) for m in info.macros) or "(none)"
    raise JobError(f"script has no macro named {name!r}; available macros: {available}")


def execute(job: Job, out: Output | None = None) -> Result:
    out = out or Output(quiet=job.quiet, progress=job.progress)
    paths = AegisubPaths(dict(job.path_tokens))
    cfg = HostConfig(
        script_path=job.script_path,
        include_dirs=job.include_dirs,
        paths=paths,
        trace_level=job.trace_level,
        memory_limit=job.memory_limit,
        max_output_lines=job.max_output_lines,
        font_dirs=job.font_dirs,
        subtitle_filename=job.subtitle_filename,
    )
    host: ScriptHost | None = None
    try:
        host = ScriptHost(cfg, job.ass, job.video, out)
        try:
            info = host.load_script()
        except ScriptLoadError as e:
            return Result("error", str(e))
        for w in info.warnings:
            out.message(f"Warning in Automation script '{job.script_path.stem}':\n{w}")

        if job.list_only:
            return Result(
                "ok",
                features=FeatureList(
                    info.name,
                    info.description,
                    info.author,
                    info.version,
                    [(m.name, m.help) for m in info.macros],
                    [(f.name, f.description, f.priority) for f in info.filters],
                ),
            )

        if job.filter is not None:
            flt = next((f for f in info.filters if f.name == job.filter), None)
            if flt is None:
                available = ", ".join(repr(f.name) for f in info.filters) or "(none)"
                return Result("error", f"script has no export filter named {job.filter!r}; available: {available}")
            out.progress_begin(flt.name)
            status = host.run_filter(flt)
        else:
            macro = _pick_macro(info, job.macro or "")
            rows, active = _select_rows(job.ass, job.selection)
            if macro.has_validate and not job.force and not host.validate_macro(macro, rows, active):
                return Result(
                    "error",
                    f"the validation function of macro {macro.name!r} returned false "
                    "(Aegisub would grey it out); use --force to run it anyway",
                )
            out.progress_begin(macro.name)
            status = host.run_macro(macro, rows, active)
        out.progress_end(complete=status is RunStatus.OK)

        result = Result(str(status), memory_peak=host.state.memory_peak, memory_limit_hit=host.state.memory_limit_hit)
        if status is RunStatus.OK:
            result.ass = job.ass
        elif status is RunStatus.ERROR and host.state.memory_limit_hit:
            result.message = f"aegisub-render: the Lua memory limit ({job.memory_limit} bytes) was exceeded"
        return result
    except JobError as e:
        return Result("error", str(e))
    except LuaError as e:
        return Result("error", f"aegisub-render: unexpected Lua error in the host: {e.message}")
    finally:
        out.progress_end(complete=False)
        out.finish_line()
        if host is not None:
            host.close()


def worker_main(conn: Connection, job: Job) -> None:
    use_utf8_for_pipes()
    try:
        result = execute(job)
    except BaseException as e:  # report anything, including crashes in the host itself
        import traceback

        result = Result("error", "aegisub-render internal error:\n" + "".join(traceback.format_exception(e)))
    conn.send(result)
    conn.close()
