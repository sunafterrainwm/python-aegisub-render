"""Command line interface. Options given here override config.py."""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from aegisub_render.macro import (
    LayoutSpec,
    MarginLayout,
    MeasureError,
    MeasureStyle,
    Offset,
    PosLayout,
    measure,
    offset,
    parse_offset,
    resolve_style_from_ass,
)
from aegisub_render.output import Output, use_utf8_for_pipes
from aegisub_render.runner import STDOUT, ExitCode, RenderOptions, list_features, render
from aegisub_render.settings import ConfigError, Settings, load_settings
from aegisub_render.video.context import VideoOptions
from aegisub_render.wsl import translate, translate_path

_NEGATIVE_OFFSET_RE = re.compile(r"^-[\d.]")


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


def parse_pos(spec: str) -> tuple[float, float]:
    parts = spec.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"bad pos {spec!r} (expected X,Y)")
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad pos {spec!r} (expected X,Y)") from None


def parse_margins(spec: str) -> tuple[int, int, int]:
    parts = spec.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"bad margins {spec!r} (expected L,R,V)")
    try:
        return int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad margins {spec!r} (expected L,R,V integers)") from None


def parse_output(spec: str) -> Path | Literal["-"]:
    """'-' is stdout; a file named '-' can still be given as './-'."""
    return STDOUT if spec == STDOUT else translate_path(spec)


def parse_offset_arg(spec: str) -> Offset:
    try:
        return parse_offset(spec)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _base_common(p: argparse.ArgumentParser) -> None:
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


def _video_args(p: argparse.ArgumentParser) -> None:
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


def _override_args(p: argparse.ArgumentParser) -> None:
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


def _output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-o",
        "--output",
        type=parse_output,
        help="output .ass file, or - for stdout (default: replace the input file)",
    )
    _override_args(p)
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


def _common(p: argparse.ArgumentParser) -> None:
    _base_common(p)
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
    _video_args(p)


def _measure_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("text", help="text string to measure")
    p.add_argument(
        "--config",
        type=translate_path,
        help="config file (default: AEGISUB_RENDER_CONFIG or the repository's config.py)",
    )
    p.add_argument(
        "--font-dir", type=translate_path, action="append", default=[], help="extra font directory (repeatable)"
    )
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    p.add_argument(
        "-o",
        "--output",
        type=parse_output,
        default=STDOUT,
        help="output JSON file, or - for stdout (default: -)",
    )
    _override_args(p)

    style_grp = p.add_argument_group("style")
    style_grp.add_argument("--ass", type=translate_path, help="load base style and resolution from an .ass/.ssa file")
    style_grp.add_argument("--style", default="Default", help="style name in --ass (default: Default)")
    style_grp.add_argument("--font", help="font family name (required unless --ass is given)")
    style_grp.add_argument("--fontsize", type=float, help="font size in points/pixels (required unless --ass is given)")
    bold = style_grp.add_mutually_exclusive_group()
    bold.add_argument(
        "--bold",
        type=int,
        nargs="?",
        const=1,
        default=None,
        metavar="N",
        help="bold, read like libass reads \\bN: 1 (the default) is bold, 0 is normal, 100..900 is a weight",
    )
    bold.add_argument("--no-bold", dest="bold", action="store_const", const=0, help="not bold (--bold 0)")
    style_grp.add_argument(
        "--italic", action=argparse.BooleanOptionalAction, default=None, help="italic style (--italic / --no-italic)"
    )
    style_grp.add_argument(
        "--underline",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="underline style (--underline / --no-underline)",
    )
    style_grp.add_argument(
        "--strikeout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="strikeout style (--strikeout / --no-strikeout)",
    )
    style_grp.add_argument("--scale-x", type=float, default=None, metavar="PERCENT", help="horizontal scale percentage")
    style_grp.add_argument("--scale-y", type=float, default=None, metavar="PERCENT", help="vertical scale percentage")
    style_grp.add_argument("--spacing", type=float, default=None, metavar="PX", help="inter-character spacing")
    style_grp.add_argument("--encoding", type=int, default=None, metavar="ID", help="Windows font charset/encoding ID")
    style_grp.add_argument("--outline", type=float, default=None, metavar="PX", help="outline width")
    style_grp.add_argument("--shadow", type=float, default=None, metavar="PX", help="shadow depth")

    layout_grp = p.add_argument_group("layout (karaskel position)")
    layout_grp.add_argument("--an", type=int, choices=range(1, 10), metavar="1-9", help="numpad alignment (1-9)")
    pos_or_margins = layout_grp.add_mutually_exclusive_group()
    pos_or_margins.add_argument("--pos", type=parse_pos, metavar="X,Y", help="explicit \\pos(x,y) coordinates")
    pos_or_margins.add_argument(
        "--margins", type=parse_margins, metavar="L,R,V", help="left, right, and vertical margins (L,R,V)"
    )
    layout_grp.add_argument(
        "--res", type=parse_size, metavar="WxH", help="script resolution for margins (default: 1280x720 or from --ass)"
    )
    layout_grp.add_argument(
        "--video-size",
        type=parse_size,
        metavar="WxH",
        help="video frame size for karaskel aspect ratio width correction",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegisub-render", description="Run Aegisub 3.5.1 automation scripts headlessly."
    )
    parser._negative_number_matcher = _NEGATIVE_OFFSET_RE
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="run a macro (default: kara-templater) or export filter and save the result")
    _common(r)
    _output_args(r)
    which = r.add_mutually_exclusive_group()
    which.add_argument("--macro", help="macro name (default from config: Apply karaoke template)")
    which.add_argument(
        "--filter",
        help="export filter name; the result is then written like File > Export, but keeps the input's "
        "Project Garbage and Extradata sections",
    )
    r.add_argument("--select", type=parse_selection, help="dialogue lines to select, e.g. 1,3-5 (default: all)")
    r.add_argument("--force", action="store_true", help="run the macro even if its validation function says no")

    lst = sub.add_parser("list", help="list the macros and export filters a script registers")
    _common(lst)

    mc = sub.add_parser("macro", help="run built-in headless macro operations without starting Aegisub")
    mc._negative_number_matcher = _NEGATIVE_OFFSET_RE
    macro_sub = mc.add_subparsers(dest="macro_command", required=True)

    off = macro_sub.add_parser(
        "offset", help="shift subtitle times by a timestamp or frame count without starting Aegisub"
    )
    off._negative_number_matcher = _NEGATIVE_OFFSET_RE
    _base_common(off)
    off.add_argument(
        "offset",
        type=parse_offset_arg,
        help="time ([+-][[[hh:]mm:]ss].ms) or frame ([+-]frames) offset",
    )
    off.add_argument("--max-input-lines", type=int, help="limit on lines in the input file")
    off.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    _output_args(off)
    _video_args(off)

    ms = macro_sub.add_parser(
        "measure",
        help="measure text extents, ink padding, per-character boxes, and karaskel position as JSON",
    )
    ms._negative_number_matcher = _NEGATIVE_OFFSET_RE
    _measure_args(ms)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    s = load_settings(args.config)
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


def _options(args: argparse.Namespace, settings: Settings) -> RenderOptions:
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


def _run_measure(args: argparse.Namespace, settings: Settings) -> ExitCode:
    out = Output(quiet=args.quiet, progress=False)
    output: Path | Literal["-"] = args.output
    if isinstance(output, Path) and output.exists() and not may_replace(output, args.override):
        return ExitCode.ERROR

    res_x, res_y = 1280, 720
    ass_align: int | None = None
    ass_margins: tuple[int, int, int] | None = None

    if args.ass is not None:
        try:
            base_ass_style, (res_x, res_y) = resolve_style_from_ass(args.ass, args.style)
        except MeasureError as e:
            out.error(f"aegisub-render: {e}")
            return ExitCode.ERROR
        style = MeasureStyle.from_ass_style(base_ass_style)
        ass_align = base_ass_style.alignment
        ass_margins = (base_ass_style.margin[0], base_ass_style.margin[1], base_ass_style.margin[2])
        if args.font is not None:
            style.font = args.font
        if args.fontsize is not None:
            style.fontsize = args.fontsize
    else:
        if args.font is None or args.fontsize is None:
            out.error("aegisub-render: measure requires --font and --fontsize (or --ass)")
            return ExitCode.ERROR
        style = MeasureStyle(font=args.font, fontsize=args.fontsize)

    if args.bold is not None:
        style.bold = args.bold
    if args.italic is not None:
        style.italic = args.italic
    if args.underline is not None:
        style.underline = args.underline
    if args.strikeout is not None:
        style.strikeout = args.strikeout
    if args.scale_x is not None:
        style.scalex = args.scale_x
    if args.scale_y is not None:
        style.scaley = args.scale_y
    if args.spacing is not None:
        style.spacing = args.spacing
    if args.encoding is not None:
        style.encoding = args.encoding
    if args.outline is not None:
        style.outline = args.outline
    if args.shadow is not None:
        style.shadow = args.shadow

    if args.res is not None:
        res_x, res_y = args.res

    arg_an: int | None = args.an
    arg_pos: tuple[float, float] | None = args.pos
    arg_margins: tuple[int, int, int] | None = args.margins

    layout: LayoutSpec | None = None
    if arg_pos is not None:
        align = arg_an if arg_an is not None else ass_align
        if align is None:
            out.error("aegisub-render: --pos requires --an (or --ass)")
            return ExitCode.ERROR
        layout = PosLayout(align=align, x=arg_pos[0], y=arg_pos[1])
    elif arg_margins is not None:
        align = arg_an if arg_an is not None else ass_align
        if align is None:
            out.error("aegisub-render: --margins requires --an (or --ass)")
            return ExitCode.ERROR
        layout = MarginLayout(
            align=align,
            margin_l=arg_margins[0],
            margin_r=arg_margins[1],
            margin_v=arg_margins[2],
            res_x=res_x,
            res_y=res_y,
        )
    elif arg_an is not None:
        if ass_margins is not None:
            layout = MarginLayout(
                align=arg_an,
                margin_l=ass_margins[0],
                margin_r=ass_margins[1],
                margin_v=ass_margins[2],
                res_x=res_x,
                res_y=res_y,
            )
        else:
            out.error("aegisub-render: --an requires --pos or --margins (or --ass)")
            return ExitCode.ERROR

    video_x_correct = 1.0
    if args.video_size is not None:
        vx, vy = args.video_size
        video_x_correct = (vy / vx) / (res_y / res_x)

    return measure(
        style,
        args.text,
        output_path=output,
        layout=layout,
        video_x_correct_factor=video_x_correct,
        font_dirs=list(settings.font_dirs),
        out=out,
    )


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_for_pipes()
    args = build_parser().parse_args(argv)
    try:
        settings = _settings(args)
    except ConfigError as e:
        print(f"aegisub-render: {e}", file=sys.stderr)
        return ExitCode.ERROR

    if args.command == "macro" and args.macro_command == "measure":
        return _run_measure(args, settings)

    options = _options(args, settings)
    out = Output(quiet=options.quiet, progress=options.progress)

    if args.command == "list":
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

    output: Path | Literal["-"] | None = args.output
    if isinstance(output, Path) and not options.dry_run and output.exists() and not may_replace(output, args.override):
        return ExitCode.ERROR
    if args.command == "macro" and args.macro_command == "offset":
        return offset(args.input, output, args.offset, settings, options, out)
    return render(args.input, output, settings, options, out)


if __name__ == "__main__":
    raise SystemExit(main())
