# cython: language_level=3, boundscheck=False, wraparound=False, embedsignature=True, embedsignature.format=python, binding=True
"""aegisub_render.host._font as built outside Windows (or forced with the meson option
font_backend=fcft): the non-WIN32 branch of Automation4::CalculateTextExtents (src/auto4_base.cpp).

Aegisub measures there with wxMemoryDC::GetTextExtent, a Pango layout on GTK, and then scales
everything by fontsize / line height. Here the font is chosen the way libass chooses it
(fontconfig: family, weight, slant, FcConfigSubstitute + FcDefaultSubstitute + FcFontMatch) and
measured with HarfBuzz (shaped advances, hb_font_get_h_extents for the line height, as Pango does
since it moved to HarfBuzz). _font.gdi.pyx is the other build of the same module; both have the
same functions, signatures and docstrings, so one _font.pyi describes either.
"""

from pathlib import Path

from cpython.bytes cimport PyBytes_AS_STRING

from aegisub_render.host.fontnames import font_files

include "_font.fcft.pxi"

# Pango lays text out at 96 dpi: a font of N points is N * 96 / 72 pixels.
cdef double PANGO_DPI = 96.0

cdef FcConfig *_config = FcInitLoadConfigAndFonts()


cdef class _Font:
    """A matched font: its HarfBuzz font (scale = units per em) and fontconfig's names for it."""

    cdef hb_font_t *font
    cdef double upem
    cdef double ascender  # above the baseline, font units
    cdef double descender  # below the baseline, font units (positive)
    cdef str family
    cdef list names

    def __dealloc__(self):
        if self.font is not NULL:
            hb_font_destroy(self.font)

    cdef double line_height(self):
        cdef double h = self.ascender + self.descender
        return h if h > 0 else self.upem

    cdef double advance(self, str text):
        cdef bytes utf8 = text.encode("utf-8")
        cdef hb_buffer_t *buf = hb_buffer_create()
        cdef unsigned int n = 0, i
        cdef hb_glyph_position_t *pos
        cdef double total = 0
        hb_buffer_add_utf8(buf, PyBytes_AS_STRING(utf8), <int>len(utf8), 0, <int>len(utf8))
        hb_buffer_guess_segment_properties(buf)
        hb_shape(self.font, buf, NULL, 0)
        pos = hb_buffer_get_glyph_positions(buf, &n)
        for i in range(n):
            total += pos[i].x_advance
        hb_buffer_destroy(buf)
        return total


cdef dict _fonts = {}


cdef list _strings(FcPattern *pattern, const char *obj):
    cdef list out = []
    cdef FcChar8 *s
    cdef int n = 0
    while FcPatternGetString(pattern, obj, n, &s) == FcResultMatch:
        out.append((<bytes>(<char *>s)).decode("utf-8", "replace"))
        n += 1
    return out


cdef _Font _match(str face, int weight, bint italic):
    key = (face, weight, italic)
    cached = _fonts.get(key)
    if cached is not None:
        return <_Font>cached
    cdef bytes family = face.encode("utf-8")
    cdef FcPattern *pattern = FcPatternCreate()
    cdef FcPattern *match
    cdef FcResult result
    cdef FcChar8 *file
    cdef int index = 0
    cdef hb_blob_t *blob
    cdef hb_face_t *hb_face
    cdef hb_font_extents_t ext
    cdef _Font font = _Font()
    FcPatternAddString(pattern, FC_FAMILY, <const FcChar8 *>PyBytes_AS_STRING(family))
    FcPatternAddInteger(pattern, FC_WEIGHT, FcWeightFromOpenType(weight))  # 400 -> REGULAR, 700 -> BOLD
    FcPatternAddInteger(pattern, FC_SLANT, FC_SLANT_ITALIC if italic else FC_SLANT_ROMAN)
    FcConfigSubstitute(_config, pattern, FcMatchPattern)
    FcDefaultSubstitute(pattern)
    match = FcFontMatch(_config, pattern, &result)
    FcPatternDestroy(pattern)
    if match is NULL:
        return None
    try:
        if FcPatternGetString(match, FC_FILE, 0, &file) != FcResultMatch:
            return None
        FcPatternGetInteger(match, FC_INDEX, 0, &index)
        blob = hb_blob_create_from_file(<const char *>file)
        if hb_blob_get_length(blob) == 0:
            hb_blob_destroy(blob)
            return None
        hb_face = hb_face_create(blob, <unsigned int>(index & 0xFFFF))  # the upper bits name a variation instance
        hb_blob_destroy(blob)
        font.font = hb_font_create(hb_face)  # scale defaults to units per em
        font.upem = hb_face_get_upem(hb_face)
        hb_face_destroy(hb_face)
        hb_font_get_h_extents(font.font, &ext)
        font.ascender = ext.ascender
        font.descender = -ext.descender
        font.family = (_strings(match, FC_FAMILY) or [face])[0]
        font.names = _strings(match, FC_FAMILY) + _strings(match, FC_FULLNAME)
    finally:
        FcPatternDestroy(match)
    _fonts[key] = font
    return font


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
    """Select the font for these attributes and measure `text` as Automation4::CalculateTextExtents
    does on this platform.

    Windows: GDI, with the LOGFONT it builds (lfHeight = `height`, lfWeight = `weight`, the face
    copied like wcsncpy(lfFaceName, face, 31)). Elsewhere: wxWidgets' Pango, reproduced with
    fontconfig and HarfBuzz (the line height scaled to `height`, external leading 0; underline,
    strike-out and the charset change nothing). `height` and `spacing` are the font size and
    spacing times 64. Returns (width, height, descent, external leading, face actually used),
    unscaled, or None.
    """
    cdef _Font font = _match(face, weight, italic)
    if font is None:
        return None
    cdef double size = height
    cdef double line = font.line_height()
    cdef double width = 0, h = 0, descent = 0
    cdef double spacing_scale
    if spacing != 0:
        # wx: scaling = fontsize / b, where b is the line height in pixels of a (int)fontsize pt font
        spacing_scale = size / (line / font.upem * <long>size * PANGO_DPI / 72.0) if size != 0 else 0
        for ch in text:
            width += font.advance(ch) * size / line + spacing * spacing_scale
            h = size
            descent = font.descender * size / line
    else:
        width = font.advance(text) * size / line
        h = size
        descent = font.descender * size / line
    return width, h, descent, 0.0, font.family


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
    """The ink box of each character of `text` in the font measure() uses (GetGlyphOutlineW on
    Windows, HarfBuzz glyph extents elsewhere).

    Returns `(ascent, [(has_ink, origin_x, origin_y, blackbox_x, blackbox_y), ...])` in the units
    of measure(), the origin being the top left corner of the ink relative to the pen position
    (y up), or None.
    """
    cdef _Font font = _match(face, weight, italic)
    if font is None:
        return None
    cdef double s = height / font.line_height()
    cdef hb_codepoint_t glyph
    cdef hb_glyph_extents_t ext
    cdef list boxes = []
    for ch in text:
        if (
            hb_font_get_nominal_glyph(font.font, <hb_codepoint_t>ord(ch), &glyph)
            and hb_font_get_glyph_extents(font.font, glyph, &ext)
            and ext.width > 0
            and ext.height < 0
        ):
            boxes.append(
                (True, round(ext.x_bearing * s), round(ext.y_bearing * s), round(ext.width * s), round(-ext.height * s))
            )
        else:
            boxes.append((False, 0, 0, 0, 0))
    return round(font.ascender * s), boxes


def face_names(face: str, weight: int, italic: bool, charset: int) -> list[str]:
    """Every family and full name, in every language it has them in, of the font the system picks
    for `face` (its 'name' table on Windows, fontconfig elsewhere); empty if there are none."""
    cdef _Font font = _match(face, weight, italic)
    return [] if font is None else sorted(set(font.names))


def add_private_font(path: str) -> bool:
    """Make a font file available to this process only (AddFontResourceEx with FR_PRIVATE on
    Windows, FcConfigAppFontAddFile elsewhere)."""
    cdef bytes p = path.encode("utf-8", "surrogateescape")
    _fonts.clear()
    return FcConfigAppFontAddFile(_config, <const FcChar8 *>PyBytes_AS_STRING(p)) != 0


def add_private_font_dir(path: str) -> int:
    """Make the font files in a directory and its subdirectories available to this process only
    (each with add_private_font on Windows, FcConfigAppFontAddDir elsewhere); returns how many."""
    cdef bytes p = path.encode("utf-8", "surrogateescape")
    cdef list files = font_files(Path(path))
    _fonts.clear()
    if not files or FcConfigAppFontAddDir(_config, <const FcChar8 *>PyBytes_AS_STRING(p)) == 0:
        return 0
    return len(files)


def backend() -> str:
    """Which build of this module is loaded: "gdi" (GDI, the Windows build) or "fcft" (fontconfig and
    HarfBuzz, the build used elsewhere, which Windows can also be built with)."""
    return "fcft"
