"""Run Aegisub 3.5.1 Automation 4 scripts (kara-templater and friends) headlessly."""

import os as _os
from pathlib import Path as _Path

# Modules built with native/build.py --msys2 load DLLs from an MSYS2 environment, which Windows does
# not look for on PATH: build.py names its directory here (the file only exists after such a build).
if hasattr(_os, "add_dll_directory") and (_dlls := _Path(__file__).with_name("_dll_dirs.txt")).is_file():
    for _line in _dlls.read_text(encoding="utf-8").splitlines():
        if _line.strip() and _Path(_line.strip()).is_dir():
            _os.add_dll_directory(_line.strip())

from aegisub_render.macro import (
    CharMetrics,
    LayoutSpec,
    LinePosition,
    MarginLayout,
    MeasureError,
    MeasureResult,
    MeasureStyle,
    Offset,
    PosLayout,
    measure,
    measure_text,
    offset,
    parse_offset,
    resolve_style_from_ass,
    shift_time_by_frames,
)
from aegisub_render.runner import ExitCode, RenderOptions, list_features, render
from aegisub_render.settings import Settings, load_settings
from aegisub_render.video.context import VideoOptions

__all__ = [
    "CharMetrics",
    "ExitCode",
    "LayoutSpec",
    "LinePosition",
    "MarginLayout",
    "MeasureError",
    "MeasureResult",
    "MeasureStyle",
    "Offset",
    "PosLayout",
    "RenderOptions",
    "Settings",
    "VideoOptions",
    "list_features",
    "load_settings",
    "measure",
    "measure_text",
    "offset",
    "parse_offset",
    "render",
    "resolve_style_from_ass",
    "shift_time_by_frames",
]
