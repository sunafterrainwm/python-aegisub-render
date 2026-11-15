"""The child process that owns the Lua state.

Running Lua in its own process lets the parent enforce the wall-clock limit by killing it,
which also covers JIT-compiled loops and C functions that a Lua hook could never interrupt.
"""

from collections.abc import Callable
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
    status: str  # "ok" | "error" (the script failed) | "cancel" | "refused" (nothing ran; see message)
    message: str = ""
    ass: AssFile | None = None
    features: FeatureList | None = None
    can_run: bool | None = None  # the "validate" request: what the macro's validation function said
    memory_peak: int = 0
    memory_limit_hit: bool = False
    warnings: list[str] = field(default_factory=list[str])


class JobError(Exception):
    """The job cannot be run as asked; the script was not run."""


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


def _disabled_message(job: Job, macro: Macro) -> str:
    text = (
        f"macro {macro.name!r} is disabled for this file: its validation function returned false, "
        "so Aegisub would grey it out"
    )
    if job.script_path.name.lower() == "kara-templater.lua":  # macro_can_template
        text += " (kara-templater needs a line whose Effect starts with 'template' among the first 51 [Events] lines)"
    return text + ". Use --force to run it anyway"


class ScriptSession:
    """One Lua state: load the script once, then answer requests against it.

    Aegisub keeps a script's state between the menu asking a macro whether it may run and the
    macro running, so validate and run have to happen in the same session. Running two macros
    (or running the same one twice) through one session is like applying them in Aegisub one
    after the other: the second one sees what the first one did.
    """

    def __init__(self, job: Job, out: Output | None = None) -> None:
        self.job = job
        self.out = out or Output(quiet=job.quiet, progress=job.progress)
        self.host: ScriptHost | None = None
        self.info: ScriptInfo | None = None

    def load(self) -> Result:
        """Build the Lua state and load the script. Everything else needs this to have run."""
        job = self.job
        cfg = HostConfig(
            script_path=job.script_path,
            include_dirs=job.include_dirs,
            paths=AegisubPaths(dict(job.path_tokens)),
            trace_level=job.trace_level,
            memory_limit=job.memory_limit,
            max_output_lines=job.max_output_lines,
            font_dirs=job.font_dirs,
            subtitle_filename=job.subtitle_filename,
        )
        try:
            self.host = ScriptHost(cfg, job.ass, job.video, self.out)
            try:
                self.info = self.host.load_script()
            except ScriptLoadError as e:
                return Result("error", str(e))
            for w in self.info.warnings:
                self.out.message(f"Warning in Automation script '{job.script_path.stem}':\n{w}")
            return Result("ok")
        except LuaError as e:
            return Result("error", f"aegisub-render: unexpected Lua error in the host: {e.message}")
        finally:
            self.out.finish_line()

    def _loaded(self) -> tuple[ScriptHost, ScriptInfo]:
        if self.host is None or self.info is None:
            raise JobError("the script was not loaded")
        return self.host, self.info

    def features(self) -> Result:
        """What the script registers: the `list` command's answer."""
        try:
            _, info = self._loaded()
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
        except JobError as e:
            return Result("refused", f"aegisub-render: {e}")
        except LuaError as e:
            return Result("error", f"aegisub-render: unexpected Lua error in the host: {e.message}")
        finally:
            self.out.finish_line()

    def validate(self) -> Result:
        """Run the macro's validation function: what Aegisub asks before it enables the menu item.

        An export filter has nothing to validate, and neither has a macro without a validation
        function, so both answer True.
        """
        job = self.job
        try:
            host, info = self._loaded()
            if job.filter is not None:
                if not any(f.name == job.filter for f in info.filters):
                    self._no_filter(info)
                return Result("ok", can_run=True)
            macro = _pick_macro(info, job.macro or "")
            if not macro.has_validate:
                return Result("ok", can_run=True)
            rows, active = _select_rows(job.ass, job.selection)
            can_run = host.validate_macro(macro, rows, active)
            return Result("ok", can_run=can_run, message="" if can_run else _disabled_message(job, macro))
        except JobError as e:
            return Result("refused", f"aegisub-render: {e}")
        except LuaError as e:
            return Result("error", f"aegisub-render: unexpected Lua error in the host: {e.message}")
        finally:
            self.out.finish_line()

    def _no_filter(self, info: ScriptInfo) -> None:
        available = ", ".join(repr(f.name) for f in info.filters) or "(none)"
        raise JobError(f"script has no export filter named {self.job.filter!r}; available: {available}")

    def run(self) -> Result:
        """Run the macro or the export filter. `ass` of an "ok" result is the job's own AssFile."""
        job = self.job
        try:
            host, info = self._loaded()
            if job.filter is not None:
                flt = next((f for f in info.filters if f.name == job.filter), None)
                if flt is None:
                    self._no_filter(info)
                    raise JobError(f"script has no export filter named {job.filter!r}")  # _no_filter raised
                self.out.progress_begin(flt.name)
                status = host.run_filter(flt)
            else:
                macro = _pick_macro(info, job.macro or "")
                rows, active = _select_rows(job.ass, job.selection)
                if macro.has_validate and not job.force and not host.validate_macro(macro, rows, active):
                    raise JobError(_disabled_message(job, macro))
                self.out.progress_begin(macro.name)
                status = host.run_macro(macro, rows, active)
            self.out.progress_end(complete=status is RunStatus.OK)

            result = Result(
                str(status), memory_peak=host.state.memory_peak, memory_limit_hit=host.state.memory_limit_hit
            )
            if status is RunStatus.OK:
                result.ass = job.ass
            elif status is RunStatus.ERROR and host.state.memory_limit_hit:
                result.message = f"aegisub-render: the Lua memory limit ({job.memory_limit} bytes) was exceeded"
            return result
        except JobError as e:
            return Result("refused", f"aegisub-render: {e}")
        except LuaError as e:
            return Result("error", f"aegisub-render: unexpected Lua error in the host: {e.message}")
        finally:
            self.out.progress_end(complete=False)
            self.out.finish_line()

    def close(self) -> None:
        if self.host is not None:
            self.host.close()
            self.host = None
        self.info = None


def execute(job: Job, out: Output | None = None) -> Result:
    """Load the script and run it once, in this process: no worker, no wall-clock limit.

    ScriptRender(isolate=False) is the same thing with the session kept open.
    """
    session = ScriptSession(job, out)
    try:
        loaded = session.load()
        return loaded if loaded.status != "ok" else session.run()
    finally:
        session.close()


def _guard(answer: Callable[[], Result]) -> Result:
    """Any crash in the host itself is reported like a failed script, not by dying."""
    try:
        return answer()
    except BaseException as e:
        import traceback

        return Result("error", "aegisub-render internal error:\n" + "".join(traceback.format_exception(e)))


def worker_main(conn: Connection, job: Job) -> None:
    """The child process: load the script, then answer one request at a time until told to stop.

    The first thing sent back is the result of loading the script; after that, every request
    ("features", "validate", "run") gets exactly one Result. "stop", a closed pipe or anything
    unknown ends the loop.
    """
    use_utf8_for_pipes()
    session = ScriptSession(job)
    try:
        loaded = _guard(session.load)
        conn.send(loaded)
        if loaded.status != "ok":
            return
        answers: dict[str, Callable[[], Result]] = {
            "features": session.features,
            "validate": session.validate,
            "run": session.run,
        }
        while True:
            try:
                request = conn.recv()
            except EOFError:
                return
            if request == "stop":
                return
            answer = answers.get(request) if isinstance(request, str) else None
            if answer is None:
                conn.send(Result("error", f"aegisub-render internal error: unknown request {request!r}"))
                return
            conn.send(_guard(answer))
    finally:
        session.close()
        conn.close()
