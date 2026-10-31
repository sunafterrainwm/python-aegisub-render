# C declarations for _font.gdi.pyx (the subset of windows.h / wingdi.h it uses), included by it.

from libc.stddef cimport wchar_t

cdef extern from "windows.h" nogil:
    ctypedef void *HDC
    ctypedef void *HFONT
    ctypedef void *HGDIOBJ
    ctypedef long LONG
    ctypedef short SHORT
    ctypedef unsigned short WORD
    ctypedef unsigned int UINT
    ctypedef unsigned char BYTE
    ctypedef int BOOL
    ctypedef unsigned long DWORD

    ctypedef struct SIZE:
        LONG cx
        LONG cy

    ctypedef struct POINT:
        LONG x
        LONG y

    ctypedef struct GLYPHMETRICS:
        UINT gmBlackBoxX
        UINT gmBlackBoxY
        POINT gmptGlyphOrigin
        SHORT gmCellIncX
        SHORT gmCellIncY

    ctypedef struct FIXED:
        WORD fract
        SHORT value

    ctypedef struct MAT2:
        FIXED eM11
        FIXED eM12
        FIXED eM21
        FIXED eM22

    ctypedef struct LOGFONTW:
        LONG lfHeight
        LONG lfWidth
        LONG lfEscapement
        LONG lfOrientation
        LONG lfWeight
        BYTE lfItalic
        BYTE lfUnderline
        BYTE lfStrikeOut
        BYTE lfCharSet
        BYTE lfOutPrecision
        BYTE lfClipPrecision
        BYTE lfQuality
        BYTE lfPitchAndFamily
        wchar_t lfFaceName[32]

    ctypedef struct TEXTMETRICW:
        LONG tmHeight
        LONG tmAscent
        LONG tmDescent
        LONG tmInternalLeading
        LONG tmExternalLeading

    enum:
        MM_TEXT
        FW_NORMAL
        FW_BOLD
        OUT_TT_PRECIS
        CLIP_DEFAULT_PRECIS
        ANTIALIASED_QUALITY
        DEFAULT_PITCH
        FF_DONTCARE
        FR_PRIVATE
        LF_FACESIZE
        GGO_METRICS
        GGO_NATIVE

    HDC CreateCompatibleDC(HDC hdc)
    int SetMapMode(HDC hdc, int mode)
    HFONT CreateFontIndirectW(const LOGFONTW *lplf)
    HGDIOBJ SelectObject(HDC hdc, HGDIOBJ h)
    BOOL DeleteObject(HGDIOBJ ho)
    BOOL DeleteDC(HDC hdc)
    BOOL GetTextExtentPoint32W(HDC hdc, const wchar_t *lpString, int c, SIZE *psizl)
    BOOL GetTextMetricsW(HDC hdc, TEXTMETRICW *lptm)
    int GetTextFaceW(HDC hdc, int c, wchar_t *lpName)
    int AddFontResourceExW(const wchar_t *name, DWORD fl, void *res)
    DWORD GetFontData(HDC hdc, DWORD dwTable, DWORD dwOffset, void *pvBuffer, DWORD cjBuffer)
    DWORD GetGlyphOutlineW(
        HDC hdc,
        UINT uChar,
        UINT fuFormat,
        GLYPHMETRICS *lpgm,
        DWORD cjBuffer,
        void *pvBuffer,
        const MAT2 *lpmat2,
    )
    DWORD GDI_ERROR


# The 'name' table tag as GetFontData wants it: the four tag bytes read as a little-endian DWORD.
cdef enum:
    TAG_NAME = 0x656D616E
