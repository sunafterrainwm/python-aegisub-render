# cython: language_level=3, boundscheck=False, wraparound=False, embedsignature=True, embedsignature.format=python, binding=True
"""GDI text measurement: the WIN32 branch of Automation4::CalculateTextExtents (src/auto4_base.cpp)."""

from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_GET_SIZE
from libc.string cimport memcpy, memset


cdef extern from "Python.h":
    object PyUnicode_FromWideChar(const wchar_t *w, Py_ssize_t size)


cdef bytes _wide(str text):
    """UTF-16 code units (wchar_t on Windows) followed by a terminating NUL."""
    return text.encode("utf-16-le", "surrogatepass") + b"\0\0"


cdef HFONT _create_font(
    bytes wface, LONG height, int weight, bint italic, bint underline, bint strikeout, int charset
):
    """The LOGFONT CalculateTextExtents builds; `wface` is copied like wcsncpy(lf.lfFaceName, face, 31)."""
    cdef LOGFONTW lf
    cdef Py_ssize_t face_units = (PyBytes_GET_SIZE(wface) - 2) // 2
    memset(&lf, 0, sizeof(lf))
    lf.lfHeight = height
    lf.lfWeight = weight
    lf.lfItalic = italic
    lf.lfUnderline = underline
    lf.lfStrikeOut = strikeout
    lf.lfCharSet = <BYTE>charset
    lf.lfOutPrecision = OUT_TT_PRECIS
    lf.lfClipPrecision = CLIP_DEFAULT_PRECIS
    lf.lfQuality = ANTIALIASED_QUALITY
    lf.lfPitchAndFamily = DEFAULT_PITCH | FF_DONTCARE
    if face_units > 31:
        face_units = 31
    memcpy(lf.lfFaceName, PyBytes_AS_STRING(wface), face_units * sizeof(wchar_t))
    return CreateFontIndirectW(&lf)


def measure(
    face: str,
    height: int,
    weight: int,
    italic: bool,
    underline: bool,
    strikeout: bool,
    charset: int,
    text: str,
    spacing: float,
) -> tuple[float, float, float, float, str] | None:
    """Select the font described by the arguments (as CalculateTextExtents builds its LOGFONT)
    and measure `text`.

    `face` is copied like wcsncpy(lf.lfFaceName, face, 31) and `weight` becomes lfWeight
    (CalculateTextExtents gives FW_BOLD or FW_NORMAL); `spacing` is in the same units as
    `height` (already multiplied by 64). Returns (width, height, descent, external leading,
    face actually selected by GDI), unscaled, or None if GDI failed.
    """
    cdef HDC dc
    cdef HFONT font
    cdef HGDIOBJ old
    cdef SIZE sz
    cdef TEXTMETRICW tm
    cdef double width = 0, h = 0
    cdef bytes wtext = _wide(text)
    cdef Py_ssize_t units = (PyBytes_GET_SIZE(wtext) - 2) // 2
    cdef const wchar_t *ptext = <const wchar_t *>PyBytes_AS_STRING(wtext)
    cdef Py_ssize_t i
    cdef wchar_t actual[32]

    dc = CreateCompatibleDC(NULL)
    if dc is NULL:
        return None
    try:
        SetMapMode(dc, MM_TEXT)
        font = _create_font(_wide(face), height, weight, italic, underline, strikeout, charset)
        if font is NULL:
            return None
        old = SelectObject(dc, font)
        try:
            if spacing != 0:
                for i in range(units):
                    GetTextExtentPoint32W(dc, ptext + i, 1, &sz)
                    width += sz.cx + spacing
                    h = sz.cy
            else:
                GetTextExtentPoint32W(dc, ptext, <int>units, &sz)
                width = sz.cx
                h = sz.cy
            GetTextMetricsW(dc, &tm)
            memset(actual, 0, sizeof(actual))
            GetTextFaceW(dc, 31, actual)
            actual_face = PyUnicode_FromWideChar(actual, -1)
            return width, h, <double>tm.tmDescent, <double>tm.tmExternalLeading, actual_face
        finally:
            SelectObject(dc, old)
            DeleteObject(font)
    finally:
        DeleteDC(dc)


def glyph_ink_boxes(
    face: str,
    height: int,
    weight: int,
    italic: bool,
    underline: bool,
    strikeout: bool,
    charset: int,
    text: str,
) -> tuple[int, list[tuple[bool, int, int, int, int]]] | None:
    """Query GetTextMetricsW + GetGlyphOutlineW for each character in `text`.

    Returns `(tmAscent, [(has_ink, origin_x, origin_y, blackbox_x, blackbox_y), ...])`, unscaled,
    or None if GDI failed.
    """
    cdef HDC dc
    cdef HFONT font
    cdef HGDIOBJ old
    cdef TEXTMETRICW tm
    cdef GLYPHMETRICS gm
    cdef MAT2 mat
    cdef DWORD res, res_m
    cdef UINT code
    cdef bint has_ink
    cdef list boxes = []
    cdef str ch

    memset(&mat, 0, sizeof(mat))
    mat.eM11.value = 1
    mat.eM22.value = 1

    dc = CreateCompatibleDC(NULL)
    if dc is NULL:
        return None
    try:
        SetMapMode(dc, MM_TEXT)
        font = _create_font(_wide(face), height, weight, italic, underline, strikeout, charset)
        if font is NULL:
            return None
        old = SelectObject(dc, font)
        try:
            if not GetTextMetricsW(dc, &tm):
                return None
            for ch in text:
                memset(&gm, 0, sizeof(gm))
                code = <UINT>ord(ch)
                res = GetGlyphOutlineW(dc, code, GGO_NATIVE, &gm, 0, NULL, &mat)
                if res == GDI_ERROR:
                    res_m = GetGlyphOutlineW(dc, code, GGO_METRICS, &gm, 0, NULL, &mat)
                    has_ink = (
                        res_m != GDI_ERROR
                        and not ch.isspace()
                        and gm.gmBlackBoxX > 0
                        and gm.gmBlackBoxY > 0
                    )
                else:
                    has_ink = res > 0 and gm.gmBlackBoxX > 0 and gm.gmBlackBoxY > 0
                boxes.append(
                    (
                        bool(has_ink),
                        <int>gm.gmptGlyphOrigin.x,
                        <int>gm.gmptGlyphOrigin.y,
                        <int>gm.gmBlackBoxX,
                        <int>gm.gmBlackBoxY,
                    )
                )
            return <int>tm.tmAscent, boxes
        finally:
            SelectObject(dc, old)
            DeleteObject(font)
    finally:
        DeleteDC(dc)


def name_table(face: str, weight: int, italic: bool, charset: int) -> bytes | None:
    """The OpenType 'name' table of the font GDI selects for these attributes, or None if it has
    none (bitmap fonts) or GDI failed."""
    cdef HDC dc
    cdef HFONT font
    cdef HGDIOBJ old
    cdef DWORD size
    cdef bytearray data

    dc = CreateCompatibleDC(NULL)
    if dc is NULL:
        return None
    try:
        font = _create_font(_wide(face), 0, weight, italic, False, False, charset)
        if font is NULL:
            return None
        old = SelectObject(dc, font)
        try:
            size = GetFontData(dc, TAG_NAME, 0, NULL, 0)
            if size == GDI_ERROR or size == 0:
                return None
            data = bytearray(size)
            if GetFontData(dc, TAG_NAME, 0, <char *>data, size) != size:
                return None
            return bytes(data)
        finally:
            SelectObject(dc, old)
            DeleteObject(font)
    finally:
        DeleteDC(dc)


def add_private_font(path: str) -> bool:
    """AddFontResourceEx(path, FR_PRIVATE): make a font file available to this process only."""
    cdef bytes wpath = _wide(path)
    return AddFontResourceExW(<const wchar_t *>PyBytes_AS_STRING(wpath), FR_PRIVATE, NULL) != 0
