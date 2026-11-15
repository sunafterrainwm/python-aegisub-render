"""Running an Aegisub automation script on an AssFile: ScriptRender.

This is the whole of "run the script" and none of "deal with files": it takes a subtitle file
that is already in memory and updates it in place. Reading, writing, choosing between Save and
Export, diffing and exit codes belong to the caller (see runner.render for the one the CLI uses).
"""

import dataclasses
import multiprocessing
import os
import time
from multiprocessing.process import BaseProcess
from pathlib import Path, PurePath
from types import TracebackType
from typing import Final, Protocol, Self, cast

from aegisub_render.ass.file import AssFile
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths, join
from aegisub_render.settings import Settings, automation_dirs, read_aegisub_options
from aegisub_render.video.context import VideoOptions, build_video_context
from aegisub_render.worker import FeatureList, Job, Result, ScriptSession, worker_main

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


_SCRIPT_EXTENSIONS_TO_GUESS = (".lua", ".moon")


def _get_script_from_automation(name: str, paths: AegisubPaths, autoload_option: str) -> Path:
    """A file name looked up in ?user then ?data autoload directories."""

    for d in automation_dirs(paths, autoload_option):
        candidate = Path(join(d, name))
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError


def resolve_script(name: str, paths: AegisubPaths, autoload_option: str) -> Path:
    """A path, or a file name looked up in ?user then ?data autoload directories."""

    if any(sep in name for sep in "/\\") or PurePath(name).is_absolute():
        p = Path(name)
        if not p.is_file():
            raise RenderError(f"script not found: {name}")
        return p.resolve()

    _, extension = os.path.splitext(name)

    if extension != "":
        # Refuse to load scripts without extensions
        try:
            return _get_script_from_automation(name, paths, autoload_option)
        except FileNotFoundError:
            pass

    if extension.lower() not in _SCRIPT_EXTENSIONS_TO_GUESS:
        # The user only provided the script name without the extension. Let's make a rough guess.
        for extension in _SCRIPT_EXTENSIONS_TO_GUESS:
            try:
                return _get_script_from_automation(f"{name}{extension}", paths, autoload_option)
            except FileNotFoundError:
                pass

    raise RenderError(f"script {name!r} not found in the autoload directories ({autoload_option})")


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
    ) -> None:
        """`subtitle_path` is the .ass this AssFile came from: its directory is Aegisub's ?script
        token and its name is what aegisub.file_name() returns. None means the working directory
        and "Untitled.ass", like a file Aegisub has not saved yet.

        `script` is a file name looked up in the autoload directories, or a path; None takes
        settings.default_script. `macro` and `filter` are mutually exclusive; with neither,
        settings.default_macro runs.
        """
        self.ass = file
        self.settings = settings = settings or Settings()
        self.out = out or Output(quiet=quiet, progress=progress)
        self._isolate = isolate
        self.warnings: list[str] = []

        root = settings.aegisub_root
        if not (root / "automation").is_dir():
            raise RenderError(f"{root} does not look like an Aegisub installation (no automation directory)")
        self.paths = AegisubPaths.create(root, settings.load_appdata)
        subtitle_dir = (subtitle_path.resolve().parent if subtitle_path is not None else Path.cwd()).resolve()
        self.paths.set_token("?script", str(subtitle_dir))
        aeg = read_aegisub_options(self.paths)
        self.save_ui_state = aeg.save_ui_state

        vopts = dataclasses.replace(video) if video is not None else VideoOptions()  # never the caller's object
        vopts.open_audio = aeg.open_audio
        self.video = build_video_context(file, self.paths, vopts)
        self.warnings.extend(self.video.warnings)

        wanted = str(script) if script is not None else settings.default_script
        script_path = resolve_script(wanted, self.paths, aeg.autoload)
        self.job = Job(
            script_path=script_path,
            include_dirs=[
                str(script_path.parent),
                *automation_dirs(self.paths, aeg.include),
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
        """Build the Lua state and load the script. A no-op once it is running."""
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

    def features(self) -> FeatureList:
        """The macros and export filters the script registers (the `list` command). Cached."""
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
        result = self._request("run")
        if result.ass is None:
            raise ScriptFailed(result.message)
        _adopt(self.ass, result.ass)
        return self.ass

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
