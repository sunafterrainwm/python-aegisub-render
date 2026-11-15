"""Subtitle line types (src/ass_info.h, ass_style.cpp, ass_dialogue.cpp, ass_attachment.cpp, ass_file.h)."""

import copy
import itertools
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar, Self

from aegisub_render._crt import format_g
from aegisub_render.ass.color import Color
from aegisub_render.ass.override import BlockType, parse_dialogue_blocks
from aegisub_render.ass.time import ass_formatted, parse_time
from aegisub_render.cutil import boost_lexical_double, boost_lexical_int, mid, trim


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


@dataclass(slots=True, eq=False)
class AssInfo:
    """AssInfo. `eq=False` everywhere in this module: an entry is an identity, as in Aegisub."""

    key: str
    value: str

    group: ClassVar[Group] = Group.INFO

    def entry_data(self) -> str:
        return f"{self.key}: {self.value}"

    def copy(self) -> Self:
        return type(self)(self.key, self.value)


_SSA_TO_ASS = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}


def ssa_to_ass_alignment(value: int) -> int:
    """An SSA alignment as the matching `\\an` value (1-9).

    AssStyle::Parse's table for a v4 (SSA) style line. The legacy `\\a` override tag uses the
    same numbering, so this is also how to read one. A number that is not an SSA alignment
    becomes 2, the fallback Aegisub's lookup leaves in place.
    """
    return _SSA_TO_ASS.get(value, 2)


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


@dataclass(kw_only=True, slots=True, eq=False)
class AssStyle:
    """AssStyle.

    Aegisub keeps the rendered `Style:` line in a `data` member that `UpdateData()` refreshes;
    here `entry_data()` builds it from the fields on demand, so there is nothing to keep in sync
    and no `update_data()` to call. UpdateData's other job -- replacing the commas in Name and
    Font, which would otherwise split the line -- happens on construction and again on output.
    """

    name: str = "Default"
    font: str = "Arial"
    fontsize: float = 48.0
    primary: Color = field(default_factory=lambda: Color(255, 255, 255))
    secondary: Color = field(default_factory=lambda: Color(255, 0, 0))
    outline: Color = field(default_factory=lambda: Color(0, 0, 0))
    shadow: Color = field(default_factory=lambda: Color(0, 0, 0))
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    scalex: float = 100.0
    scaley: float = 100.0
    spacing: float = 0.0
    angle: float = 0.0
    borderstyle: int = 1
    outline_w: float = 2.0
    shadow_w: float = 2.0
    alignment: int = 2
    margin: list[int] = field(default_factory=lambda: [10, 10, 10])
    encoding: int = 1

    group: ClassVar[Group] = Group.STYLE

    def __post_init__(self) -> None:
        self.name = self.name.replace(",", ";")
        self.font = self.font.replace(",", ";")

    @classmethod
    def parse(cls, text: str, version: int = 1) -> Self:
        """Build a style from a `Style:` line; cls(...) so a subclass parses into itself."""
        p = _StyleTokens(text)
        name = p.next_tok()
        font = p.next_tok()
        fontsize = p.next_double()
        primary = Color.parse(p.next_tok())
        secondary = Color.parse(p.next_tok())
        if version != 0:
            outline = Color.parse(p.next_tok())
            shadow = Color.parse(p.next_tok())
        else:
            p.skip()
            outline = Color.parse(p.next_tok())
            shadow = Color(outline.r, outline.g, outline.b, outline.a)
        bold = p.next_int() != 0
        italic = p.next_int() != 0
        if version != 0:
            underline = p.next_int() != 0
            strikeout = p.next_int() != 0
            scalex = p.next_double()
            scaley = p.next_double()
            spacing = p.next_double()
            angle = p.next_double()
        else:
            underline = False
            strikeout = False
            scalex = 100.0
            scaley = 100.0
            spacing = 0.0
            angle = 0.0
        borderstyle = p.next_int()
        outline_w = p.next_double()
        shadow_w = p.next_double()
        alignment = p.next_int()
        if version == 0:
            alignment = ssa_to_ass_alignment(alignment)
        margin = [mid(-9999, p.next_int(), 99999) for _ in range(3)]
        if version == 0:
            p.skip()
        encoding = p.next_int()
        p.check_done()
        return cls(
            name=name,
            font=font,
            fontsize=fontsize,
            primary=primary,
            secondary=secondary,
            outline=outline,
            shadow=shadow,
            bold=bold,
            italic=italic,
            underline=underline,
            strikeout=strikeout,
            scalex=scalex,
            scaley=scaley,
            spacing=spacing,
            angle=angle,
            borderstyle=borderstyle,
            outline_w=outline_w,
            shadow_w=shadow_w,
            alignment=alignment,
            margin=margin,
            encoding=encoding,
        )

    def entry_data(self) -> str:
        """AssStyle::GetEntryData, rendered from the current field values."""
        fields = [
            self.name.replace(",", ";"),
            self.font.replace(",", ";"),
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
        return "Style: " + ",".join(fields)

    def copy(self) -> Self:
        """A separate style with the same values (Aegisub clones styles with its copy ctor)."""
        return copy.deepcopy(self)


_dialogue_ids = itertools.count(1)


@dataclass(kw_only=True, slots=True, eq=False)
class AssDialogue:
    """AssDialogue. `id` defaults to the next value of the module-wide counter, as in Aegisub."""

    id: int = field(init=False)
    comment: bool = False
    layer: int = 0
    start: int = 0  # ms; agi::Time
    end: int = 5000
    style: str = "Default"
    actor: str = ""
    effect: str = ""
    text: str = ""
    margin: list[int] = field(default_factory=lambda: [0, 0, 0])
    extradata_ids: list[int] = field(default_factory=list[int])

    group: ClassVar[Group] = Group.DIALOGUE

    def __post_init__(self) -> None:
        self.id = next(_dialogue_ids)

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Build an event from a `Dialogue:`/`Comment:` line; cls(...) so a subclass parses into itself."""
        if raw.startswith("Dialogue:"):
            comment = False
            body = raw[min(len(raw), 10) :]
        elif raw.startswith("Comment:"):
            comment = True
            body = raw[min(len(raw), 9) :]
        else:
            raise SubtitleFormatParseError("Failed parsing line: " + raw)

        toks = body.split(",", 9)
        if len(toks) < 10:
            raise SubtitleFormatParseError("Failed parsing line: " + body)

        first = trim(toks[0])
        ssa = first.lower().startswith("marked=")
        try:
            layer = 0 if ssa else boost_lexical_int(first)
            start = parse_time(trim(toks[1]))
            end = parse_time(trim(toks[2]))
            style = trim(toks[3])
            actor = trim(toks[4])
            margin = [mid(-9999, boost_lexical_int(toks[5 + i]), 99999) for i in range(3)]
        except ValueError as e:
            raise SubtitleFormatParseError(f"Failed parsing line: {body} ({e})") from None
        effect = trim(toks[8])
        text = toks[9]

        extradata_ids: list[int] = []
        if len(text) > 1 and text[0] == "{" and text[1] == "=":
            m = re.match(r"\{(=\d+)+\}", text)
            if m:
                extradata_ids = [int(x) & 0xFFFFFFFF for x in re.findall(r"=(\d+)", m.group(0))]
                text = text[m.end() :]
        return cls(
            comment=comment,
            layer=layer,
            start=start,
            end=end,
            style=style,
            actor=actor,
            effect=effect,
            text=text,
            margin=margin,
            extradata_ids=extradata_ids,
        )

    def get_stripped_text(self) -> str:
        """AssDialogue::GetStrippedText: the plain-text blocks of Text, joined.

        So the `{...}` override and comment blocks are gone, and drawings with them (a drawing is
        not a plain block). `\\N`, `\\n` and `\\h` are part of the plain text and stay as the two
        characters they are -- Aegisub does not turn them into whitespace here.
        """
        return "".join(b.text for b in parse_dialogue_blocks(self.text) if b.type is BlockType.PLAIN)

    def strip_tags(self) -> None:
        """AssDialogue::StripTags: replace Text with get_stripped_text()."""
        self.text = self.get_stripped_text()

    def copy(self) -> Self:
        """A copy with a new id, like Aegisub's copy constructor (`Id = ++next_id`).

        A subclass that adds state gets it deep-copied too; only the id is reassigned.
        """
        d = copy.deepcopy(self)
        d.id = next(_dialogue_ids)
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


@dataclass(slots=True, eq=False)
class AssAttachment:
    """AssAttachment; `data` is the whole `fontname:`/`filename:` header plus the UUE payload."""

    data: str
    group: Group

    @property
    def filename(self) -> str:
        """The name on `data`'s header line, read on demand so it follows a replaced `data`."""
        name = self.data[10:]
        pos = name.find("\r\n")
        return name[:pos] if pos >= 0 else name

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
