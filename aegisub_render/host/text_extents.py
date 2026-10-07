"""aegisub.text_extents: Automation4::CalculateTextExtents (src/auto4_base.cpp), WIN32 branch."""

from pathlib import Path

from aegisub_render.ass.entries import AssStyle
from aegisub_render.host import _gdi
from aegisub_render.textenc import to_wide

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".fon", ".fnt")


def register_private_fonts(dirs: list[Path]) -> int:
    """Make the fonts in `dirs` available to this process only (AddFontResourceEx FR_PRIVATE)."""
    count = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() in FONT_SUFFIXES and _gdi.add_private_font(str(f)):
                count += 1
    return count


class TextExtents:
    def __init__(self) -> None:
        self._warned: set[str] = set()
        self.warnings: list[str] = []

    def measure(self, style: AssStyle, text: str) -> tuple[float, float, float, float] | None:
        fontsize = style.fontsize * 64
        spacing = style.spacing * 64
        try:
            height = int(fontsize)  # (LONG)fontsize
        except OverflowError, ValueError:
            height = -(2**31)
        height = max(-(2**31), min(2**31 - 1, height))
        face = to_wide(style.font)
        result = _gdi.measure(
            face,
            height,
            style.bold,
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
        self._check_face(face, actual_face)
        return (
            style.scalex / 100 * width / 64,
            style.scaley / 100 * h / 64,
            style.scaley / 100 * descent / 64,
            style.scaley / 100 * extlead / 64,
        )

    def _check_face(self, requested: str, actual: str) -> None:
        requested = requested[:31]
        key = requested.lower()
        if key in self._warned or actual.lower() == key or requested.startswith("@"):
            return
        self._warned.add(key)
        self.warnings.append(
            f"aegisub-render: font '{requested}' is not installed; Windows substituted '{actual}' "
            "for aegisub.text_extents (Aegisub would do the same)\n"
        )
