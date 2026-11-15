"""ASS subtitle model, parser and writer (port of Aegisub 3.5.1 src/ass_*.cpp).

The model classes and the entry points of the parser and the writer are re-exported here (and
the classes again from the top-level package), so that `from aegisub_render import AssFile` is
all a caller needs. AssFile itself carries from_text / from_bytes / from_path / serialize /
export, so the module-level functions below are only needed for the lower-level steps.
"""

from aegisub_render.ass.color import Color
from aegisub_render.ass.entries import (
    AnyEntry,
    AssAttachment,
    AssDialogue,
    AssInfo,
    AssStyle,
    ExtradataEntry,
    Group,
    ProjectProperties,
    SubtitleFormatParseError,
    ssa_to_ass_alignment,
)
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.karaoke import KaraokeSyllable, parse_karaoke_syllables
from aegisub_render.ass.override import (
    Block,
    BlockType,
    OverrideParam,
    OverrideTag,
    ParamClass,
    VarType,
    parse_dialogue_blocks,
    parse_override_tags,
    set_strict_tag_parsing,
    strict_tag_parsing,
)
from aegisub_render.ass.parser import (
    AssParser,
    UnsupportedEncodingError,
    decode_subtitle_bytes,
    parse_ass_bytes,
    parse_ass_lines,
    parse_ass_text,
    read_ass_file,
    split_subtitle_text,
)
from aegisub_render.ass.time import ass_formatted, clamp_time, parse_time
from aegisub_render.ass.writer import header_lines, serialize_ass, serialize_export, write_ass_file

__all__ = [
    "AnyEntry",
    "AssAttachment",
    "AssDialogue",
    "AssFile",
    "AssInfo",
    "AssParser",
    "AssStyle",
    "Block",
    "BlockType",
    "Color",
    "ExtradataEntry",
    "Group",
    "KaraokeSyllable",
    "OverrideParam",
    "OverrideTag",
    "ParamClass",
    "ProjectProperties",
    "SubtitleFormatParseError",
    "UnsupportedEncodingError",
    "VarType",
    "ass_formatted",
    "clamp_time",
    "decode_subtitle_bytes",
    "header_lines",
    "parse_ass_bytes",
    "parse_ass_lines",
    "parse_ass_text",
    "parse_dialogue_blocks",
    "parse_karaoke_syllables",
    "parse_override_tags",
    "parse_time",
    "read_ass_file",
    "serialize_ass",
    "serialize_export",
    "set_strict_tag_parsing",
    "split_subtitle_text",
    "ssa_to_ass_alignment",
    "strict_tag_parsing",
    "write_ass_file",
]
