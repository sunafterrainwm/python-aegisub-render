"""Built-in headless macro operations (offset, measure) that run without starting Aegisub."""

import argparse
import importlib
import pkgutil

from aegisub_render.cli import CommandRegistry
from aegisub_render.cli import register_command as _register_top_command
from aegisub_render.runner import ExitCode
from aegisub_render.settings import Settings

_MACRO_COMMANDS = CommandRegistry()
register_command = _MACRO_COMMANDS.register_command

for _mod_name in ("offset", "measure", *(m.name for m in pkgutil.iter_modules(__path__))):
    if not _mod_name.startswith("_"):
        importlib.import_module(f"{__name__}.{_mod_name}")

from aegisub_render.macro.measure import (  # noqa: E402
    CharMetrics,
    LayoutSpec,
    LinePosition,
    MarginLayout,
    MeasureError,
    MeasureResult,
    MeasureStyle,
    PosLayout,
    measure,
    measure_text,
    resolve_style_from_ass,
)
from aegisub_render.macro.offset import (  # noqa: E402
    Offset,
    offset,
    parse_offset,
    shift_time_by_frames,
)


def _macro_help() -> str:
    names = ", ".join(_MACRO_COMMANDS.names())
    return f"run built-in headless macro operations without starting Aegisub ({names})"


def _configure_macro_parser(p: argparse.ArgumentParser) -> None:
    p.description = "Run built-in headless macro operations without starting Aegisub."
    _MACRO_COMMANDS.populate_subparsers(p, dest="macro_command", required=False)
    p.set_defaults(_macro_parser=p)


@_register_top_command(
    "macro",
    help=_macro_help,
    configure=_configure_macro_parser,
)
def _run_macro(args: argparse.Namespace, _settings: Settings) -> ExitCode:
    parser: argparse.ArgumentParser = args._macro_parser
    parser.print_help()
    return ExitCode.OK


__all__ = [
    "CharMetrics",
    "LayoutSpec",
    "LinePosition",
    "MarginLayout",
    "MeasureError",
    "MeasureResult",
    "MeasureStyle",
    "Offset",
    "PosLayout",
    "measure",
    "measure_text",
    "offset",
    "parse_offset",
    "register_command",
    "resolve_style_from_ass",
    "shift_time_by_frames",
]
