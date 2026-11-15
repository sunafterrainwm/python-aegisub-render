"""Command line interface. Options given here override config.py."""

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

from aegisub_render.output import Output, use_utf8_for_pipes
from aegisub_render.runner import STDOUT, ExitCode, RenderOptions, list_features, render
from aegisub_render.settings import ConfigError, Settings, load_settings
from aegisub_render.video.context import VideoOptions
from aegisub_render.wsl import translate, translate_path

NEGATIVE_NUMBER_RE = re.compile(r"^-[\d.]")

type CommandHandler = Callable[[argparse.Namespace, Settings], ExitCode]
type ParserConfigurator = Callable[[argparse.ArgumentParser], object]
type HelpSource = str | Callable[[], str]


@dataclass(slots=True, frozen=True)
class CommandSpec:
    name: str
    help: HelpSource
    configure: ParserConfigurator
    run: CommandHandler

    def resolve_help(self) -> str:
        return self.help() if callable(self.help) else self.help


class CommandRegistry:
    """Registry of CLI (sub)commands mapped to their parser configurators and handlers."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    @overload
    def register_command(
        self,
        name: str,
        help: HelpSource,
        configure: ParserConfigurator,
        run: CommandHandler,
    ) -> CommandSpec: ...

    @overload
    def register_command(
        self,
        name: str,
        help: HelpSource,
        configure: ParserConfigurator,
        run: None = None,
    ) -> Callable[[CommandHandler], CommandHandler]: ...

    def register_command(
        self,
        name: str,
        help: HelpSource,
        configure: ParserConfigurator,
        run: CommandHandler | None = None,
    ) -> CommandSpec | Callable[[CommandHandler], CommandHandler]:
        if run is not None:
            spec = CommandSpec(name=name, help=help, configure=configure, run=run)
            self._commands[name] = spec
            return spec

        def decorator(fn: CommandHandler) -> CommandHandler:
            self._commands[name] = CommandSpec(name=name, help=help, configure=configure, run=fn)
            return fn

        return decorator

    def names(self) -> tuple[str, ...]:
        return tuple(self._commands)

    def commands(self) -> tuple[CommandSpec, ...]:
        return tuple(self._commands.values())

    def populate_subparsers(
        self,
        parser: argparse.ArgumentParser,
        *,
        dest: str,
        required: bool = True,
    ) -> None:
        sub = parser.add_subparsers(dest=dest, required=required)
        for spec in self._commands.values():
            sp = sub.add_parser(spec.name, help=spec.resolve_help())
            sp._negative_number_matcher = NEGATIVE_NUMBER_RE
            spec.configure(sp)
            sp.set_defaults(handler=spec.run)


_COMMANDS = CommandRegistry()
register_command = _COMMANDS.register_command


def parse_selection(spec: str) -> list[int]:
    """'1,3-5' -> [1, 3, 4, 5] (1-based dialogue line numbers)."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        lo, sep, hi = part.partition("-")
        try:
            if sep:
                a, b = int(lo), int(hi)
                if b < a:
                    raise ValueError
                out.extend(range(a, b + 1))
            else:
                out.append(int(lo))
        except ValueError:
            raise argparse.ArgumentTypeError(f"bad selection {part!r}") from None
    return out


def parse_size(spec: str) -> tuple[int, int]:
    w, sep, h = spec.lower().partition("x")
    try:
        if not sep or int(w) <= 0 or int(h) <= 0:
            raise ValueError
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad size {spec!r} (expected WIDTHxHEIGHT)") from None


def parse_output(spec: str) -> Path | Literal["-"]:
    """'-' is stdout; a file named '-' can still be given as './-'."""
    return STDOUT if spec == STDOUT else translate_path(spec)


def add_base_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", type=translate_path, help="subtitle file (.ass/.ssa)")
    p.add_argument(
        "--config",
        type=translate_path,
        help="config file (default: AEGISUB_RENDER_CONFIG or the repository's config.py)",
    )
    p.add_argument("--aegisub-root", type=translate_path, help="Aegisub installation directory (?data)")
    appdata = p.add_mutually_exclusive_group()
    appdata.add_argument(
        "--load-appdata",
        dest="load_appdata",
        action="store_true",
        default=None,
        help="use %%APPDATA%%\\Aegisub as ?user",
    )
    appdata.add_argument(
        "--no-load-appdata",
        dest="load_appdata",
        action="store_false",
        help="portable mode: ?user is the Aegisub directory",
    )


def add_video_args(p: argparse.ArgumentParser) -> None:
    video = p.add_argument_group("video")
    v = video.add_mutually_exclusive_group()
    v.add_argument(
        "--video", type=translate, help="video to load instead of the project's Video File (also ?dummy:...)"
    )
    v.add_argument("--no-video", action="store_true", help="behave as if no video were loaded")
    video.add_argument("--video-size", type=parse_size, metavar="WxH", help="override the video frame size")
    tc = video.add_mutually_exclusive_group()
    tc.add_argument("--fps", help="constant frame rate (e.g. 23.976 or 24000/1001)")
    tc.add_argument("--timecodes", type=translate, help="timecodes file (v1 or v2)")
    video.add_argument("--keyframes", type=translate, help="keyframes file")
    video.add_argument("--refresh-cache", action="store_true", help="re-probe the video instead of using the cache")
    video.add_argument("--no-cache", action="store_true", help="neither read nor write the video cache")


def add_override_args(p: argparse.ArgumentParser) -> None:
    override = p.add_mutually_exclusive_group()
    override.add_argument(
        "-y",
        "--override",
        dest="override",
        action="store_const",
        const=True,
        default=None,
        help="replace the --output file if it exists without asking",
    )
    override.add_argument(
        "-n",
        "--no-override",
        dest="override",
        action="store_const",
        const=False,
        help="stop with an error if the --output file exists (default: ask)",
    )


def add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-o",
        "--output",
        type=parse_output,
        help="output .ass file, or - for stdout (default: replace the input file)",
    )
    add_override_args(p)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="write nothing; print how the result differs from the input to stderr "
        "(lines starting with ';' are ignored)",
    )
    p.add_argument(
        "--keep-brand",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="keep the input's header comments ('; Script generated by ...') as they are; by default "
        "' with aegisub-render <ver>' is appended to an Aegisub header and any other header is replaced",
    )


def add_common_args(p: argparse.ArgumentParser) -> None:
    add_base_args(p)
    p.add_argument("--script", type=translate, help="script path, or file name looked up in the autoload directories")
    p.add_argument("--timeout", type=float, help="wall-clock limit in seconds")
    p.add_argument("--memory-limit", type=int, help="Lua heap limit in bytes")
    p.add_argument("--max-output-lines", type=int, help="limit on lines produced by the script")
    p.add_argument("--max-input-lines", type=int, help="limit on lines in the input file")
    p.add_argument(
        "--font-dir", type=translate_path, action="append", default=[], help="extra font directory (repeatable)"
    )
    p.add_argument("--trace-level", type=int, choices=range(6), help="aegisub.debug.out threshold (0-5)")
    p.add_argument("-v", "--verbose", action="count", default=0, help="raise the trace level by one per -v")
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors and script output")
    p.add_argument("--no-progress", action="store_true", help="never draw the progress line")
    add_video_args(p)


def load_cli_settings(args: argparse.Namespace) -> Settings:
    s = load_settings(getattr(args, "config", None))
    aegisub_root = getattr(args, "aegisub_root", None)
    if aegisub_root is not None:
        s.aegisub_root = aegisub_root
    load_appdata = getattr(args, "load_appdata", None)
    if load_appdata is not None:
        s.load_appdata = load_appdata
    timeout = getattr(args, "timeout", None)
    if timeout is not None:
        s.timeout_seconds = timeout
    memory_limit = getattr(args, "memory_limit", None)
    if memory_limit is not None:
        s.memory_limit_bytes = memory_limit
    max_output_lines = getattr(args, "max_output_lines", None)
    if max_output_lines is not None:
        s.max_output_lines = max_output_lines
    max_input_lines = getattr(args, "max_input_lines", None)
    if max_input_lines is not None:
        s.max_input_lines = max_input_lines
    font_dir = getattr(args, "font_dir", None)
    if font_dir:
        s.font_dirs = [*s.font_dirs, *font_dir]
    return s


def build_render_options(args: argparse.Namespace, settings: Settings) -> RenderOptions:
    trace_level = getattr(args, "trace_level", None)
    verbose = getattr(args, "verbose", 0)
    trace = trace_level if trace_level is not None else settings.trace_level
    trace = min(5, trace + verbose)
    return RenderOptions(
        script=getattr(args, "script", None),
        macro=getattr(args, "macro", None),
        filter=getattr(args, "filter", None),
        selection=getattr(args, "select", None),
        force=getattr(args, "force", False),
        video=VideoOptions(
            video=args.video,
            no_video=args.no_video,
            video_size=args.video_size,
            fps=args.fps,
            timecodes=args.timecodes,
            keyframes=args.keyframes,
            use_cache=not args.no_cache,
            refresh_cache=args.refresh_cache,
        ),
        trace_level=trace,
        quiet=args.quiet,
        progress=False if getattr(args, "no_progress", False) else None,
        dry_run=getattr(args, "dry_run", False),
        keep_brand=getattr(args, "keep_brand", False),
    )


def may_replace(path: Path, override: bool | None) -> bool:
    """Whether an existing --output may be replaced: -y, -n, or ask (no when stdin is not a terminal)."""
    if override is not None:
        if not override:
            print(f"aegisub-render: {path} already exists; not replacing it (--no-override)", file=sys.stderr)
        return override
    if not sys.stdin.isatty():
        print(f"aegisub-render: {path} already exists; pass -y to replace it", file=sys.stderr)
        return False
    print(f"aegisub-render: {path} already exists. Replace it? [y/N] ", end="", file=sys.stderr, flush=True)
    try:
        answer = sys.stdin.readline()
    except KeyboardInterrupt:
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return True
    print("aegisub-render: not replacing it", file=sys.stderr)
    return False


def _configure_render(p: argparse.ArgumentParser) -> None:
    add_common_args(p)
    add_output_args(p)
    which = p.add_mutually_exclusive_group()
    which.add_argument("--macro", help="macro name (default from config: Apply karaoke template)")
    which.add_argument(
        "--filter",
        help="export filter name; the result is then written like File > Export, but keeps the input's "
        "Project Garbage and Extradata sections",
    )
    p.add_argument("--select", type=parse_selection, help="dialogue lines to select, e.g. 1,3-5 (default: all)")
    p.add_argument("--force", action="store_true", help="run the macro even if its validation function says no")


@register_command(
    "render",
    help="run a macro (default: kara-templater) or export filter and save the result",
    configure=_configure_render,
)
def _run_render(args: argparse.Namespace, settings: Settings) -> ExitCode:
    options = build_render_options(args, settings)
    out = Output(quiet=options.quiet, progress=options.progress)
    output: Path | Literal["-"] | None = args.output
    if isinstance(output, Path) and not options.dry_run and output.exists() and not may_replace(output, args.override):
        return ExitCode.ERROR
    return render(args.input, output, settings, options, out)


@register_command(
    "list",
    help="list the macros and export filters a script registers",
    configure=add_common_args,
)
def _run_list(args: argparse.Namespace, settings: Settings) -> ExitCode:
    options = build_render_options(args, settings)
    out = Output(quiet=options.quiet, progress=options.progress)
    features = list_features(args.input, settings, options, out)
    if features is None:
        return ExitCode.ERROR
    print(f"{features.script_name}  {features.script_version}".rstrip())
    if features.script_description:
        print(f"  {features.script_description}")
    print("macros:")
    for name, help_text in features.macros:
        print(f"  {name}" + (f"  -- {help_text}" if help_text else ""))
    print("export filters:")
    for name, desc, priority in features.filters:
        print(f"  {name}  (priority {priority})" + (f"  -- {desc.splitlines()[0]}" if desc else ""))
    return ExitCode.OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegisub-render", description="Run Aegisub 3.5.1 automation scripts headlessly."
    )
    parser._negative_number_matcher = NEGATIVE_NUMBER_RE
    _COMMANDS.populate_subparsers(parser, dest="command", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_for_pipes()
    args = build_parser().parse_args(argv)
    handler: CommandHandler = args.handler
    if not hasattr(args, "config"):
        return handler(args, Settings())
    try:
        settings = load_cli_settings(args)
    except ConfigError as e:
        print(f"aegisub-render: {e}", file=sys.stderr)
        return ExitCode.ERROR
    return handler(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
