"""Running an Aegisub automation script on an AssFile: ScriptRender.

This is the whole of "run the script" and none of "deal with files": it takes a subtitle file
that is already in memory and updates it in place. Reading, writing, choosing between Save and
Export, diffing and exit codes belong to the caller (see runner.render for the one the CLI uses).
"""

import dataclasses
import multiprocessing
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path, PurePath
from types import TracebackType
from typing import TYPE_CHECKING, Final, Protocol, Self, cast

from aegisub_render.ass.file import AssFile
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths, join
from aegisub_render.settings import Settings, automation_dirs, effective_autoload, read_aegisub_options
from aegisub_render.video.context import VideoContext, VideoOptions, build_video_context
from aegisub_render.worker import FeatureList, Job, Result, ScriptSession, worker_main

if TYPE_CHECKING:
    from aegisub_render.automation.base import AutomationContext, ExportFilter, Macro

_STOP: Final = "stop"


class _Pipe(Protocol):
    """The end of a multiprocessing pipe this module uses (Connection, or PipeConnection on
    Windows; they share no public base class in the type stubs)."""

    def send(self, obj: object) -> None: ...
    def recv(self) -> object: ...
    def poll(self, timeout: float | None = ...) -> bool: ...
    def close(self) -> None: ...


class RenderError(Exception):
    """The script cannot be run as asked (bad paths, no such script, a dead worker)."""


class ScriptError(Exception):
    """A request reached the script and did not come back with a result.

    `message` is the line to show the user; it is empty when the script already said everything
    it had to say through aegisub.debug.out.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class ScriptRefused(ScriptError):
    """Nothing ran: no such macro, a selection that does not exist, validation said no."""


class ScriptFailed(ScriptError):
    """The script raised, hit the memory limit, or ran out of time."""


class ScriptCancelled(ScriptError):
    """The script called aegisub.cancel()."""


#: what an Aegisub automation script is: Lua, or MoonScript compiled to it
LUA_SUFFIXES: Final = (".lua", ".moon")
#: a Python automation file (automation.plugin): the autoload directories may hold those as well,
#: and Aegisub ignores them, but there is nothing wrong with pointing --script at one
PYTHON_SUFFIXES: Final = (".py",)
#: everything this tool will load from an automation directory
AUTOMATION_SUFFIXES: Final = (*LUA_SUFFIXES, *PYTHON_SUFFIXES)

_SCRIPT_EXTENSIONS_TO_GUESS = LUA_SUFFIXES  # kept: the name this module used before


def automation_files(dirs: list[str], suffixes: Sequence[str] = LUA_SUFFIXES) -> list[Path]:
    """Every file of those directories with one of `suffixes`, in search (?user, ?data) order.

    The listing the autoload directories are read with: `automation.plugin` takes the .py files
    this way, and the export filter scan the .lua and .moon ones. A name starting with "_" is
    skipped, so a plugin can keep a private helper next to itself.
    """
    found: list[Path] = []
    wanted = tuple(s.lower() for s in suffixes)
    for directory in dirs:
        base = Path(directory)
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.suffix.lower() in wanted and not path.name.startswith("_") and path.is_file():
                found.append(path.resolve())
    return found


def _get_script_from_automation(
    name: str,
    paths: AegisubPaths,
    autoload_option: str,
    suffixes: Sequence[str] = AUTOMATION_SUFFIXES,
) -> Path:
    """A file name looked up in ?user then ?data autoload directories.

    Raises FileNotFoundError if no directory has it, and RenderError if one does but it is the
    wrong kind of file -- a .py among the Lua, say, which is a filter plugin and not a script.
    """

    for d in automation_dirs(paths, autoload_option):
        candidate = Path(join(d, name))
        if candidate.is_file():
            if candidate.suffix.lower() not in tuple(s.lower() for s in suffixes):
                raise RenderError(_wrong_kind(candidate, suffixes))
            return candidate.resolve()

    raise FileNotFoundError


def _wrong_kind(path: Path, suffixes: Sequence[str]) -> str:
    return f"{path} is not one of the file types this expects ({', '.join(suffixes)})"


def resolve_script(
    name: str,
    paths: AegisubPaths,
    autoload_option: str,
    suffixes: Sequence[str] = AUTOMATION_SUFFIXES,
) -> Path:
    """A path, or a file name looked up in ?user then ?data autoload directories.

    `suffixes` is what the caller can load: any automation file by default (.lua, .moon, .py),
    or a narrower set when only one kind will do. A name without one of them is tried as it is
    and then with each appended, in that order.
    """

    if any(sep in name for sep in "/\\") or PurePath(name).is_absolute():
        p = Path(name)
        if not p.is_file():
            raise RenderError(f"script not found: {name}")
        if p.suffix.lower() not in tuple(s.lower() for s in suffixes):
            raise RenderError(_wrong_kind(p, suffixes))
        return p.resolve()

    _, extension = os.path.splitext(name)

    if extension != "":
        # Refuse to load scripts without extensions
        try:
            return _get_script_from_automation(name, paths, autoload_option, suffixes)
        except FileNotFoundError:
            pass

    if extension.lower() not in suffixes:
        # The user only provided the script name without the extension. Let's make a rough guess.
        for guess in suffixes:
            try:
                return _get_script_from_automation(f"{name}{guess}", paths, autoload_option, suffixes)
            except FileNotFoundError:
                pass

    raise RenderError(f"script {name!r} not found in the autoload directories ({autoload_option})")


@dataclass(slots=True)
class RenderEnvironment:
    """Everything a run needs that does not involve Lua: the installation, the paths, the video.

    ScriptRender builds one in its constructor; `build_environment` is the same work without a
    script, for a caller that only wants the project's video and path tokens -- running export
    filters, say, where there may be no script at all.
    """

    paths: AegisubPaths
    video: VideoContext
    save_ui_state: bool
    #: Path/Automation/Autoload with the extra directories merged in (settings.effective_autoload)
    autoload: str
    include: str
    warnings: list[str] = dataclasses.field(default_factory=list[str])


def build_environment(
    file: AssFile,
    settings: Settings | None = None,
    *,
    subtitle_path: Path | None = None,
    video: VideoOptions | None = None,
) -> RenderEnvironment:
    """Resolve the Aegisub installation, the path tokens and the project's video.

    `subtitle_path` is the .ass this AssFile came from: its directory is Aegisub's ?script token.
    Raises RenderError if the installation is not where the settings say.
    """
    settings = settings or Settings()
    root = settings.aegisub_root
    if not (root / "automation").is_dir():
        raise RenderError(f"{root} does not look like an Aegisub installation (no automation directory)")
    paths = AegisubPaths.create(root, settings.load_appdata)
    subtitle_dir = (subtitle_path.resolve().parent if subtitle_path is not None else Path.cwd()).resolve()
    paths.set_token("?script", str(subtitle_dir))
    aeg = read_aegisub_options(paths)

    vopts = dataclasses.replace(video) if video is not None else VideoOptions()  # never the caller's object
    vopts.open_audio = aeg.open_audio
    ctx = build_video_context(file, paths, vopts)
    return RenderEnvironment(
        paths=paths,
        video=ctx,
        save_ui_state=aeg.save_ui_state,
        autoload=effective_autoload(settings, aeg.autoload),
        include=aeg.include,
        warnings=list(ctx.warnings),
    )


class ScriptRender:
    """Run one Aegisub automation script on one AssFile.

    Building it resolves everything that does not need Lua (the Aegisub installation, the path
    tokens, the project's video, the script's location) and raises RenderError if any of that
    fails. The Lua state is a worker that lives from the first request until stop_lua_worker(),
    so the macro's validation function and the macro itself share one state, the way they do in
    Aegisub::

        with ScriptRender(ass, "kara-templater.lua", settings) as sr:
            if not sr.macro_can_run():
                raise RuntimeError(sr.refusal_reason())
            sr.run()            # ass is updated in place

    `run()` copies the result back into the AssFile it was given, so the caller keeps using the
    object it already has; the entries inside it are new objects, so a reference to a particular
    AssDialogue taken before the run goes stale. Running twice applies the macro twice, as
    pressing it twice in Aegisub would.

    It owns a process, so it is not thread-safe, cannot be pickled, and answers one request at a
    time. With `isolate=False` the Lua state lives in this process instead: no spawn, but also no
    wall-clock limit and no way to survive a hard crash in the state.
    """

    def __init__(
        self,
        file: AssFile,
        script: str | Path | None = None,
        settings: Settings | None = None,
        *,
        subtitle_path: Path | None = None,
        macro: str | None = None,
        filter: str | None = None,
        selection: list[int] | None = None,
        force: bool = False,
        video: VideoOptions | None = None,
        trace_level: int | None = None,
        quiet: bool = False,
        progress: bool | None = None,
        isolate: bool = True,
        out: Output | None = None,
        env: RenderEnvironment | None = None,
    ) -> None:
        """`subtitle_path` is the .ass this AssFile came from: its directory is Aegisub's ?script
        token and its name is what aegisub.file_name() returns. None means the working directory
        and "Untitled.ass", like a file Aegisub has not saved yet.

        `script` is a file name looked up in the autoload directories, or a path; None takes
        settings.default_script. `macro` and `filter` are mutually exclusive; with neither,
        settings.default_macro runs.

        `env` is a RenderEnvironment the caller already built (build_environment), so that a
        chain of runs over one file does not resolve the installation and probe the video again.
        """
        self.ass = file
        self.settings = settings = settings or Settings()
        self.out = out or Output(quiet=quiet, progress=progress)
        self._isolate = isolate
        self.warnings: list[str] = []

        if env is None:
            env = build_environment(file, settings, subtitle_path=subtitle_path, video=video)
        self.env = env
        self.paths = env.paths
        self.save_ui_state = env.save_ui_state
        self.video = env.video
        self.warnings.extend(env.warnings)
        aeg_autoload, aeg_include = env.autoload, env.include

        wanted = str(script) if script is not None else settings.default_script
        script_path = resolve_script(wanted, self.paths, aeg_autoload)
        self.job = Job(
            script_path=script_path,
            include_dirs=[
                str(script_path.parent),
                *automation_dirs(self.paths, aeg_include),
            ],
            path_tokens=dict(self.paths.tokens),
            ass=file,
            video=self.video,
            subtitle_filename=(subtitle_path.name if subtitle_path is not None else "Untitled.ass"),
            trace_level=(trace_level if trace_level is not None else settings.trace_level),
            memory_limit=settings.memory_limit_bytes,
            max_output_lines=settings.max_output_lines,
            font_dirs=list(settings.font_dirs),
            quiet=quiet,
            progress=progress,
            macro=(macro if macro is not None else (None if filter is not None else settings.default_macro)),
            filter=filter,
            selection=selection,
            force=force,
        )

        #: a .py script is a Python automation file: no Lua state, and no default macro either
        #: (the config's default macro name is Lua's; a .py has whatever it registers)
        self.is_python = script_path.suffix.lower() in PYTHON_SUFFIXES
        self._macro_arg = macro
        self._proc: BaseProcess | None = None
        self._conn: _Pipe | None = None
        self._session: ScriptSession | None = None
        self._stopped = False
        self._busy = False
        self._features: FeatureList | None = None
        self._can_run: bool | None = None
        self._refusal: str = ""

    # -- the script's name for things, resolved without running Lua -------------------------

    @property
    def is_filter(self) -> bool:
        """Whether this will run an export filter (File > Export) instead of a macro (then Save)."""
        return self.job.filter is not None

    @property
    def script_path(self) -> Path:
        """The automation script that was found for this run."""
        return self.job.script_path

    @property
    def alive(self) -> bool:
        return self._session is not None or self._proc is not None

    # -- the Lua worker ----------------------------------------------------------------------

    def start_lua_worker(self) -> None:
        """Build the Lua state and load the script. A no-op once it is running.

        A Python automation script has no Lua state; this imports it instead, so that the file
        has registered its filters by the time anything asks what it provides.
        """
        if self.is_python:
            self._python_registered()
            return
        if self.alive:
            return
        if self._stopped:
            raise RenderError("this ScriptRender was stopped; make a new one to run the script again")
        if self._isolate:
            ctx = multiprocessing.get_context("spawn")
            parent_conn, child_conn = ctx.Pipe(duplex=True)
            proc = ctx.Process(target=worker_main, args=(child_conn, self.job), daemon=True)
            proc.start()
            child_conn.close()
            self._proc, self._conn = proc, parent_conn
            loaded = self._receive(self._deadline())
        else:
            self._session = ScriptSession(self.job, self.out)
            loaded = self._session.load()
        self._check_loaded(loaded)

    def stop_lua_worker(self) -> None:
        """Let the worker go. Every later request raises RenderError."""
        self._stopped = True
        if self._session is not None:
            self._session.close()
            self._session = None
        proc, conn = self._proc, self._conn
        self._proc, self._conn = None, None
        if proc is None or conn is None:
            return
        try:
            if not self._busy:
                conn.send(_STOP)
            proc.join(5)
        except OSError, EOFError, BrokenPipeError:
            pass
        finally:
            _kill(proc, conn)

    def __enter__(self) -> Self:
        self.start_lua_worker()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop_lua_worker()

    def __del__(self) -> None:
        """A forgotten stop_lua_worker() must not leave the worker behind."""
        try:
            self.stop_lua_worker()
        except Exception:
            pass

    # -- requests ----------------------------------------------------------------------------

    def _python_registered(self) -> tuple[list[type[Macro]], list[type[ExportFilter]]]:
        """Import the Python automation file; the macros and filters it registers."""
        from aegisub_render.automation.base import AutomationError
        from aegisub_render.automation.plugin import plugin_filters, plugin_macros

        try:
            return plugin_macros(self.script_path), plugin_filters(self.script_path)
        except (OSError, ImportError, AutomationError) as e:
            raise ScriptFailed(f"aegisub-render: could not load {self.script_path.name}: {e}") from None
        except BaseException as e:  # the file's top level can raise anything at all
            raise ScriptFailed(f"aegisub-render: {self.script_path.name} failed to load: {e}") from None

    def _python_macro(self) -> type[Macro]:
        """The macro this run asks for: `macro`, or the only one the file registers."""
        found, _ = self._python_registered()
        wanted = self._macro_arg
        if not found:
            raise ScriptRefused(
                f"aegisub-render: {self.script_path.name} registers no macros"
                + (f" (it has the export filter {self.job.filter!r})" if self.job.filter else "")
            )
        if wanted is None:
            return found[0]
        chosen = next((m for m in found if m.name == wanted), None)
        if chosen is None:
            available = ", ".join(repr(m.name) for m in found)
            raise ScriptRefused(
                f"aegisub-render: {self.script_path.name} has no macro named {wanted!r}; available: {available}"
            )
        return chosen

    def _python_context(self) -> AutomationContext:
        from aegisub_render.automation.base import AutomationContext

        return AutomationContext(fps=self.video.timecodes, out=self.out)

    def features(self) -> FeatureList:
        """The macros and export filters the script registers (the `list` command). Cached."""
        if self.is_python and self._features is None:
            found_macros, found_filters = self._python_registered()
            self._features = FeatureList(
                self.script_path.stem,
                "",
                "",
                "",
                [(m.name, m.description) for m in found_macros],
                [(f.name, f.description, f.priority) for f in found_filters],
            )
        if self._features is None:
            result = self._request("features")
            if result.features is None:
                raise ScriptFailed(result.message)
            self._features = result.features
        return self._features

    def macro_can_run(self) -> bool:
        """What the macro's validation function says: whether Aegisub would enable its menu item.

        Cached. True for an export filter and for a macro without a validation function. The run
        itself validates again unless `force` was given, so asking first is optional; both calls
        share one Lua state, as they do in Aegisub.
        """
        if self.is_python and self._can_run is None:
            try:
                if self.job.filter is not None:
                    self._can_run = True  # an export filter has nothing to validate
                else:
                    macro = self._python_macro()
                    self._can_run = macro().validate_macro(self.ass, [], 0, self._python_context())
                    self._refusal = "" if self._can_run else _disabled(self.script_path.name, macro.name)
            except ScriptRefused as e:
                self._can_run = False
                self._refusal = e.message
        if self._can_run is None:
            result = self._request("validate")
            if result.can_run is None:
                raise ScriptFailed(result.message)
            self._can_run = result.can_run
            self._refusal = result.message
        return self._can_run

    def refusal_reason(self) -> str | None:
        """Why macro_can_run() said no, in the words the CLI would print. None if it can run."""
        return None if self.macro_can_run() else (self._refusal or None)

    def run(self) -> AssFile:
        """Run the macro or export filter; the AssFile given to the constructor is updated.

        Returns that same object. Raises ScriptRefused (nothing ran), ScriptCancelled
        (aegisub.cancel()) or ScriptFailed (the script raised, or hit a limit).
        """
        if self.is_python:
            self._run_python()
            return self.ass
        result = self._request("run")
        if result.ass is None:
            raise ScriptFailed(result.message)
        _adopt(self.ass, result.ass)
        return self.ass

    def _run_python(self) -> None:
        """Run the Python automation file's macro, or one of its export filters.

        The selection Aegisub hands a macro is `[]` and `0` here: this tool has no selection of
        its own yet (see automation.base.Macro).
        """
        from aegisub_render.automation.base import AutomationError

        if self.job.filter is not None:
            _, found = self._python_registered()
            chosen = next((f for f in found if f.name == self.job.filter), None)
            if chosen is None:
                available = ", ".join(repr(f.name) for f in found) or "(none)"
                raise ScriptRefused(
                    f"aegisub-render: {self.script_path.name} has no export filter named "
                    f"{self.job.filter!r}; available: {available}"
                )
            run = lambda: chosen().process_filter(self.ass, self._python_context())  # noqa: E731
            name = chosen.name
        else:
            macro = self._python_macro()
            instance = macro()
            ctx = self._python_context()
            if not self.job.force and not instance.validate_macro(self.ass, [], 0, ctx):
                raise ScriptRefused(_disabled(self.script_path.name, macro.name))
            run = lambda: instance.process_macro(self.ass, [], 0, ctx)  # noqa: E731
            name = macro.name

        self.out.progress_begin(name)
        try:
            run()
        except AutomationError as e:
            self.out.progress_end(complete=False)
            raise ScriptFailed(f"aegisub-render: {e}") from None
        except Exception as e:  # a plugin is ordinary Python: a bug in it is not ours
            self.out.progress_end(complete=False)
            raise ScriptFailed(f"aegisub-render: {name} failed: {e!r}") from None
        self.out.progress_end(complete=True)

    def _request(self, what: str) -> Result:
        self.start_lua_worker()
        if self._busy:
            raise RuntimeError("this ScriptRender is already running a request")
        self._busy = True
        try:
            if self._session is not None:
                answer = {
                    "run": self._session.run,
                    "features": self._session.features,
                    "validate": self._session.validate,
                }.get(what)
                assert answer, f"Invalid request: {what}"
                result = answer()
            else:
                conn = self._conn
                if conn is None:
                    raise RenderError("the Lua worker is gone")
                conn.send(what)
                result = self._receive(self._deadline())
        finally:
            self._busy = False
        if result.status == "ok":
            # validate reports a refusal reason in `message` without failing
            return result
        if result.status == "refused":
            raise ScriptRefused(result.message)
        if result.status == "cancel":
            raise ScriptCancelled(result.message)
        raise ScriptFailed(result.message)

    # -- plumbing ----------------------------------------------------------------------------

    def _deadline(self) -> float:
        """Each request gets the whole wall-clock budget: a session is several requests."""
        return time.monotonic() + self.settings.timeout_seconds

    def _check_loaded(self, loaded: Result) -> None:
        if loaded.status == "ok":
            return
        self.stop_lua_worker()
        if loaded.status == "refused":
            raise ScriptRefused(loaded.message)
        raise ScriptFailed(loaded.message)

    def _receive(self, deadline: float) -> Result:
        proc, conn = self._proc, self._conn
        if proc is None or conn is None:
            raise RenderError("the Lua worker is gone")
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._tear_down(proc, conn)
                    timeout = self.settings.timeout_seconds
                    raise ScriptFailed(
                        f"aegisub-render: the script ran longer than {timeout:g} seconds and was stopped"
                    )
                if conn.poll(min(remaining, 0.25)):
                    try:
                        received = conn.recv()
                    except EOFError:
                        break
                    return cast(Result, received)
                if not proc.is_alive():
                    break
        except BaseException:
            self._tear_down(proc, conn)
            raise
        exitcode = proc.exitcode
        self._tear_down(proc, conn)
        raise ScriptFailed(f"aegisub-render: the Lua worker process died (exit code {exitcode})")

    def _tear_down(self, proc: BaseProcess, conn: _Pipe) -> None:
        self._proc, self._conn = None, None
        self._stopped = True
        _kill(proc, conn)


def _disabled(script_name: str, macro_name: str) -> str:
    """What the CLI prints when a macro's validation function says no."""
    return f"aegisub-render: {script_name}'s macro {macro_name!r} said it cannot run on this file"


def _kill(proc: BaseProcess, conn: _Pipe) -> None:
    try:
        if proc.is_alive():
            proc.kill()
        proc.join()
    except OSError, ValueError, AssertionError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _adopt(target: AssFile, source: AssFile) -> None:
    """Move a worker's result into the caller's AssFile, so `run()` updates it in place."""
    if target is source:
        return
    target.info = source.info
    target.styles = source.styles
    target.events = source.events
    target.attachments = source.attachments
    target.extradata = source.extradata
    target.properties = source.properties
    target.next_extradata_id = source.next_extradata_id
    target.had_project_garbage = source.had_project_garbage
    target.had_extradata = source.had_extradata
    target.header_comments = source.header_comments
