"""Command line interface. Options given here override config.py."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from aegisub_render.output import Output
from aegisub_render.runner import ExitCode, RenderOptions, list_features, render
from aegisub_render.settings import ConfigError, Settings, load_settings
from aegisub_render.video.context import VideoOptions


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


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", type=Path, help="subtitle file (.ass/.ssa)")
    p.add_argument("--config", type=Path, help="config file (default: AEGISUB_RENDER_CONFIG or the repository's config.py)")
    p.add_argument("--aegisub-root", type=Path, help="Aegisub installation directory (?data)")
    appdata = p.add_mutually_exclusive_group()
    appdata.add_argument("--load-appdata", dest="load_appdata", action="store_true", default=None,
                         help="use %%APPDATA%%\\Aegisub as ?user")
    appdata.add_argument("--no-load-appdata", dest="load_appdata", action="store_false",
                         help="portable mode: ?user is the Aegisub directory")
    p.add_argument("--script", help="script path, or file name looked up in the autoload directories")
    p.add_argument("--timeout", type=float, help="wall-clock limit in seconds")
    p.add_argument("--memory-limit", type=int, help="Lua heap limit in bytes")
    p.add_argument("--max-output-lines", type=int, help="limit on lines produced by the script")
    p.add_argument("--max-input-lines", type=int, help="limit on lines in the input file")
    p.add_argument("--font-dir", type=Path, action="append", default=[], help="extra font directory (repeatable)")
    p.add_argument("--trace-level", type=int, choices=range(6), help="aegisub.debug.out threshold (0-5)")
    p.add_argument("-v", "--verbose", action="count", default=0, help="raise the trace level by one per -v")
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors and script output")
    p.add_argument("--no-progress", action="store_true", help="never draw the progress line")

    video = p.add_argument_group("video")
    v = video.add_mutually_exclusive_group()
    v.add_argument("--video", help="video to load instead of the project's Video File (also ?dummy:...)")
    v.add_argument("--no-video", action="store_true", help="behave as if no video were loaded")
    video.add_argument("--video-size", type=parse_size, metavar="WxH", help="override the video frame size")
    tc = video.add_mutually_exclusive_group()
    tc.add_argument("--fps", help="constant frame rate (e.g. 23.976 or 24000/1001)")
    tc.add_argument("--timecodes", help="timecodes file (v1 or v2)")
    video.add_argument("--keyframes", help="keyframes file")
    video.add_argument("--refresh-cache", action="store_true", help="re-probe the video instead of using the cache")
    video.add_argument("--no-cache", action="store_true", help="neither read nor write the video cache")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aegisub-render", description="Run Aegisub 3.5.0 automation scripts headlessly.")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="run a macro (default: kara-templater) or export filter and save the result")
    _common(r)
    r.add_argument("-o", "--output", type=Path, required=True, help="output .ass file")
    which = r.add_mutually_exclusive_group()
    which.add_argument("--macro", help="macro name (default from config: Apply karaoke template)")
    which.add_argument("--filter", help="export filter name")
    r.add_argument("--select", type=parse_selection, help="dialogue lines to select, e.g. 1,3-5 (default: all)")
    r.add_argument("--force", action="store_true", help="run the macro even if its validation function says no")

    lst = sub.add_parser("list", help="list the macros and export filters a script registers")
    _common(lst)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    s = load_settings(args.config)
    if args.aegisub_root is not None:
        s.aegisub_root = args.aegisub_root
    if args.load_appdata is not None:
        s.load_appdata = args.load_appdata
    if args.timeout is not None:
        s.timeout_seconds = args.timeout
    if args.memory_limit is not None:
        s.memory_limit_bytes = args.memory_limit
    if args.max_output_lines is not None:
        s.max_output_lines = args.max_output_lines
    if args.max_input_lines is not None:
        s.max_input_lines = args.max_input_lines
    s.font_dirs = [*s.font_dirs, *args.font_dir]
    return s


def _options(args: argparse.Namespace, settings: Settings) -> RenderOptions:
    trace = args.trace_level if args.trace_level is not None else settings.trace_level
    trace = min(5, trace + args.verbose)
    return RenderOptions(
        script=args.script,
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
        progress=False if args.no_progress else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _settings(args)
    except ConfigError as e:
        print(f"aegisub-render: {e}", file=sys.stderr)
        return ExitCode.ERROR
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

    return render(args.input, args.output, settings, options, out)


if __name__ == "__main__":
    raise SystemExit(main())
