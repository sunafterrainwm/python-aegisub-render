# cython: language_level=3, boundscheck=False, wraparound=False, embedsignature=True, embedsignature.format=python, binding=True
"""GDI text measurement: the WIN32 branch of Automation4::CalculateTextExtents (src/auto4_base.cpp)."""

from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_GET_SIZE
from libc.string cimport memcpy, memset


cdef extern from "Python.h":
    object PyUnicode_FromWideChar(const wchar_t *w, Py_ssize_t size)


cdef bytes _wide(str text):
    """UTF-16 code units (wchar_t on Windows) followed by a terminating NUL."""
    return text.encode("utf-16-le", "surrogatepass") + b"\0\0"


def measure(
    face: str,
    height: int,
    bold: bool,
    italic: bool,
    underline: bool,
    strikeout: bool,
    charset: int,
    text: str,
    spacing: float,
) -> tuple[float, float, float, float, str] | None:
    """Select the font described by the arguments (as CalculateTextExtents builds its LOGFONT)
    and measure `text`.

    `face` is copied like wcsncpy(lf.lfFaceName, face, 31); `spacing` is in the same units as
    `height` (already multiplied by 64). Returns (width, height, descent, external leading,
    face actually selected by GDI), unscaled, or None if GDI failed.
    """
    cdef LOGFONTW lf
    cdef HDC dc
    cdef HFONT font
    cdef HGDIOBJ old
    cdef SIZE sz
    cdef TEXTMETRICW tm
    cdef double width = 0, h = 0
    cdef bytes wface = _wide(face)
    cdef bytes wtext = _wide(text)
    cdef Py_ssize_t units = (PyBytes_GET_SIZE(wtext) - 2) // 2
    cdef Py_ssize_t face_units = (PyBytes_GET_SIZE(wface) - 2) // 2
    cdef const wchar_t *ptext = <const wchar_t *>PyBytes_AS_STRING(wtext)
    cdef Py_ssize_t i
    cdef wchar_t actual[32]

    dc = CreateCompatibleDC(NULL)
    if dc is NULL:
        return None
    try:
        SetMapMode(dc, MM_TEXT)
        memset(&lf, 0, sizeof(lf))
        lf.lfHeight = height
        lf.lfWeight = FW_BOLD if bold else FW_NORMAL
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

        font = CreateFontIndirectW(&lf)
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


def add_private_font(path: str) -> bool:
    """AddFontResourceEx(path, FR_PRIVATE): make a font file available to this process only."""
    cdef bytes wpath = _wide(path)
    return AddFontResourceExW(<const wchar_t *>PyBytes_AS_STRING(wpath), FR_PRIVATE, NULL) != 0
