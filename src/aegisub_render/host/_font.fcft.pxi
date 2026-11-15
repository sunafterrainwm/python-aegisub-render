# C declarations for _font.fcft.pyx (the parts of fontconfig and HarfBuzz it uses), included by it.

from libc.stdint cimport int32_t, uint32_t

cdef extern from "fontconfig/fontconfig.h" nogil:
    ctypedef unsigned char FcChar8
    ctypedef int FcBool
    ctypedef struct FcConfig
    ctypedef struct FcPattern

    ctypedef enum FcResult:
        FcResultMatch

    ctypedef enum FcMatchKind:
        FcMatchPattern

    const char *FC_FAMILY
    const char *FC_FULLNAME
    const char *FC_FILE
    const char *FC_INDEX
    const char *FC_WEIGHT
    const char *FC_SLANT
    int FC_SLANT_ROMAN
    int FC_SLANT_ITALIC

    FcConfig *FcInitLoadConfigAndFonts()
    int FcWeightFromOpenType(int ot_weight)
    FcBool FcConfigAppFontAddDir(FcConfig *config, const FcChar8 *dir)
    FcBool FcConfigAppFontAddFile(FcConfig *config, const FcChar8 *file)
    FcPattern *FcPatternCreate()
    void FcPatternDestroy(FcPattern *p)
    FcBool FcPatternAddString(FcPattern *p, const char *object, const FcChar8 *s)
    FcBool FcPatternAddInteger(FcPattern *p, const char *object, int i)
    FcResult FcPatternGetString(const FcPattern *p, const char *object, int n, FcChar8 **s)
    FcResult FcPatternGetInteger(const FcPattern *p, const char *object, int n, int *i)
    FcBool FcConfigSubstitute(FcConfig *config, FcPattern *p, FcMatchKind kind)
    void FcDefaultSubstitute(FcPattern *pattern)
    FcPattern *FcFontMatch(FcConfig *config, FcPattern *p, FcResult *result)

cdef extern from "hb.h" nogil:
    ctypedef int hb_bool_t
    ctypedef uint32_t hb_codepoint_t
    ctypedef int32_t hb_position_t
    ctypedef struct hb_blob_t
    ctypedef struct hb_face_t
    ctypedef struct hb_font_t
    ctypedef struct hb_buffer_t
    ctypedef struct hb_feature_t

    ctypedef struct hb_font_extents_t:
        hb_position_t ascender
        hb_position_t descender
        hb_position_t line_gap

    ctypedef struct hb_glyph_extents_t:
        hb_position_t x_bearing
        hb_position_t y_bearing
        hb_position_t width
        hb_position_t height

    ctypedef struct hb_glyph_position_t:
        hb_position_t x_advance
        hb_position_t y_advance
        hb_position_t x_offset
        hb_position_t y_offset

    hb_blob_t *hb_blob_create_from_file(const char *file_name)
    unsigned int hb_blob_get_length(hb_blob_t *blob)
    void hb_blob_destroy(hb_blob_t *blob)
    hb_face_t *hb_face_create(hb_blob_t *blob, unsigned int index)
    unsigned int hb_face_get_upem(hb_face_t *face)
    void hb_face_destroy(hb_face_t *face)
    hb_font_t *hb_font_create(hb_face_t *face)
    void hb_font_destroy(hb_font_t *font)
    void hb_font_get_h_extents(hb_font_t *font, hb_font_extents_t *extents)
    hb_bool_t hb_font_get_nominal_glyph(hb_font_t *font, hb_codepoint_t unicode, hb_codepoint_t *glyph)
    hb_bool_t hb_font_get_glyph_extents(hb_font_t *font, hb_codepoint_t glyph, hb_glyph_extents_t *extents)
    hb_buffer_t *hb_buffer_create()
    void hb_buffer_destroy(hb_buffer_t *buffer)
    void hb_buffer_add_utf8(hb_buffer_t *buffer, const char *text, int text_length, unsigned int item_offset, int item_length)
    void hb_buffer_guess_segment_properties(hb_buffer_t *buffer)
    void hb_shape(hb_font_t *font, hb_buffer_t *buffer, const hb_feature_t *features, unsigned int num_features)
    hb_glyph_position_t *hb_buffer_get_glyph_positions(hb_buffer_t *buffer, unsigned int *length)
