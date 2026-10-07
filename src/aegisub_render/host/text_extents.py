"""aegisub.text_extents: Automation4::CalculateTextExtents (src/auto4_base.cpp), WIN32 branch, via ctypes."""

import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

from aegisub_render.ass.entries import AssStyle
from aegisub_render.textenc import to_wide

MM_TEXT = 1
FW_NORMAL = 400
FW_BOLD = 700
OUT_TT_PRECIS = 4
CLIP_DEFAULT_PRECIS = 0
ANTIALIASED_QUALITY = 4
DEFAULT_PITCH = 0
FF_DONTCARE = 0
FR_PRIVATE = 0x10
LF_FACESIZE = 32


class LOGFONTW(ctypes.Structure):
    _fields_ = (
        ("lfHeight", wintypes.LONG),
        ("lfWidth", wintypes.LONG),
        ("lfEscapement", wintypes.LONG),
        ("lfOrientation", wintypes.LONG),
        ("lfWeight", wintypes.LONG),
        ("lfItalic", ctypes.c_ubyte),
        ("lfUnderline", ctypes.c_ubyte),
        ("lfStrikeOut", ctypes.c_ubyte),
        ("lfCharSet", ctypes.c_ubyte),
        ("lfOutPrecision", ctypes.c_ubyte),
        ("lfClipPrecision", ctypes.c_ubyte),
        ("lfQuality", ctypes.c_ubyte),
        ("lfPitchAndFamily", ctypes.c_ubyte),
        ("lfFaceName", ctypes.c_wchar * LF_FACESIZE),
    )


class TEXTMETRICW(ctypes.Structure):
    _fields_ = (
        ("tmHeight", wintypes.LONG),
        ("tmAscent", wintypes.LONG),
        ("tmDescent", wintypes.LONG),
        ("tmInternalLeading", wintypes.LONG),
        ("tmExternalLeading", wintypes.LONG),
        ("tmAveCharWidth", wintypes.LONG),
        ("tmMaxCharWidth", wintypes.LONG),
        ("tmWeight", wintypes.LONG),
        ("tmOverhang", wintypes.LONG),
        ("tmDigitizedAspectX", wintypes.LONG),
        ("tmDigitizedAspectY", wintypes.LONG),
        ("tmFirstChar", ctypes.c_wchar),
        ("tmLastChar", ctypes.c_wchar),
        ("tmDefaultChar", ctypes.c_wchar),
        ("tmBreakChar", ctypes.c_wchar),
        ("tmItalic", ctypes.c_ubyte),
        ("tmUnderlined", ctypes.c_ubyte),
        ("tmStruckOut", ctypes.c_ubyte),
        ("tmPitchAndFamily", ctypes.c_ubyte),
        ("tmCharSet", ctypes.c_ubyte),
    )


_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_CreateCompatibleDC = _gdi32.CreateCompatibleDC
_CreateCompatibleDC.restype = wintypes.HDC
_CreateCompatibleDC.argtypes = (wintypes.HDC,)
_SetMapMode = _gdi32.SetMapMode
_SetMapMode.argtypes = (wintypes.HDC, ctypes.c_int)
_CreateFontIndirectW = _gdi32.CreateFontIndirectW
_CreateFontIndirectW.restype = wintypes.HFONT
_CreateFontIndirectW.argtypes = (ctypes.POINTER(LOGFONTW),)
_SelectObject = _gdi32.SelectObject
_SelectObject.restype = wintypes.HGDIOBJ
_SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
_DeleteObject = _gdi32.DeleteObject
_DeleteObject.argtypes = (wintypes.HGDIOBJ,)
_DeleteDC = _gdi32.DeleteDC
_DeleteDC.argtypes = (wintypes.HDC,)
_GetTextExtentPoint32W = _gdi32.GetTextExtentPoint32W
_GetTextExtentPoint32W.restype = wintypes.BOOL
_GetTextExtentPoint32W.argtypes = (wintypes.HDC, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(wintypes.SIZE))
_GetTextMetricsW = _gdi32.GetTextMetricsW
_GetTextMetricsW.restype = wintypes.BOOL
_GetTextMetricsW.argtypes = (wintypes.HDC, ctypes.POINTER(TEXTMETRICW))
_GetTextFaceW = _gdi32.GetTextFaceW
_GetTextFaceW.restype = ctypes.c_int
_GetTextFaceW.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_wchar_p)
_AddFontResourceExW = _gdi32.AddFontResourceExW
_AddFontResourceExW.restype = ctypes.c_int
_AddFontResourceExW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".fon", ".fnt")


def register_private_fonts(dirs: list[Path]) -> int:
    """Make the fonts in `dirs` available to this process only (AddFontResourceEx FR_PRIVATE)."""
    count = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() in FONT_SUFFIXES and _AddFontResourceExW(str(f), FR_PRIVATE, None):
                count += 1
    return count


def _utf16_units(text: str) -> bytes:
    return text.encode("utf-16-le", "surrogatepass")


class TextExtents:
    def __init__(self) -> None:
        self._warned: set[str] = set()
        self.warnings: list[str] = []

    @staticmethod
    @lru_cache(maxsize=256)
    def _logfont_key(height: int, weight: int, italic: int, underline: int, strikeout: int, charset: int,
                     face: str) -> LOGFONTW:
        lf = LOGFONTW()
        lf.lfHeight = height
        lf.lfWeight = weight
        lf.lfItalic = italic
        lf.lfUnderline = underline
        lf.lfStrikeOut = strikeout
        lf.lfCharSet = charset
        lf.lfOutPrecision = OUT_TT_PRECIS
        lf.lfClipPrecision = CLIP_DEFAULT_PRECIS
        lf.lfQuality = ANTIALIASED_QUALITY
        lf.lfPitchAndFamily = DEFAULT_PITCH | FF_DONTCARE
        lf.lfFaceName = face
        return lf

    def measure(self, style: AssStyle, text: str) -> tuple[float, float, float, float] | None:
        fontsize = style.fontsize * 64
        spacing = style.spacing * 64

        dc = _CreateCompatibleDC(None)
        if not dc:
            return None
        try:
            _SetMapMode(dc, MM_TEXT)
            # wcsncpy(lf.lfFaceName, ConvertW(style->font), 31)
            face_units = _utf16_units(to_wide(style.font))[: 31 * 2]
            face = face_units.decode("utf-16-le", "surrogatepass").split("\0", 1)[0]
            try:
                height = int(fontsize)
            except (OverflowError, ValueError):
                height = -(2**31)
            height = max(-(2**31), min(2**31 - 1, height))
            lf = self._logfont_key(height, FW_BOLD if style.bold else FW_NORMAL, int(style.italic),
                                   int(style.underline), int(style.strikeout), style.encoding & 0xFF, face)
            font = _CreateFontIndirectW(ctypes.byref(lf))
            if not font:
                return None
            old = _SelectObject(dc, font)
            try:
                self._check_face(dc, face)
                wtext = _utf16_units(to_wide(text))
                size = wintypes.SIZE()
                width = 0.0
                h = 0.0
                if spacing != 0:
                    for i in range(0, len(wtext), 2):
                        unit = ctypes.create_string_buffer(wtext[i : i + 2], 2)
                        _GetTextExtentPoint32W(dc, unit, 1, ctypes.byref(size))
                        width += size.cx + spacing
                        h = float(size.cy)
                else:
                    buf = ctypes.create_string_buffer(wtext + b"\0\0", len(wtext) + 2)
                    _GetTextExtentPoint32W(dc, buf, len(wtext) // 2, ctypes.byref(size))
                    width = float(size.cx)
                    h = float(size.cy)
                tm = TEXTMETRICW()
                _GetTextMetricsW(dc, ctypes.byref(tm))
                descent = float(tm.tmDescent)
                extlead = float(tm.tmExternalLeading)
            finally:
                _SelectObject(dc, old)
                _DeleteObject(font)
        finally:
            _DeleteDC(dc)

        return (
            style.scalex / 100 * width / 64,
            style.scaley / 100 * h / 64,
            style.scaley / 100 * descent / 64,
            style.scaley / 100 * extlead / 64,
        )

    def _check_face(self, dc: int, face: str) -> None:
        if face.lower() in self._warned:
            return
        buf = ctypes.create_unicode_buffer(LF_FACESIZE)
        _GetTextFaceW(dc, LF_FACESIZE, buf)
        actual = buf.value
        if actual.lower() != face.lower() and not face.startswith("@"):
            self._warned.add(face.lower())
            self.warnings.append(
                f"aegisub-render: font '{face}' is not installed; Windows substituted '{actual}' "
                "for aegisub.text_extents (Aegisub would do the same)\n"
            )
