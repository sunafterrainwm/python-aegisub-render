"""Automation: the two things a script can provide, and everything that runs them.

Aegisub's word for this is Automation (`namespace Automation4`): a script registers **macros**
(`aegisub.register_macro`) and **export filters** (`aegisub.register_filter`), and that pair is
what this package is about -- the `Macro` and `ExportFilter` base classes, the registries, the
built-in ones, the .py automation files that bring their own, and the resolution of a name to
whoever provides it. The Lua side of the same pair is `host/` and `worker.py`; this is the
Python side plus the dispatch both go through.

The export filter half is the bigger one, because it has a command line to parse. Aegisub's
File > Export runs a list of filters over a copy of the script before writing it. Two of them
are built into Aegisub itself (`Fix Styles`, `Transform Framerate`), one more is this project's
(`Shift Times`); the rest -- the `Karaoke template` of kara-templater, `Clean Tags`, whatever
else is in the autoload directories -- are Lua scripts, which this project runs through
ScriptRender. `--export-filters` takes both, in ffmpeg's filtergraph syntax so that a filter can
be given options:

    aegisub-render render in.ass -o out.ass --export-filters "Transform Framerate=input=23.976:output=25"
    aegisub-render render in.ass -o out.ass --script kara-templater.lua \
        --export-filters "Karaoke template,Fix Styles"

The filters run in the order they are written, which is what Aegisub's export dialog does as
well (its list starts in priority order, and the Move Up / Move Down buttons change it).
`filters()` lists the built-in ones in that starting order.

Only Python filters take options: a Lua filter's settings come from a dialog of its own, which
there is nothing to drive headlessly (Aegisub has the same hole), so options for one are an
error. A filter that needs options is written in Python instead -- here, or as a .py file in the
autoload directories, which `automation.plugin` loads.
"""

from aegisub_render.automation.base import (
    AutomationContext,
    AutomationError,
    ExportFilter,
    Macro,
    filters,
    get_filter,
    get_macro,
    is_builtin,
    macros,
    parse_bool,
    parse_fps,
    register,
    register_filter,
    register_macro,
    unregister,
    unregister_filter,
    unregister_macro,
)
from aegisub_render.automation.fixstyle import FixStylesFilter
from aegisub_render.automation.framerate import TransformFramerateFilter, trunc_cs
from aegisub_render.automation.plugin import (
    load_plugin,
    load_plugins,
    plugin_filters,
    plugin_macros,
    python_files,
)
from aegisub_render.automation.resolve import FilterResolver, ResolvedFilter, cache_path
from aegisub_render.automation.spec import FilterArgs, FilterSpecError, escape, parse_filter_chain

# main.cpp's two AssExportFilterChain::Register calls
register_filter(TransformFramerateFilter)
register_filter(FixStylesFilter)

__all__ = [
    "AutomationContext",
    "AutomationError",
    "ExportFilter",
    "FilterArgs",
    "FilterResolver",
    "FilterSpecError",
    "FixStylesFilter",
    "Macro",
    "ResolvedFilter",
    "TransformFramerateFilter",
    "cache_path",
    "escape",
    "filters",
    "get_filter",
    "get_macro",
    "is_builtin",
    "load_plugin",
    "load_plugins",
    "macros",
    "parse_bool",
    "parse_filter_chain",
    "parse_fps",
    "plugin_filters",
    "plugin_macros",
    "python_files",
    "register",
    "register_filter",
    "register_macro",
    "trunc_cs",
    "unregister",
    "unregister_filter",
    "unregister_macro",
]
