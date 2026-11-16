"""Macro and ExportFilter: what `aegisub.register_macro` and `aegisub.register_filter` are here.

A port of src/ass_export_filter.{h,cpp} and the non-GUI half of AssExporter: a filter has a
name, a description and a priority, and `process()` changes the file in place. The registry is
AssExportFilterChain -- `register_filter` inserts before the first filter of a lower priority,
so `filters()` comes back in the order Aegisub's export dialog lists them.

Aegisub's other export filters are Lua (`aegisub.register_filter`), which this project already
runs through ScriptRender; `filters.chain` resolves a name to whichever of the two it is.

Where Aegisub reads a filter's settings from its page of the export dialog, here they come from
the command line, in ffmpeg's filtergraph syntax (see `automation.spec`): a filter declares the
options it takes by the keyword arguments of its `__init__`, and `positional` names the ones
that may also be given by position.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Self

from aegisub_render.ass.file import AssFile
from aegisub_render.automation.spec import FilterArgs, FilterSpecError
from aegisub_render.output import Output
from aegisub_render.video.vfr import Framerate


class AutomationError(Exception):
    """A macro or filter cannot run as asked (a bad option, a missing frame rate)."""


@dataclass(slots=True)
class AutomationContext:
    """What a macro or a filter may need besides the file itself.

    `fps` is the project's timecodes (the video's, or --fps / --timecodes), which is what
    Aegisub's Transform Framerate starts from.
    """

    fps: Framerate = field(default_factory=Framerate)
    subtitle_path: Path | None = None
    out: Output | None = None

    def message(self, text: str) -> None:
        if self.out is not None:
            self.out.message(text)


class Macro(ABC):
    """One macro: what `aegisub.register_macro` registers, in Python.

    Subclass it and `register_macro(YourMacro)`, or put it in a .py automation file (see
    `automation.plugin`), which registers it when the file is loaded::

        class Shift(Macro):
            name: ClassVar[str] = "Shift by a frame"

            @override
            def process_macro(self, file, selected_lines, active_line, ctx) -> None:
                ...

    `selected_lines` and `active_line` are what Aegisub hands a macro: the 1-based indices of
    the selected subtitle lines, and the active one. This tool has no selection of its own yet,
    so they are always `[]` and `0` -- the arguments are there because the signature is the
    contract, not because they carry anything today.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    @abstractmethod
    def process_macro(
        self,
        file: AssFile,
        selected_lines: list[int],
        active_line: int,
        ctx: AutomationContext,
    ) -> None:
        """The macro's processing function: change `file` in place."""

    def validate_macro(
        self,
        file: AssFile,
        selected_lines: list[int],
        active_line: int,
        ctx: AutomationContext,
    ) -> bool:
        """The macro's validation function: whether Aegisub would enable its menu item.

        True unless overridden, which is what a macro without a validation function does.
        """
        del file, selected_lines, active_line, ctx
        return True


class ExportFilter(ABC):
    """One export filter: what `aegisub.register_filter` registers, in Python.

    Subclass it, then `register_filter(YourFilter)`. `__init__` takes the filter's options as
    keyword arguments (strings, as they were written on the command line) -- `from_args` turns a
    parsed `name=a:b=c` into that call.

    A class may be both, exactly as one Lua script may call both register functions::

        class MyCoolTool(Macro, ExportFilter):
            name: ClassVar[str] = "My Cool Tool"

            def __init__(self, strength: str = "1") -> None:  # the filter's options
                self.strength = int(strength)

            @override
            def process_macro(self, file, selected_lines, active_line, ctx) -> None:
                self.process_filter(file, ctx)

            @override
            def process_filter(self, file, ctx) -> None:
                ...

        register(MyCoolTool)   # both registries

    The macro path builds it with no arguments, so every option needs a default.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    priority: ClassVar[int] = 0
    #: option names that may also be given by position, in order (ffmpeg's positional arguments)
    positional: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        """AssExportFilter::ProcessSubs: change `file` in place."""

    @classmethod
    def from_args(cls, args: FilterArgs) -> Self:
        """Build the filter from a parsed filtergraph entry."""
        options = dict(args.options)
        if len(args.positional) > len(cls.positional):
            raise AutomationError(
                f"{cls.name}: {len(args.positional)} positional options given, but it takes "
                f"{len(cls.positional) or 'none'}" + (f" ({', '.join(cls.positional)})" if cls.positional else "")
            )
        for key, value in zip(cls.positional, args.positional, strict=False):
            if key in options:
                raise AutomationError(f"{cls.name}: option {key!r} given both by position and by name")
            options[key] = value
        try:
            return cls(**options)
        except TypeError as e:
            raise AutomationError(f"{cls.name}: {e}") from None


#: this package: what is defined inside it is built in (`automation.fixstyle`, `tools.offset`...).
#: A .py automation file is imported under a name of its own (filters.plugin), so it is not.
_BUILTIN_PACKAGE = __name__.split(".", 1)[0] + "."


def is_builtin(cls: type[Macro] | type[ExportFilter]) -> bool:
    """Whether this is one of the macros or filters built into this package.

    The others come from a .py automation file or from the caller's own code, which is what
    `--script` and the autoload search report as belonging to that file.
    """
    return cls.__module__.startswith(_BUILTIN_PACKAGE)


_MACROS: list[type[Macro]] = []
_FILTERS: list[type[ExportFilter]] = []


def register_macro(macro_cls: type[Macro]) -> None:
    """Add a macro to the registry (aegisub.register_macro).

    Unlike Aegisub a clashing name is an error rather than a rename: a clash here is a bug, not
    two scripts picking the same name. Registering the same class twice does nothing.
    """
    if macro_cls in _MACROS:
        return
    clash = next((m for m in _MACROS if m.name == macro_cls.name), None)
    if clash is not None:
        raise ValueError(f"a macro named {macro_cls.name!r} is already registered ({clash!r})")
    _MACROS.append(macro_cls)


def register_filter(filter_cls: type[ExportFilter]) -> None:
    """AssExportFilterChain::Register: insert before the first filter of a lower priority.

    Unlike Aegisub a clashing name is an error rather than a rename: a clash here is a bug, not
    two scripts picking the same name. Registering the same class twice moves it to its place
    rather than duplicating it.
    """
    unregister_filter(filter_cls)
    clash = next((f for f in _FILTERS if f.name == filter_cls.name), None)
    if clash is not None:
        raise ValueError(f"an export filter named {filter_cls.name!r} is already registered ({clash!r})")
    index = next((i for i, f in enumerate(_FILTERS) if f.priority < filter_cls.priority), len(_FILTERS))
    _FILTERS.insert(index, filter_cls)


def register(cls: type[Macro] | type[ExportFilter]) -> None:
    """Register a class as whatever it is -- a macro, an export filter, or both."""
    both = False
    if issubclass(cls, Macro):
        register_macro(cls)
        both = True
    if issubclass(cls, ExportFilter):
        register_filter(cls)
        both = True
    if not both:
        raise TypeError(f"{cls.__name__} is neither a Macro nor an ExportFilter")


def unregister_macro(macro_cls: type[Macro]) -> None:
    """Remove a macro from the registry (a no-op if it is not in it)."""
    if macro_cls in _MACROS:
        _MACROS.remove(macro_cls)


def unregister(cls: type[Macro] | type[ExportFilter]) -> None:
    """Remove a class from both registries."""
    if issubclass(cls, Macro):
        unregister_macro(cls)
    if issubclass(cls, ExportFilter):
        unregister_filter(cls)


def macros() -> tuple[type[Macro], ...]:
    """The registered macros, in the order they were registered."""
    return tuple(_MACROS)


def get_macro(name: str) -> type[Macro] | None:
    """The registered macro with that exact name, if any."""
    return next((m for m in _MACROS if m.name == name), None)


def unregister_filter(filter_cls: type[ExportFilter]) -> None:
    """Remove a filter from the registry (a no-op if it is not in it)."""
    if filter_cls in _FILTERS:
        _FILTERS.remove(filter_cls)


def filters() -> tuple[type[ExportFilter], ...]:
    """The registered filters, highest priority first (the export dialog's order)."""
    return tuple(_FILTERS)


def get_filter(name: str) -> type[ExportFilter] | None:
    """AssExportFilterChain::GetFilter: the built-in filter with that exact name, if any."""
    return next((f for f in _FILTERS if f.name == name), None)


def parse_bool(value: str, option: str) -> bool:
    """An on/off option as written on the command line ("1", "true", "yes", "on", and friends)."""
    low = value.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("", "0", "false", "no", "off"):
        return False
    raise AutomationError(f"{option}: expected a yes/no value, not {value!r}")


def parse_fps(value: str, option: str) -> Framerate:
    """A frame rate option: "23.976", "24000/1001", or a v1/v2 timecodes file."""
    from aegisub_render.video.context import parse_framerate
    from aegisub_render.video.vfr import FramerateError

    text = value.strip()
    path = Path(text)
    try:
        if path.is_file():
            return Framerate.from_file(path)
        return parse_framerate(text)
    except (FramerateError, OSError) as e:
        raise AutomationError(f"{option}: {e}") from None


__all__ = [
    "AutomationContext",
    "AutomationError",
    "ExportFilter",
    "FilterArgs",
    "FilterSpecError",
    "Macro",
    "filters",
    "get_filter",
    "get_macro",
    "is_builtin",
    "macros",
    "parse_bool",
    "parse_fps",
    "register",
    "register_filter",
    "register_macro",
    "unregister",
    "unregister_filter",
    "unregister_macro",
]
