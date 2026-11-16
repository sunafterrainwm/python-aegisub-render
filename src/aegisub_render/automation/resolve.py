"""Finding out who provides an export filter, and remembering the answer.

Aegisub loads every autoload script at startup, so by the time the export dialog opens it knows
which script registers which filter. A headless run cannot afford that -- loading every script
means a Lua state per script -- so the order here is:

1. the built-in filters (`automation.base`), which cost nothing to look at;
2. the filters the script given with `--script` registers, if there is one;
3. the Python filters of the extra autoload directories (--autoload-dirs and friends), which
   cost an import and no Aegisub installation at all;
4. the Python filters of Aegisub's own autoload directories, which needs to know where the
   installation is but still only costs an import;
5. only if that failed, and only when scanning is allowed (`--scan-autoload`, off by default):
   load every autoload *Lua* script once and ask it, then remember the answer in a cache file.

Where the installation is comes either from the caller (`paths` and `autoload_option`, which
`render` already has) or from `env_factory`, called the first time a step actually needs it --
so `convert` only goes looking for Aegisub when a filter name is not something it can resolve
without one.

The cache is keyed on the autoload directories and on the size and modification time of every
script in them, so editing, adding or removing a script invalidates it by itself. On top of
that, a filter taken from the cache is checked against the script it names when that script is
loaded: if the name is not there any more the whole cache is thrown away and the scan runs
again. The cache only ever saves time, so losing it is never a correctness problem.
"""

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from aegisub_render.ass.file import AssFile
from aegisub_render.automation.base import AutomationError, ExportFilter, get_filter
from aegisub_render.automation.plugin import load_plugins, plugin_filters
from aegisub_render.automation.spec import FilterArgs
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths
from aegisub_render.settings import Settings, automation_dirs, extra_autoload_dirs
from aegisub_render.video.context import VideoOptions

CACHE_VERSION = 1
LUA_SUFFIXES = (".lua", ".moon")  # what a scan loads; .py is filters.plugin's business


def cache_dir() -> Path:
    """The same place the video probe cache lives."""
    return Path(tempfile.gettempdir()) / "aegisub-render"


def cache_path() -> Path:
    return cache_dir() / "export-filters.json"


@dataclass(slots=True)
class ResolvedFilter:
    """One entry of the chain, with whoever will run it."""

    args: FilterArgs
    builtin: type[ExportFilter] | None = None
    script: Path | None = None
    priority: int = 0
    description: str = ""

    @property
    def name(self) -> str:
        return self.args.name

    @property
    def is_builtin(self) -> bool:
        return self.builtin is not None


@dataclass(slots=True)
class _Scan:
    """What a scan found: filter name -> (script, priority, description)."""

    dirs: list[str] = field(default_factory=list[str])
    scripts: dict[str, list[int]] = field(default_factory=dict[str, list[int]])
    found: dict[str, tuple[str, int, str]] = field(default_factory=dict[str, tuple[str, int, str]])


def _script_files(dirs: list[str]) -> dict[str, list[int]]:
    """The Lua scripts of those directories with the stats the cache is keyed on."""
    from aegisub_render.script_render import automation_files

    out: dict[str, list[int]] = {}
    for path in automation_files(dirs, LUA_SUFFIXES):
        st = path.stat()
        out[str(path)] = [st.st_mtime_ns, st.st_size]
    return out


class FilterResolver:
    """Resolve filter names to the built-in class or the script that registers them."""

    def __init__(
        self,
        settings: Settings,
        paths: AegisubPaths | None = None,
        autoload_option: str | None = None,
        out: Output | None = None,
        scan: bool = False,
        use_cache: bool = True,
        env_factory: Callable[[], tuple[AegisubPaths, str]] | None = None,
    ) -> None:
        """`paths`/`autoload_option` say where Aegisub is; `env_factory` finds that out later."""
        self.settings = settings
        self.paths = paths
        self.autoload_option = autoload_option
        self.out = out
        self.scan_allowed = scan
        self.use_cache = use_cache
        self.env_factory = env_factory
        self._scan: _Scan | None = None
        self._script_cache: dict[Path, dict[str, tuple[int, str]]] = {}
        self._loaded_from: set[str] = set()

    def aegisub(self) -> tuple[AegisubPaths, str]:
        """Where Aegisub is, asking env_factory for it the first time something needs it."""
        if self.paths is None or self.autoload_option is None:
            if self.env_factory is None:
                raise AutomationError("this needs an Aegisub installation and there is none to use")
            self.paths, self.autoload_option = self.env_factory()
        return self.paths, self.autoload_option

    def extra_dirs(self) -> list[str]:
        """The extra autoload directories, which are known without an Aegisub installation."""
        prepend, append = extra_autoload_dirs(self.settings)
        return [str(d) for d in (*prepend, *append) if d.is_dir()]

    def autoload_dirs(self) -> list[str]:
        """Every autoload directory, Aegisub's own included (so it has to know where it is)."""
        paths, option = self.aegisub()
        return [str(Path(d)) for d in automation_dirs(paths, option)]

    def load_python_plugins(self, dirs: list[str]) -> None:
        """Import the .py filters of those directories (see filters.plugin). Once each."""
        fresh = [d for d in dirs if d.casefold() not in self._loaded_from]
        self._loaded_from.update(d.casefold() for d in dirs)
        if not fresh:
            return

        def report(path: Path, error: BaseException) -> None:
            if self.out is not None:
                self.out.message(f"aegisub-render: could not load the filter plugin {path.name}: {error}")

        load_plugins(fresh, report)

    # -- asking a script what it registers -----------------------------------------------------

    def script_filters(self, script: Path) -> dict[str, tuple[int, str]]:
        """The export filters a script registers: name -> (priority, description). Cached.

        A .py is imported (no Lua state); a .lua or .moon is loaded and asked, which is what
        makes scanning expensive enough to be opt-in.
        """
        known = self._script_cache.get(script)
        if known is not None:
            return known
        from aegisub_render.script_render import PYTHON_SUFFIXES, RenderError, ScriptError, ScriptRender

        found: dict[str, tuple[int, str]] = {}
        if script.suffix.lower() in PYTHON_SUFFIXES:
            try:
                for cls in plugin_filters(script):
                    found[cls.name] = (cls.priority, cls.description)
            except BaseException as e:  # the file's top level can raise anything at all
                if self.out is not None:
                    self.out.message(f"aegisub-render: could not load the filter plugin {script.name}: {e}")
            self._script_cache[script] = found
            return found
        try:
            with ScriptRender(
                AssFile(),
                script,
                self.settings,
                video=VideoOptions(no_video=True),
                quiet=True,
                progress=False,
                out=self.out,
            ) as sr:
                for name, description, priority in sr.features().filters:
                    found[name] = (priority, description)
        except (RenderError, ScriptError) as e:
            if self.out is not None:
                self.out.message(f"aegisub-render: could not load {script.name} while looking for filters: {e}")
        self._script_cache[script] = found
        return found

    # -- the autoload scan and its cache -------------------------------------------------------

    def _load_cache(self, dirs: list[str], scripts: dict[str, list[int]]) -> _Scan | None:
        if not self.use_cache:
            return None
        try:
            raw: object = json.loads(cache_path().read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        if not isinstance(raw, dict):
            return None
        data = cast("dict[str, object]", raw)
        if data.get("version") != CACHE_VERSION:
            return None
        if data.get("dirs") != dirs or data.get("scripts") != scripts:
            return None  # a script was edited, added or removed: scan again
        found = data.get("filters")
        if not isinstance(found, dict):
            return None
        entries: dict[str, tuple[str, int, str]] = {}
        for key, value in cast("dict[str, object]", found).items():
            if not isinstance(value, list):
                return None
            entry = cast("list[object]", value)
            if len(entry) != 3:
                return None
            script, priority, description = entry
            if not isinstance(script, str) or not isinstance(priority, int) or not isinstance(description, str):
                return None
            entries[key] = (script, priority, description)
        return _Scan(dirs, scripts, entries)

    def _save_cache(self, scan: _Scan) -> None:
        if not self.use_cache:
            return
        payload = {
            "version": CACHE_VERSION,
            "dirs": scan.dirs,
            "scripts": scan.scripts,
            "filters": {name: [script, priority, desc] for name, (script, priority, desc) in scan.found.items()},
        }
        try:
            cache_dir().mkdir(parents=True, exist_ok=True)
            tmp = cache_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache_path())
        except OSError:
            pass  # the cache is an optimisation; failing to write it changes nothing

    def forget_cache(self) -> None:
        """Throw the cache away (a cached name turned out not to be registered any more)."""
        self._scan = None
        try:
            cache_path().unlink(missing_ok=True)
        except OSError:
            pass

    def scan(self, force: bool = False) -> _Scan:
        """Every filter the autoload scripts register, from the cache or by loading them all."""
        if self._scan is not None and not force:
            return self._scan
        dirs = self.autoload_dirs()
        scripts = _script_files(dirs)
        if not force:
            cached = self._load_cache(dirs, scripts)
            if cached is not None:
                self._scan = cached
                return cached
        if self.out is not None:
            self.out.message(f"aegisub-render: looking for export filters in {len(scripts)} autoload scripts...")
        found: dict[str, tuple[str, int, str]] = {}
        for name in scripts:
            path = Path(name)
            for filter_name, (priority, description) in self.script_filters(path).items():
                found.setdefault(filter_name, (name, priority, description))
        scan = _Scan(dirs, scripts, found)
        self._scan = scan
        self._save_cache(scan)
        return scan

    # -- the resolution itself -----------------------------------------------------------------

    def resolve(self, chain: list[FilterArgs], script: Path | None = None) -> list[ResolvedFilter]:
        """Resolve a whole chain, keeping the order it was written in."""
        return [self.resolve_one(args, script) for args in chain]

    def resolve_one(self, args: FilterArgs, script: Path | None = None) -> ResolvedFilter:
        builtin = get_filter(args.name)
        if builtin is not None:
            return ResolvedFilter(args, builtin=builtin, priority=builtin.priority, description=builtin.description)

        if script is not None:
            registered = self.script_filters(script).get(args.name)
            if registered is not None:
                return ResolvedFilter(args, script=script, priority=registered[0], description=registered[1])

        # a .py in an autoload directory costs an import, not a Lua state, so it is always
        # worth looking at; what it registers is an ExportFilter like any built-in one. The
        # extra directories are known without an Aegisub installation, so they come first.
        for dirs in (self.extra_dirs, self.autoload_dirs):
            try:
                self.load_python_plugins(dirs())
            except AutomationError:
                break  # no installation to look in; the message below says what to do
            plugin = get_filter(args.name)
            if plugin is not None:
                return ResolvedFilter(args, builtin=plugin, priority=plugin.priority, description=plugin.description)

        if not self.scan_allowed:
            raise AutomationError(
                f"no export filter named {args.name!r}"
                + (f" in {script.name}" if script is not None else "")
                + f". Built-in: {self.builtin_names()}. Pass --scan-autoload to look through the "
                "autoload directories for the Lua script that registers it (the answer is cached), "
                "or put a Python filter of that name in one of them (--autoload-dirs)."
            )

        entry = self.scan().found.get(args.name)
        if entry is not None:
            path = Path(entry[0])
            # the cache said this script registers it: make sure it still does
            if args.name in self.script_filters(path):
                return ResolvedFilter(args, script=path, priority=entry[1], description=entry[2])
            self.forget_cache()
            entry = self.scan(force=True).found.get(args.name)
            if entry is not None:
                return ResolvedFilter(args, script=Path(entry[0]), priority=entry[1], description=entry[2])

        available = ", ".join(sorted(self.scan().found)) or "(none)"
        raise AutomationError(
            f"no export filter named {args.name!r}. Built-in: {self.builtin_names()}; "
            f"registered by the autoload scripts: {available}"
        )

    @staticmethod
    def builtin_names() -> str:
        from aegisub_render.automation.base import filters

        return ", ".join(repr(f.name) for f in filters()) or "(none)"


__all__ = [
    "CACHE_VERSION",
    "LUA_SUFFIXES",
    "FilterResolver",
    "ResolvedFilter",
    "cache_path",
]
