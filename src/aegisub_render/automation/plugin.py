"""Automation files written in Python: the macros and export filters a `.py` registers.

A Lua export filter cannot be given options: Aegisub asks it for a configuration dialog, and a
headless run has nothing to drive one with. Upstream has the same hole, so rather than invent a
way to pass options to Lua, a filter that needs options is written in Python -- either built
into this package (`automation.fixstyle`, `automation.framerate`) or as a `.py` automation file in one
of the autoload directories, which this module loads. Such a file registers macros and export
filters the way a Lua script calls `aegisub.register_macro` and `aegisub.register_filter`:

    # a .py in any autoload directory (--autoload-dirs, or Aegisub's own)
    from typing import ClassVar, override
    from aegisub_render import AssFile
    from aegisub_render.automation import ExportFilter, Macro, AutomationContext, register

    class DropSigns(Macro, ExportFilter):   # either one on its own is fine too
        name: ClassVar[str] = "Drop Signs"
        priority: ClassVar[int] = 0
        positional: ClassVar[tuple[str, ...]] = ("prefix",)

        def __init__(self, prefix: str = "Sign") -> None:
            self.prefix = prefix

        @override
        def process_macro(self, file, selected_lines, active_line, ctx) -> None:
            self.process_filter(file, ctx)

        @override
        def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
            file.events = [e for e in file.events if not e.style.startswith(self.prefix)]

    register(DropSigns)

`--export-filters "Drop Signs=OP"` then finds it, and `--script` that file runs its macro.
Aegisub itself ignores a `.py` in its own autoload directories (it only loads .lua and .moon),
so the two can live side by side.

Unlike a Lua script, importing one of these costs nothing but an import, so they are loaded
whenever a filter name is not already registered -- `--scan-autoload` is about the Lua scripts
and is not needed for these. The flip side is that a .py file in those directories is imported
and its top level runs; they are the same directories whose Lua is already being executed, but
it is worth knowing.
"""

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

from aegisub_render.automation.base import ExportFilter, Macro, filters, macros

#: files already imported in this process, by resolved path and the mtime they had
_loaded: dict[Path, int] = {}
#: the module name each of them was imported as
_module_names: dict[Path, str] = {}


def python_files(dirs: list[str]) -> list[Path]:
    """The .py files of the given directories, listed the way every automation search lists."""
    from aegisub_render.script_render import PYTHON_SUFFIXES, automation_files

    return automation_files(dirs, PYTHON_SUFFIXES)


def load_plugin(path: Path) -> list[type[ExportFilter] | type[Macro]]:
    """Import one .py and return the filters it registered (nothing if it was already loaded)."""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    if _loaded.get(path) == mtime:
        return []

    before: set[type[ExportFilter] | type[Macro]] = {*filters(), *macros()}
    # outside the package, so that automation.base.is_builtin() tells the two apart
    module_name = "aegisub_render_plugins." + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    _loaded[path] = mtime
    _module_names[path] = module_name
    return [f for f in (*filters(), *macros()) if f not in before]


def plugin_filters(path: Path) -> list[type[ExportFilter]]:
    """The export filters an already-imported (or now imported) automation file provides.

    Unlike `load_plugin` this answers the same thing however often it is asked: what was
    registered from that file's module, which is what `--script` and the autoload search report.
    Matching on the module is what keeps a built-in out of the list if a plugin imports one.
    """
    load_plugin(path)
    module = _module_names.get(path)
    return [f for f in filters() if f.__module__ == module]


def plugin_macros(path: Path) -> list[type[Macro]]:
    """The macros an already-imported (or now imported) automation file provides."""
    load_plugin(path)
    module = _module_names.get(path)
    return [m for m in macros() if m.__module__ == module]


def load_plugins(
    dirs: list[str],
    on_error: Callable[[Path, BaseException], None] | None = None,
) -> list[type[ExportFilter] | type[Macro]]:
    """Import every .py of those directories; a file that raises is reported, not fatal."""
    registered: list[type[ExportFilter] | type[Macro]] = []
    for path in python_files(dirs):
        try:
            registered.extend(load_plugin(path))
        except BaseException as e:  # a plugin's top level can raise anything at all
            if on_error is not None:
                on_error(path, e)
    return registered


__all__ = ["load_plugin", "load_plugins", "plugin_filters", "plugin_macros", "python_files"]
