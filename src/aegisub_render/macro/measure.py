"""Measure text dimensions, glyph ink padding, per-character positions, and karaskel-style layout
without starting Aegisub."""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aegisub_render.ass.entries import AssStyle, SubtitleFormatParseError
from aegisub_render.ass.parser import UnsupportedEncodingError, read_ass_file
from aegisub_render.cli import add_override_args, may_replace, parse_output, parse_size
from aegisub_render.host.text_extents import TextExtents, register_private_fonts
from aegisub_render.macro import register_command
from aegisub_render.output import Output
from aegisub_render.runner import STDOUT, ExitCode
from aegisub_render.settings import Settings
from aegisub_render.wsl import translate_path


class MeasureError(Exception):
    pass


@dataclass(slots=True)
class MeasureStyle:
    """All style variables that affect text size and glyph metrics."""

    font: str
    fontsize: float
    bold: bool | int = False  # as libass reads \\b: 0/False, 1/True (700) or a weight (100 .. 900)
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    scalex: float = 100.0
    scaley: float = 100.0
    spacing: float = 0.0
    encoding: int = 1
    outline: float = 2.0
    shadow: float = 2.0

    @staticmethod
    def from_ass_style(style: AssStyle) -> MeasureStyle:
        return MeasureStyle(
            font=style.font,
            fontsize=style.fontsize,
            bold=style.bold,
            italic=style.italic,
            underline=style.underline,
            strikeout=style.strikeout,
            scalex=style.scalex,
            scaley=style.scaley,
            spacing=style.spacing,
            encoding=style.encoding,
            outline=style.outline_w,
            shadow=style.shadow_w,
        )

    def to_ass_style(self) -> AssStyle:
        return AssStyle(
            font=self.font,
            fontsize=self.fontsize,
            bold=bool(self.bold),  # an AssStyle only holds a bool; measure_text passes the weight
            italic=self.italic,
            underline=self.underline,
            strikeout=self.strikeout,
            scalex=self.scalex,
            scaley=self.scaley,
            spacing=self.spacing,
            encoding=self.encoding,
            outline_w=self.outline,
            shadow_w=self.shadow,
        )


@dataclass(slots=True)
class MarginLayout:
    """Positioning via numpad alignment (1-9) and L/R/V margins within a script resolution."""

    align: int
    margin_l: int
    margin_r: int
    margin_v: int
    res_x: int = 1280
    res_y: int = 720


@dataclass(slots=True)
class PosLayout:
    """Positioning via numpad alignment (1-9) and explicit \\pos(x, y) coordinates."""

    align: int
    x: float
    y: float


type LayoutSpec = MarginLayout | PosLayout


@dataclass(slots=True)
class CharMetrics:
    index: int
    char: str
    width: float
    height: float
    descent: float
    extlead: float
    ink_width: float
    ink_height: float
    padding_left: float
    padding_right: float
    padding_top: float
    padding_bottom: float
    left: float
    center: float
    right: float
    sleft: float | None = None
    scenter: float | None = None
    sright: float | None = None
    stop: float | None = None
    smiddle: float | None = None
    sbottom: float | None = None
    x: float | None = None
    y: float | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "index": self.index,
            "char": self.char,
            "width": self.width,
            "height": self.height,
            "descent": self.descent,
            "extlead": self.extlead,
            "ink_width": self.ink_width,
            "ink_height": self.ink_height,
            "padding_left": self.padding_left,
            "padding_right": self.padding_right,
            "padding_top": self.padding_top,
            "padding_bottom": self.padding_bottom,
            "left": self.left,
            "center": self.center,
            "right": self.right,
        }
        if self.x is not None:
            d["sleft"] = self.sleft
            d["scenter"] = self.scenter
            d["sright"] = self.sright
            d["stop"] = self.stop
            d["smiddle"] = self.smiddle
            d["sbottom"] = self.sbottom
            d["x"] = self.x
            d["y"] = self.y
        return d


@dataclass(slots=True)
class LinePosition:
    """Position fields matching karaskel.lua (`karaskel.preproc_line_pos`)."""

    align: int
    halign: Literal["left", "center", "right"]
    valign: Literal["top", "middle", "bottom"]
    left: float
    center: float
    hcenter: float
    right: float
    top: float
    middle: float
    vcenter: float
    bottom: float
    x: float
    y: float
    eff_margin_l: int | None = None
    eff_margin_r: int | None = None
    eff_margin_t: int | None = None
    eff_margin_b: int | None = None
    eff_margin_v: int | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "align": self.align,
            "halign": self.halign,
            "valign": self.valign,
            "left": self.left,
            "center": self.center,
            "hcenter": self.hcenter,
            "right": self.right,
            "top": self.top,
            "middle": self.middle,
            "vcenter": self.vcenter,
            "bottom": self.bottom,
            "x": self.x,
            "y": self.y,
        }
        if self.eff_margin_v is not None:
            d["eff_margin_l"] = self.eff_margin_l
            d["eff_margin_r"] = self.eff_margin_r
            d["eff_margin_t"] = self.eff_margin_t
            d["eff_margin_b"] = self.eff_margin_b
            d["eff_margin_v"] = self.eff_margin_v
        return d


@dataclass(slots=True)
class MeasureResult:
    text: str
    width: float
    height: float
    descent: float
    extlead: float
    ink_width: float
    ink_height: float
    padding_left: float
    padding_right: float
    padding_top: float
    padding_bottom: float
    chars: list[CharMetrics]
    position: LinePosition | None = None
    warnings: list[str] = field(default_factory=list[str])

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "text": self.text,
            "width": self.width,
            "height": self.height,
            "descent": self.descent,
            "extlead": self.extlead,
            "ink_width": self.ink_width,
            "ink_height": self.ink_height,
            "padding_left": self.padding_left,
            "padding_right": self.padding_right,
            "padding_top": self.padding_top,
            "padding_bottom": self.padding_bottom,
        }
        if self.position is not None:
            pos_dict = self.position.to_dict()
            d.update(pos_dict)
            d["position"] = pos_dict
        d["chars"] = [c.to_dict() for c in self.chars]
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _compute_position(width: float, height: float, layout: LayoutSpec) -> LinePosition:
    align = layout.align
    if not (1 <= align <= 9):
        raise MeasureError(f"align (an) must be between 1 and 9, got {align}")

    halign: Literal["left", "center", "right"]
    valign: Literal["top", "middle", "bottom"]

    if isinstance(layout, MarginLayout):
        ml, mr, mv = layout.margin_l, layout.margin_r, layout.margin_v
        res_x, res_y = layout.res_x, layout.res_y
        if align in (1, 4, 7):
            left = float(ml)
            center = left + width / 2.0
            right = left + width
            x = left
            halign = "left"
        elif align in (2, 5, 8):
            left = (res_x - ml - mr - width) / 2.0 + ml
            center = left + width / 2.0
            right = left + width
            x = center
            halign = "center"
        else:
            left = res_x - mr - width
            center = left + width / 2.0
            right = left + width
            x = right
            halign = "right"

        if 1 <= align <= 3:
            bottom = float(res_y - mv)
            middle = bottom - height / 2.0
            top = bottom - height
            y = bottom
            valign = "bottom"
        elif 4 <= align <= 6:
            top = (res_y - mv - mv - height) / 2.0 + mv
            middle = top + height / 2.0
            bottom = top + height
            y = middle
            valign = "middle"
        else:
            top = float(mv)
            middle = top + height / 2.0
            bottom = top + height
            y = top
            valign = "top"

        return LinePosition(
            align=align,
            halign=halign,
            valign=valign,
            left=left,
            center=center,
            hcenter=center,
            right=right,
            top=top,
            middle=middle,
            vcenter=middle,
            bottom=bottom,
            x=x,
            y=y,
            eff_margin_l=ml,
            eff_margin_r=mr,
            eff_margin_t=mv,
            eff_margin_b=mv,
            eff_margin_v=mv,
        )

    px, py = layout.x, layout.y
    if align in (1, 4, 7):
        left = px
        center = left + width / 2.0
        right = left + width
        halign = "left"
    elif align in (2, 5, 8):
        left = px - width / 2.0
        center = px
        right = px + width / 2.0
        halign = "center"
    else:
        left = px - width
        center = px - width / 2.0
        right = px
        halign = "right"

    if 1 <= align <= 3:
        bottom = py
        middle = bottom - height / 2.0
        top = bottom - height
        valign = "bottom"
    elif 4 <= align <= 6:
        top = py - height / 2.0
        middle = py
        bottom = py + height / 2.0
        valign = "middle"
    else:
        top = py
        middle = top + height / 2.0
        bottom = top + height
        valign = "top"

    return LinePosition(
        align=align,
        halign=halign,
        valign=valign,
        left=left,
        center=center,
        hcenter=center,
        right=right,
        top=top,
        middle=middle,
        vcenter=middle,
        bottom=bottom,
        x=px,
        y=py,
    )


def measure_text(
    style: MeasureStyle | AssStyle,
    text: str,
    layout: LayoutSpec | None = None,
    *,
    video_x_correct_factor: float = 1.0,
    font_dirs: list[Path] | None = None,
) -> MeasureResult:
    """Measure `text` under `style`, returning overall metrics, ink padding, per-character boxes,
    and (when `layout` is given) karaskel-compatible position information."""
    if font_dirs:
        register_private_fonts(font_dirs)

    ass_style = style.to_ass_style() if isinstance(style, MeasureStyle) else style
    bold = style.bold if isinstance(style, MeasureStyle) else None
    extents = TextExtents()
    whole = extents.measure(ass_style, text, bold)
    if whole is None:
        raise MeasureError("GDI failed to measure text")
    raw_w, height, descent, extlead = whole
    width = raw_w * video_x_correct_factor

    glyph_info = extents.glyph_ink_boxes(ass_style, text, bold)
    if glyph_info is None:
        raise MeasureError("GDI failed to query glyph metrics")
    tm_ascent, boxes = glyph_info

    sx = (ass_style.scalex / 100.0) / 64.0 * video_x_correct_factor
    sy = (ass_style.scaley / 100.0) / 64.0

    char_list = list(text)
    chars: list[CharMetrics] = []
    cur_x = 0.0
    ink_min_x: float | None = None
    ink_max_x: float | None = None
    ink_min_y: float | None = None
    ink_max_y: float | None = None

    for idx, (ch, (has_ink, orig_x, orig_y, bb_x, bb_y)) in enumerate(zip(char_list, boxes, strict=True)):
        ch_ext = extents.measure(ass_style, ch, bold)
        if ch_ext is None:
            raise MeasureError(f"GDI failed to measure character {ch!r}")
        ch_w = ch_ext[0] * video_x_correct_factor
        ch_h, ch_desc, ch_lead = ch_ext[1], ch_ext[2], ch_ext[3]

        left = cur_x
        center = left + ch_w / 2.0
        right = left + ch_w
        cur_x = right

        if has_ink:
            c_pad_left = orig_x * sx
            c_ink_w = bb_x * sx
            c_pad_right = ch_w - (c_pad_left + c_ink_w)
            c_pad_top = (tm_ascent - orig_y) * sy
            c_ink_h = bb_y * sy
            c_pad_bottom = ch_h - (c_pad_top + c_ink_h)

            x0 = left + c_pad_left
            x1 = x0 + c_ink_w
            y0 = c_pad_top
            y1 = y0 + c_ink_h
            ink_min_x = x0 if ink_min_x is None else min(ink_min_x, x0)
            ink_max_x = x1 if ink_max_x is None else max(ink_max_x, x1)
            ink_min_y = y0 if ink_min_y is None else min(ink_min_y, y0)
            ink_max_y = y1 if ink_max_y is None else max(ink_max_y, y1)
        else:
            c_ink_w = 0.0
            c_ink_h = 0.0
            c_pad_left = ch_w
            c_pad_right = ch_w
            c_pad_top = ch_h
            c_pad_bottom = ch_h

        chars.append(
            CharMetrics(
                index=idx,
                char=ch,
                width=ch_w,
                height=ch_h,
                descent=ch_desc,
                extlead=ch_lead,
                ink_width=c_ink_w,
                ink_height=c_ink_h,
                padding_left=c_pad_left,
                padding_right=c_pad_right,
                padding_top=c_pad_top,
                padding_bottom=c_pad_bottom,
                left=left,
                center=center,
                right=right,
            )
        )

    if ink_min_x is not None and ink_max_x is not None and ink_min_y is not None and ink_max_y is not None:
        line_pad_left = ink_min_x
        line_pad_right = width - ink_max_x
        line_pad_top = ink_min_y
        line_pad_bottom = height - ink_max_y
        line_ink_w = ink_max_x - ink_min_x
        line_ink_h = ink_max_y - ink_min_y
    else:
        line_ink_w = 0.0
        line_ink_h = 0.0
        line_pad_left = width
        line_pad_right = width
        line_pad_top = height
        line_pad_bottom = height

    position: LinePosition | None = None
    if layout is not None:
        position = _compute_position(width, height, layout)
        for c in chars:
            c.sleft = position.left + c.left
            c.scenter = position.left + c.center
            c.sright = position.left + c.right
            c.stop = position.top
            c.smiddle = position.middle
            c.sbottom = position.bottom
            if position.halign == "left":
                c.x = c.sleft
            elif position.halign == "center":
                c.x = c.scenter
            else:
                c.x = c.sright
            if position.valign == "top":
                c.y = c.stop
            elif position.valign == "middle":
                c.y = c.smiddle
            else:
                c.y = c.sbottom

    return MeasureResult(
        text=text,
        width=width,
        height=height,
        descent=descent,
        extlead=extlead,
        ink_width=line_ink_w,
        ink_height=line_ink_h,
        padding_left=line_pad_left,
        padding_right=line_pad_right,
        padding_top=line_pad_top,
        padding_bottom=line_pad_bottom,
        chars=chars,
        position=position,
        warnings=list(extents.warnings),
    )


def resolve_style_from_ass(ass_path: Path, style_name: str = "Default") -> tuple[AssStyle, tuple[int, int]]:
    """Load `style_name` and `(res_x, res_y)` from `ass_path`."""
    try:
        ass = read_ass_file(ass_path)
    except (OSError, UnsupportedEncodingError, SubtitleFormatParseError) as e:
        raise MeasureError(f"could not read {ass_path}: {e}") from None
    for s in ass.styles:
        if s.name == style_name:
            return s, ass.get_resolution()
    available = ", ".join(repr(s.name) for s in ass.styles) or "(none)"
    raise MeasureError(f"style {style_name!r} not found in {ass_path}; available styles: {available}")


def measure(
    style: MeasureStyle | AssStyle,
    text: str,
    output_path: Path | Literal["-"] | None = STDOUT,
    layout: LayoutSpec | None = None,
    *,
    video_x_correct_factor: float = 1.0,
    font_dirs: list[Path] | None = None,
    out: Output | None = None,
) -> ExitCode:
    """Run `measure_text` and write the JSON result to `output_path` (default: stdout)."""
    out = out or Output(progress=False)
    try:
        result = measure_text(
            style,
            text,
            layout,
            video_x_correct_factor=video_x_correct_factor,
            font_dirs=font_dirs,
        )
    except MeasureError as e:
        out.error(f"aegisub-render: {e}")
        return ExitCode.ERROR
    for w in result.warnings:
        out.message(w)
    payload = (result.to_json(indent=2) + "\n").encode("utf-8")
    if output_path is None or output_path == STDOUT:
        sys.stdout.flush()
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return ExitCode.OK
    try:
        output_path.write_bytes(payload)
    except OSError as e:
        out.error(f"aegisub-render: could not write {output_path}: {e}")
        return ExitCode.ERROR
    return ExitCode.OK


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


def _configure_measure(p: argparse.ArgumentParser) -> None:
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
    add_override_args(p)

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


@register_command(
    "measure",
    help="measure text extents, ink padding, per-character boxes, and karaskel position as JSON",
    configure=_configure_measure,
)
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


__all__ = [
    "CharMetrics",
    "LayoutSpec",
    "LinePosition",
    "MarginLayout",
    "MeasureError",
    "MeasureResult",
    "MeasureStyle",
    "PosLayout",
    "measure",
    "measure_text",
    "parse_margins",
    "parse_pos",
    "resolve_style_from_ass",
]
