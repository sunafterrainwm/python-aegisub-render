"""Run Aegisub 3.5.1 Automation 4 scripts (kara-templater and friends) headlessly."""

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
