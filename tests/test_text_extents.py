"""The font substitution warning of aegisub.text_extents (GDI) and macro.measure."""

import struct
import sys

import pytest

from aegisub_render.ass.entries import AssStyle
from aegisub_render.host.text_extents import (  # pyright: ignore[reportPrivateUsage]
    TextExtents,
    _face_names,
    font_names,
    font_weight,
)
from aegisub_render.macro.measure import (
    MarginLayout,
    MeasureStyle,
    PosLayout,
    measure_text,
)


def style(font: str) -> AssStyle:
    return AssStyle.parse(
        f"Style: S,{font},40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1"
    )


def build_name_table(records: list[tuple[int, int, int, bytes]]) -> bytes:
    """(platform, encoding, name id, raw string) records, laid out as in an OpenType 'name' table."""
    head = struct.pack(">3H", 0, len(records), 6 + 12 * len(records))
    body = storage = b""
    for platform, encoding, name_id, raw in records:
        body += struct.pack(">6H", platform, encoding, 0, name_id, len(raw), len(storage))
        storage += raw
    return head + body + storage


def test_font_names() -> None:
    table = build_name_table(
        [
            (3, 1, 1, "Foo".encode("utf-16-be")),
            (3, 1, 1, "福".encode("utf-16-be")),
            (3, 1, 2, "Regular".encode("utf-16-be")),  # subfamily: not a face name
            (1, 0, 4, b"Foo Bold"),
            (3, 1, 16, "Foo Family".encode("utf-16-be")),
        ]
    )
    assert font_names(table) == {"Foo", "福", "Foo Bold", "Foo Family"}
    assert font_names(b"") == set()


def test_localized_family_name_is_not_a_substitution() -> None:
    """The family comes back in the user's language, which need not be the one the style uses."""
    names = _face_names("Microsoft JhengHei", style("Microsoft JhengHei"))
    if not {"Microsoft JhengHei", "微軟正黑體"} <= names:
        pytest.skip("Microsoft JhengHei is not installed")
    for face in ("Microsoft JhengHei", "微軟正黑體", "microsoft jhenghei"):
        te = TextExtents()
        assert te.measure(style(face), "x") is not None
        assert te.warnings == []


def test_missing_font_warns_once() -> None:
    te = TextExtents()
    te.measure(style("NoSuchFont 4cd1"), "x")
    te.measure(style("NoSuchFont 4cd1"), "y")
    assert len(te.warnings) == 1
    assert te.warnings[0].startswith("aegisub-render: font 'NoSuchFont 4cd1' is not installed; ")
    assert " substituted " in te.warnings[0]


@pytest.mark.parametrize(
    ("bold", "weight"),
    [
        (False, 400),
        (True, 700),
        (0, 400),
        (1, 700),
        (-1, 700),
        (300, 300),
        (900, 900),
        (1200, 1200),
        (9999, 9999),
        (10000, 10000),
        (-5, 400),
    ],
)
def test_font_weight_reads_bold_like_libass(bold: bool | int, weight: int) -> None:
    """\\b0 / \\b1 are off / bold, larger values are the weight itself."""
    assert font_weight(bold) == weight


def test_measure_with_a_weight() -> None:
    """MeasureStyle.bold takes libass values: 700 is what True gives, 400 what False gives."""
    for bold, same_as in ((700, True), (400, False), (1, True), (0, False)):
        a = measure_text(MeasureStyle(font="Arial", fontsize=48, bold=bold), "Weight")
        b = measure_text(MeasureStyle(font="Arial", fontsize=48, bold=same_as), "Weight")
        assert a.width == b.width, (bold, same_as)
    thin = measure_text(MeasureStyle(font="Arial", fontsize=48, bold=400), "Weight")
    heavy = measure_text(MeasureStyle(font="Arial", fontsize=48, bold=900), "Weight")
    assert heavy.width >= thin.width


@pytest.mark.skipif(sys.platform != "win32", reason="GDI emboldening; fontconfig stops at its heaviest weight")
def test_weights_beyond_1000_are_not_capped() -> None:
    """\\b9999 on a face that is already Black: GDI emboldens it further, so it measures wider."""
    face = "Noto Sans CJK TC Black"
    if face.lower() not in {n.lower() for n in _face_names(face, style(face))}:
        pytest.skip(f"{face} is not installed")
    black = measure_text(MeasureStyle(font=face, fontsize=48, bold=1000), "測試")
    heavier = measure_text(MeasureStyle(font=face, fontsize=48, bold=9999), "測試")
    assert heavier.width > black.width


def test_measure_text_metrics_and_ink_padding() -> None:
    ms = MeasureStyle(font="Arial", fontsize=48, scalex=100, scaley=100, spacing=2)
    res = measure_text(ms, "A B")
    assert res.text == "A B"
    assert res.width > 0 and res.height > 0
    assert len(res.chars) == 3
    assert [c.char for c in res.chars] == ["A", " ", "B"]

    # Per-character relative horizontal span is contiguous
    assert res.chars[0].left == 0.0
    assert res.chars[0].right == pytest.approx(res.chars[1].left)
    assert res.chars[1].right == pytest.approx(res.chars[2].left)
    assert res.chars[2].right == pytest.approx(res.width)

    # Non-whitespace characters have positive ink dimensions and padding summing to cell dimensions
    for idx in (0, 2):
        c = res.chars[idx]
        assert c.ink_width > 0 and c.ink_height > 0
        assert c.padding_left + c.ink_width + c.padding_right == pytest.approx(c.width)
        assert c.padding_top + c.ink_height + c.padding_bottom == pytest.approx(c.height)

    # Space has zero ink
    sp = res.chars[1]
    assert sp.ink_width == 0.0 and sp.ink_height == 0.0
    assert sp.padding_left == sp.width and sp.padding_top == sp.height

    # Line ink + padding sums to line width / height
    assert res.padding_left + res.ink_width + res.padding_right == pytest.approx(res.width)
    assert res.padding_top + res.ink_height + res.padding_bottom == pytest.approx(res.height)
    assert res.position is None


def test_measure_text_karaskel_margin_and_pos_layouts() -> None:
    ms = MeasureStyle(font="Arial", fontsize=40)

    # 1. an + margins (align=2 bottom-center, margins 10, 20, 30 on 1280x720)
    res_m = measure_text(ms, "Hi", MarginLayout(align=2, margin_l=10, margin_r=20, margin_v=30, res_x=1280, res_y=720))
    pos_m = res_m.position
    assert pos_m is not None
    assert pos_m.align == 2
    assert pos_m.halign == "center" and pos_m.valign == "bottom"
    assert pos_m.left == pytest.approx((1280 - 10 - 20 - res_m.width) / 2.0 + 10)
    assert pos_m.center == pytest.approx(pos_m.left + res_m.width / 2.0)
    assert pos_m.hcenter == pytest.approx(pos_m.center)
    assert pos_m.right == pytest.approx(pos_m.left + res_m.width)
    assert pos_m.bottom == pytest.approx(720 - 30)
    assert pos_m.middle == pytest.approx(pos_m.bottom - res_m.height / 2.0)
    assert pos_m.vcenter == pytest.approx(pos_m.middle)
    assert pos_m.top == pytest.approx(pos_m.bottom - res_m.height)
    assert pos_m.x == pytest.approx(pos_m.center)
    assert pos_m.y == pytest.approx(pos_m.bottom)
    assert (pos_m.eff_margin_l, pos_m.eff_margin_r, pos_m.eff_margin_v) == (10, 20, 30)
    assert res_m.chars[0].sleft == pytest.approx(pos_m.left)
    assert res_m.chars[0].x == pytest.approx(res_m.chars[0].scenter)
    assert res_m.chars[0].y == pytest.approx(pos_m.bottom)

    # 2. an + pos (align=5 middle-center at (640, 360))
    res_p = measure_text(ms, "Hi", PosLayout(align=5, x=640.0, y=360.0))
    pos_p = res_p.position
    assert pos_p is not None
    assert pos_p.halign == "center" and pos_p.valign == "middle"
    assert pos_p.x == 640.0 and pos_p.y == 360.0
    assert pos_p.left == pytest.approx(640.0 - res_p.width / 2.0)
    assert pos_p.right == pytest.approx(640.0 + res_p.width / 2.0)
    assert pos_p.top == pytest.approx(360.0 - res_p.height / 2.0)
    assert pos_p.bottom == pytest.approx(360.0 + res_p.height / 2.0)
    assert pos_p.eff_margin_v is None
