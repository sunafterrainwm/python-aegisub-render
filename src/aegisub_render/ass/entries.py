"""Subtitle line types (src/ass_info.h, ass_style.cpp, ass_dialogue.cpp, ass_attachment.cpp, ass_file.h)."""

import itertools
import re
from dataclasses import dataclass, field
from enum import IntEnum

from aegisub_render.ass.color import Color
from aegisub_render.ass.time import ass_formatted, parse_time
from aegisub_render.cutil import boost_lexical_double, boost_lexical_int, format_g, mid, trim


class SubtitleFormatParseError(Exception):
    pass


class Group(IntEnum):
    """AssEntryGroup; the value indexes GROUP_HEADERS."""

    INFO = 0
    STYLE = 1
    FONT = 2
    GRAPHIC = 3
    DIALOGUE = 4
    EXTRADATA = 5


GROUP_HEADERS = (
    "[Script Info]",
    "[V4+ Styles]",
    "[Fonts]",
    "[Graphics]",
    "[Events]",
    "[Aegisub Extradata]",
)


class AssInfo:
    __slots__ = ("key", "value")
    group = Group.INFO

    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value

    def entry_data(self) -> str:
        return f"{self.key}: {self.value}"

    def copy(self) -> AssInfo:
        return AssInfo(self.key, self.value)


_SSA_TO_ASS = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}


class _StyleTokens:
    def __init__(self, text: str) -> None:
        colon = text.find(":")
        self.toks: list[str] = text[colon + 1 :].split(",") if colon >= 0 else []
        self.pos = 0

    def next_tok(self) -> str:
        if self.pos >= len(self.toks):
            raise SubtitleFormatParseError("Malformed style: not enough fields")
        tok = trim(self.toks[self.pos])
        self.pos += 1
        return tok

    def next_int(self) -> int:
        try:
            return boost_lexical_int(self.next_tok())
        except ValueError:
            raise SubtitleFormatParseError("Malformed style: bad int field") from None

    def next_double(self) -> float:
        try:
            return boost_lexical_double(self.next_tok())
        except ValueError:
            raise SubtitleFormatParseError("Malformed style: bad double field") from None

    def skip(self) -> None:
        if self.pos < len(self.toks):
            self.pos += 1

    def check_done(self) -> None:
        if self.pos < len(self.toks):
            raise SubtitleFormatParseError("Malformed style: too many fields")


class AssStyle:
    __slots__ = (
        "_data",
        "alignment",
        "angle",
        "bold",
        "borderstyle",
        "encoding",
        "font",
        "fontsize",
        "italic",
        "margin",
        "name",
        "outline",
        "outline_w",
        "primary",
        "scalex",
        "scaley",
        "secondary",
        "shadow",
        "shadow_w",
        "spacing",
        "strikeout",
        "underline",
    )
    group = Group.STYLE

    def __init__(self) -> None:
        self.name = "Default"
        self.font = "Arial"
        self.fontsize = 48.0
        self.primary = Color(255, 255, 255)
        self.secondary = Color(255, 0, 0)
        self.outline = Color(0, 0, 0)
        self.shadow = Color(0, 0, 0)
        self.bold = False
        self.italic = False
        self.underline = False
        self.strikeout = False
        self.scalex = 100.0
        self.scaley = 100.0
        self.spacing = 0.0
        self.angle = 0.0
        self.borderstyle = 1
        self.outline_w = 2.0
        self.shadow_w = 2.0
        self.alignment = 2
        self.margin = [10, 10, 10]
        self.encoding = 1
        self._data = ""
        self.update_data()

    @staticmethod
    def parse(text: str, version: int = 1) -> AssStyle:
        p = _StyleTokens(text)
        s = AssStyle()
        s.name = p.next_tok()
        s.font = p.next_tok()
        s.fontsize = p.next_double()
        if version != 0:
            s.primary = Color.parse(p.next_tok())
            s.secondary = Color.parse(p.next_tok())
            s.outline = Color.parse(p.next_tok())
            s.shadow = Color.parse(p.next_tok())
        else:
            s.primary = Color.parse(p.next_tok())
            s.secondary = Color.parse(p.next_tok())
            p.skip()
            s.outline = Color.parse(p.next_tok())
            s.shadow = Color(s.outline.r, s.outline.g, s.outline.b, s.outline.a)
        s.bold = p.next_int() != 0
        s.italic = p.next_int() != 0
        if version != 0:
            s.underline = p.next_int() != 0
            s.strikeout = p.next_int() != 0
            s.scalex = p.next_double()
            s.scaley = p.next_double()
            s.spacing = p.next_double()
            s.angle = p.next_double()
        else:
            s.underline = False
            s.strikeout = False
            s.scalex = 100.0
            s.scaley = 100.0
            s.spacing = 0.0
            s.angle = 0.0
        s.borderstyle = p.next_int()
        s.outline_w = p.next_double()
        s.shadow_w = p.next_double()
        s.alignment = p.next_int()
        if version == 0:
            s.alignment = _SSA_TO_ASS.get(s.alignment, 2)
        s.margin = [mid(-9999, p.next_int(), 99999) for _ in range(3)]
        if version == 0:
            p.skip()
        s.encoding = p.next_int()
        p.check_done()
        s.update_data()
        return s

    def update_data(self) -> None:
        self.name = self.name.replace(",", ";")
        self.font = self.font.replace(",", ";")
        fields = [
            self.name,
            self.font,
            format_g(self.fontsize),
            self.primary.ass_style(),
            self.secondary.ass_style(),
            self.outline.ass_style(),
            self.shadow.ass_style(),
            str(-1 if self.bold else 0),
            str(-1 if self.italic else 0),
            str(-1 if self.underline else 0),
            str(-1 if self.strikeout else 0),
            format_g(self.scalex),
            format_g(self.scaley),
            format_g(self.spacing),
            format_g(self.angle),
            str(self.borderstyle),
            format_g(self.outline_w),
            format_g(self.shadow_w),
            str(self.alignment),
            str(self.margin[0]),
            str(self.margin[1]),
            str(self.margin[2]),
            str(self.encoding),
        ]
        self._data = "Style: " + ",".join(fields)

    def entry_data(self) -> str:
        return self._data


_dialogue_ids = itertools.count(1)


class AssDialogue:
    __slots__ = (
        "actor",
        "comment",
        "effect",
        "end",
        "extradata_ids",
        "id",
        "layer",
        "margin",
        "start",
        "style",
        "text",
    )
    group = Group.DIALOGUE

    def __init__(self) -> None:
        self.id = next(_dialogue_ids)
        self.comment = False
        self.layer = 0
        self.margin = [0, 0, 0]
        self.start = 0  # ms; agi::Time
        self.end = 5000
        self.style = "Default"
        self.actor = ""
        self.effect = ""
        self.text = ""
        self.extradata_ids: list[int] = []

    @staticmethod
    def parse(raw: str) -> AssDialogue:
        d = AssDialogue()
        if raw.startswith("Dialogue:"):
            d.comment = False
            body = raw[min(len(raw), 10) :]
        elif raw.startswith("Comment:"):
            d.comment = True
            body = raw[min(len(raw), 9) :]
        else:
            raise SubtitleFormatParseError("Failed parsing line: " + raw)

        toks = body.split(",", 9)
        if len(toks) < 10:
            raise SubtitleFormatParseError("Failed parsing line: " + body)

        first = trim(toks[0])
        ssa = first.lower().startswith("marked=")
        try:
            d.layer = 0 if ssa else boost_lexical_int(first)
            d.start = parse_time(trim(toks[1]))
            d.end = parse_time(trim(toks[2]))
            d.style = trim(toks[3])
            d.actor = trim(toks[4])
            d.margin = [mid(-9999, boost_lexical_int(toks[5 + i]), 99999) for i in range(3)]
        except ValueError as e:
            raise SubtitleFormatParseError(f"Failed parsing line: {body} ({e})") from None
        d.effect = trim(toks[8])
        text = toks[9]

        if len(text) > 1 and text[0] == "{" and text[1] == "=":
            m = re.match(r"\{(=\d+)+\}", text)
            if m:
                ids = [int(x) & 0xFFFFFFFF for x in re.findall(r"=(\d+)", m.group(0))]
                d.extradata_ids = ids
                text = text[m.end() :]
        d.text = text
        return d

    def entry_data(self) -> str:
        parts = [
            "Comment: " if self.comment else "Dialogue: ",
            str(self.layer),
            ",",
            ass_formatted(self.start),
            ",",
            ass_formatted(self.end),
            ",",
            self.style.replace(",", ";"),
            ",",
            self.actor.replace(",", ";"),
            ",",
            str(self.margin[0]),
            ",",
            str(self.margin[1]),
            ",",
            str(self.margin[2]),
            ",",
            self.effect.replace(",", ";"),
            ",",
        ]
        if self.extradata_ids:
            parts.append("{" + "".join(f"={i}" for i in self.extradata_ids) + "}")
        parts.append(self.text.replace("\n", "").replace("\r", ""))
        return "".join(parts)


class AssAttachment:
    __slots__ = ("data", "filename", "group")

    def __init__(self, data: str, group: Group) -> None:
        self.data = data
        self.group = group
        name = data[10:]
        pos = name.find("\r\n")
        self.filename = name[:pos] if pos >= 0 else name

    def entry_data(self) -> str:
        return self.data


type AssEntry = AssInfo | AssStyle | AssDialogue
type AnyEntry = AssInfo | AssStyle | AssDialogue | AssAttachment


@dataclass(slots=True)
class ExtradataEntry:
    id: int
    key: str
    value: str


@dataclass(slots=True)
class ProjectProperties:
    automation_scripts: str = ""
    export_filters: str = ""
    export_encoding: str = ""
    style_storage: str = ""
    audio_file: str = ""
    video_file: str = ""
    timecodes_file: str = ""
    keyframes_file: str = ""
    automation_settings: dict[str, str] = field(default_factory=dict[str, str])
    video_zoom: float = 0.0
    ar_value: float = 0.0
    scroll_position: int = 0
    active_row: int = 0
    ar_mode: int = 0
    video_position: int = 0
