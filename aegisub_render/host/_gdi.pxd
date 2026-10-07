# C declarations for _gdi.pyx (the subset of windows.h / wingdi.h it uses).

from libc.stddef cimport wchar_t

cdef extern from "windows.h" nogil:
    ctypedef void *HDC
    ctypedef void *HFONT
    ctypedef void *HGDIOBJ
    ctypedef long LONG
    ctypedef unsigned char BYTE
    ctypedef int BOOL
    ctypedef unsigned long DWORD

    ctypedef struct SIZE:
        LONG cx
        LONG cy

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
