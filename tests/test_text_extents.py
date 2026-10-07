"""The font substitution warning of aegisub.text_extents (GDI)."""

import struct

import pytest

from aegisub_render.ass.entries import AssStyle
from aegisub_render.host import _gdi
from aegisub_render.host.text_extents import TextExtents, font_names


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
    """GetTextFace gives the name in the user's language, which need not be the one the style uses."""
    table = _gdi.name_table("Microsoft JhengHei", False, False, 1)
    names = font_names(table if table is not None else b"")
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
    assert te.warnings[0].startswith("aegisub-render: font 'NoSuchFont 4cd1' is not installed; Windows substituted")
