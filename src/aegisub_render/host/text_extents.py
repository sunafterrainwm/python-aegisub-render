"""aegisub.text_extents: Automation4::CalculateTextExtents (src/auto4_base.cpp).

The measuring is done by aegisub_render.host._font, built from _font.gdi.pyx on Windows (GDI, its
WIN32 branch) and from _font.fcft.pyx elsewhere (its wxWidgets branch, with fontconfig and HarfBuzz).
"""

from pathlib import Path

from aegisub_render.ass.entries import AssStyle
from aegisub_render.host import _font
from aegisub_render.host.fontnames import font_names
from aegisub_render.textenc import to_wide


def font_weight(bold: bool | int) -> int:
    """A font weight from a bold value, as libass reads \\b: 0 is normal (400), 1 (or -1, or True)
    is bold (700), anything above 1 is the weight itself (\\b100 .. \\b900). There is no upper limit:
    \\b9999 asks for more than the heaviest face has, and GDI then emboldens even a Black face
    (fontconfig takes anything above 1000 as its heaviest weight). Aegisub's styles only hold a bool,
    which gives what CalculateTextExtents asks for (FW_NORMAL / FW_BOLD)."""
    value = int(bold)
    if value in (1, -1):
        return 700
    if value <= 0:
        return 400
    return value


def register_private_fonts(dirs: list[Path]) -> int:
    """Make the fonts in `dirs` (and their subdirectories) available to this process only."""
    return sum(_font.add_private_font_dir(str(d)) for d in dirs if d.is_dir())


def _scaled_height(style: AssStyle) -> int:
    fontsize = style.fontsize * 64
    try:
        height = int(fontsize)  # (LONG)fontsize
    except OverflowError, ValueError:
        height = -(2**31)
    return max(-(2**31), min(2**31 - 1, height))


class TextExtents:
    def __init__(self) -> None:
        self._checked: set[str] = set()
        self.warnings: list[str] = []

    def measure(
        self, style: AssStyle, text: str, bold: bool | int | None = None
    ) -> tuple[float, float, float, float] | None:
        """`bold` overrides the style's (which is a bool) with a libass-style value (see font_weight)."""
        height = _scaled_height(style)
        spacing = style.spacing * 64
        face = to_wide(style.font)
        result = _font.measure(
            face,
            height,
            font_weight(style.bold if bold is None else bold),
            style.italic,
            style.underline,
            style.strikeout,
            style.encoding & 0xFF,
            to_wide(text),
            spacing,
        )
        if result is None:
            return None
        width, h, descent, extlead, actual_face = result
        self._check_face(face, actual_face, style, font_weight(style.bold if bold is None else bold))
        return (
            style.scalex / 100 * width / 64,
            style.scaley / 100 * h / 64,
            style.scaley / 100 * descent / 64,
            style.scaley / 100 * extlead / 64,
        )

    def glyph_ink_boxes(
        self, style: AssStyle, text: str, bold: bool | int | None = None
    ) -> tuple[int, list[tuple[bool, int, int, int, int]]] | None:
        return _font.glyph_ink_boxes(
            to_wide(style.font),
            _scaled_height(style),
            font_weight(style.bold if bold is None else bold),
            style.italic,
            style.underline,
            style.strikeout,
            style.encoding & 0xFF,
            to_wide(text),
        )

    def _check_face(self, requested: str, actual: str, style: AssStyle, weight: int) -> None:
        requested = requested[:31]
        key = requested.lower()
        if key in self._checked or actual.lower() == key or requested.startswith("@"):
            return
        self._checked.add(key)
        # The family comes back in the user's language; the style may use any of the font's names.
        if key in {name[:31].lower() for name in _face_names(requested, style, weight)}:
            return
        system = "Windows" if _font.backend() == "gdi" else "fontconfig"
        self.warnings.append(
            f"aegisub-render: font '{requested}' is not installed; {system} substituted '{actual}' "
            "for aegisub.text_extents (Aegisub would do the same)\n"
        )


def _face_names(requested: str, style: AssStyle, weight: int | None = None) -> set[str]:
    """Every name of the font the system picks for `requested`, in every language."""
    w = weight or font_weight(style.bold)
    return set(_font.face_names(requested, w, style.italic, style.encoding & 0xFF))


__all__ = ["TextExtents", "font_names", "font_weight", "register_private_fonts"]
