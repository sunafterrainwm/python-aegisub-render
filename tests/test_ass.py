"""ASS model, parser and writer (ports of Aegisub's C++)."""

from dataclasses import dataclass
from pathlib import Path
from typing import override

import pytest

from aegisub_render.ass.codec import inline_string_decode, inline_string_encode, uudecode, uuencode
from aegisub_render.ass.color import Color
from aegisub_render.ass.entries import (
    AssAttachment,
    AssDialogue,
    AssInfo,
    AssStyle,
    ExtradataEntry,
    Group,
    SubtitleFormatParseError,
    ssa_to_ass_alignment,
)
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.karaoke import parse_karaoke_syllables
from aegisub_render.ass.override import (
    BlockType,
    OverrideTag,
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
from aegisub_render.ass.time import ass_formatted, parse_time, time_int
from aegisub_render.ass.writer import header_lines, serialize_ass, serialize_export


@pytest.mark.parametrize(
    ("text", "rgba"),
    [
        ("&H00FFFFFF", (255, 255, 255, 0)),
        ("&H80112233", (0x33, 0x22, 0x11, 0x80)),
        ("&HFFFFFF&", (255, 255, 255, 0)),
        ("&H00FFFFFF&", (255, 255, 255, 0)),
        ("H112233", (0x33, 0x22, 0x11, 0)),
        ("16777215", (255, 255, 255, 0)),
        ("-1", (255, 255, 255, 255)),
        ("#FF8000", (255, 128, 0, 0)),
        ("#F80", (255, 136, 0, 0)),
        ("rgb(1, 2, 3)", (1, 2, 3, 0)),
        ("rgba(1,2,3,4)", (1, 2, 3, 4)),
        ("12AB34CD", (12, 0, 0, 0)),  # int_ matched a prefix, so its semantic action already ran
        ("&Hnope", (0, 0, 0, 0)),
    ],
)
def test_color_parse(text: str, rgba: tuple[int, int, int, int]) -> None:
    c = Color.parse(text)
    assert (c.r, c.g, c.b, c.a) == rgba


def test_color_format() -> None:
    c = Color(1, 2, 3, 4)
    assert c.ass_style() == "&H04030201"
    assert c.ass_override() == "&H030201&"


def test_time() -> None:
    assert parse_time("0:00:01.23") == 1230
    assert parse_time("1:02:03.456") == 3723456
    assert parse_time("1:2") == 62000
    assert parse_time("99:00:00.00") == 10 * 3600 * 1000 - 6
    assert time_int(1234) == 1230
    assert time_int(1235) == 1240
    assert ass_formatted(1235) == "0:00:01.24"
    assert ass_formatted(3723456, ms_precision=True) == "1:02:03.456"


def test_dialogue_round_trip() -> None:
    raw = "Dialogue: 2,0:00:01.00,0:00:02.50,Sty,Act,0001,0,10,fx,{=3=5}some, text"
    d = AssDialogue.parse(raw)
    assert (d.layer, d.start, d.end, d.style, d.actor, d.margin, d.effect) == (
        2,
        1000,
        2500,
        "Sty",
        "Act",
        [1, 0, 10],
        "fx",
    )
    assert d.extradata_ids == [3, 5]
    assert d.text == "some, text"
    assert d.entry_data() == "Dialogue: 2,0:00:01.00,0:00:02.50,Sty,Act,1,0,10,fx,{=3=5}some, text"
    d.style = "a,b"
    d.text = "x\ny"
    assert d.entry_data() == "Dialogue: 2,0:00:01.00,0:00:02.50,a;b,Act,1,0,10,fx,{=3=5}xy"
    c = AssDialogue.parse("Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,,")
    assert c.comment and c.text == ""
    with pytest.raises(SubtitleFormatParseError):
        AssDialogue.parse("Dialogue: 0,0:00:00.00")


def test_style_round_trip() -> None:
    raw = "Style: A,Arial,48.5,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,95.5,0,0,1,2,1.5,8,10,20,30,128"
    s = AssStyle.parse(raw)
    assert s.bold and not s.italic and s.fontsize == 48.5 and s.scaley == 95.5 and s.alignment == 8
    assert s.entry_data() == raw
    with pytest.raises(SubtitleFormatParseError, match="too many fields"):
        AssStyle.parse(raw + ",1")


def test_parser_and_writer() -> None:
    text = "\n".join(
        [
            "﻿[Script Info]",
            "; a comment that Aegisub drops",
            "Title: x",
            "ScriptType: v4.00+",
            "Collisions: Normal",
            "PlayResX: 640",
            "",
            "[Aegisub Project Garbage]",
            "Video File: video.mkv",
            "Video AR Value: 1.777778",
            "Active Line: 3",
            "Automation Settings foo: bar",
            "",
            "[V4+ Styles]",
            "Format: whatever",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1",
            "",
            "[Some Unknown Section]",
            "dropped: yes",
            "",
            "[Events]",
            "Format: whatever",
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{=1}hello   ",
            "",
            "[Aegisub Extradata]",
            "Data: 1,key,evalue#3Awith#2Cescapes",
        ]
    )
    ass = parse_ass_lines(decode_subtitle_bytes(text.encode("utf-8")))
    assert [i.key for i in ass.info] == ["Title", "ScriptType", "PlayResX"]
    assert ass.properties.video_file == "video.mkv"
    assert ass.properties.active_row == 3
    assert ass.properties.automation_settings == {" foo": "bar"}
    assert ass.events[0].text == "{=1}hello"[4:] and ass.events[0].extradata_ids == [1]
    assert ass.extradata[0].value == "value:with,escapes"
    assert ass.get_resolution() == (640, 480)

    out = serialize_ass(ass)
    assert out.startswith(
        b"\xef\xbb\xbf[Script Info]\r\n; Script generated by Aegisub 3.5.1 with aegisub-render 0.1.0\r\n; https://aegisub.org/\r\n"
    )
    assert b"\n" not in out.replace(b"\r\n", b"")
    lines = out.decode("utf-8-sig").split("\r\n")
    assert lines[3:6] == ["Title: x", "ScriptType: v4.00+", "PlayResX: 640"]
    assert lines[6:11] == [
        "",
        "[Aegisub Project Garbage]",
        "Video File: video.mkv",
        "Video AR Value: 1.777778",
        "Active Line: 3",
    ]
    assert "Data: 1,key,evalue#3Awith#2Cescapes" in lines
    assert "[Some Unknown Section]" not in lines
    without_ui = serialize_ass(ass, save_ui_state=False).decode("utf-8-sig")
    assert "Active Line" not in without_ui

    # Export keeps the sections the input had, written as Save writes them ...
    assert serialize_export(ass, save_ui_state=False) == serialize_ass(ass, save_ui_state=False)
    # ... and otherwise leaves them out like ExportFile, although GetEntryData still writes the
    # extradata ids.
    ass.had_project_garbage = ass.had_extradata = False
    exported = serialize_export(ass).decode("utf-8-sig").split("\r\n")
    assert exported[3:8] == ["Title: x", "ScriptType: v4.00+", "PlayResX: 640", "", "[V4+ Styles]"]
    assert "[Aegisub Project Garbage]" not in exported and "[Aegisub Extradata]" not in exported
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{=1}hello" in exported


def test_attachments_survive() -> None:
    data = bytes(range(256)) * 3
    encoded = uuencode(data, True)
    assert uudecode(encoded) == data
    lines = ["[Fonts]", "fontname: a_0.ttf", *encoded.split("\r\n"), "", "[Events]"]
    ass = parse_ass_lines(lines)
    assert len(ass.attachments) == 1
    assert ass.attachments[0].filename == "a_0.ttf"
    assert uudecode(ass.attachments[0].data.split("\r\n", 1)[1]) == data


def test_inline_string_codec() -> None:
    s = "a,b:c|d#e\x01"
    assert inline_string_encode(s) == "a#2Cb#3Ac#7Cd#23e#01"
    assert inline_string_decode(inline_string_encode(s)) == s


@pytest.mark.parametrize(
    ("tag", "normalized"),
    [
        ("\\fs 20 ", "\\fs20"),
        ("\\fs(20)", "\\fs20"),
        ("\\pos( 1 , 2 )", "\\pos(1,2)"),
        ("\\pos", "\\pos()"),
        ("\\t(\\fs20)", "\\t(\\fs20)"),
        ("\\t(0,100,\\fs20)", "\\t(0,100,\\fs20)"),
        ("\\t(0,100,2,\\fs20)", "\\t(0,100,2,\\fs20)"),
        ("\\clip(m 0 0 l 1 1)", "\\clip(m 0 0 l 1 1)"),
        ("\\clip(1,2,3,4)", "\\clip(1,2,3,4)"),
        ("\\-fx", "\\-fx"),
    ],
)
def test_override_tag_normalization(tag: str, normalized: str) -> None:
    assert str(OverrideTag(tag)) == normalized


def test_karaoke_syllables() -> None:
    d = AssDialogue.parse(
        "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,{\\fad(1,2)}{\\k20}a{\\k30\\fs 20}b{\\K10}c{note}d{\\kf5\\-x}e"
    )
    syls = parse_karaoke_syllables(d)
    assert [(s.tag_type, s.start_time, s.duration, s.text) for s in syls] == [
        ("\\k", 1000, 200, "a"),
        ("\\k", 1200, 300, "b"),
        ("\\kf", 1500, 100, "cd"),
        ("\\kf", 1600, 50, "e"),
    ]
    assert syls[0].get_text(False) == "{\\fad(1,2)}a"
    assert syls[1].get_text(False) == "{\\fs20}b"
    assert syls[2].get_text(False) == "c{note}d"
    assert syls[3].get_text(False) == "{\\-x}e"
    assert syls[1].get_text(True) == "{\\k30}{\\fs20}b"


OURS = " with aegisub-render 0.1.0"
STANDARD = ["; Script generated by Aegisub 3.5.1" + OURS, "; https://aegisub.org/"]


@pytest.mark.parametrize(
    ("comments", "expected"),
    [
        ((), STANDARD),
        (
            ("; Script generated by Aegisub 3.5.0", "; https://aegisub.org/"),
            ["; Script generated by Aegisub 3.5.0" + OURS, "; https://aegisub.org/"],
        ),
        # old builds: the version is a build name and the URL is the old http one, which stays
        (
            ("; Script generated by Aegisub 9706-cibuilds-20caaabc0", "; http://www.aegisub.org/"),
            ["; Script generated by Aegisub 9706-cibuilds-20caaabc0" + OURS, "; http://www.aegisub.org/"],
        ),
        (("; Script generated by Aegisub 3.2.2",), ["; Script generated by Aegisub 3.2.2" + OURS]),
        (
            ("; Script generated by Aegisub 3.5.1 with aegisub-render 0.0.9", "; https://aegisub.org/"),
            ["; Script generated by Aegisub 3.5.1" + OURS, "; https://aegisub.org/"],
        ),
        (("; Script generated by FFmpeg/Lavc61.19.100", "; http://www.aegisub.org/"), STANDARD),
        (("; a comment",), STANDARD),
    ],
)
def test_header_without_keep_brand(comments: tuple[str, ...], expected: list[str]) -> None:
    """An Aegisub header gets ' with aegisub-render' appended to its first line (once) and keeps the
    rest; anything else is replaced by the standard header."""
    ass = parse_ass_lines(["[Script Info]", *comments, "Title: x"])
    assert header_lines(ass) == expected


def test_header_with_keep_brand() -> None:
    for comments in (
        [],
        ["; Script generated by FFmpeg/Lavc61.19.100"],
        ["; Script generated by Aegisub 3.5.0", "; x"],
    ):
        ass = parse_ass_lines(["[Script Info]", *comments, "Title: x", "; later comments are dropped"])
        assert ass.header_comments == comments
        assert header_lines(ass, keep_brand=True) == comments
        out = serialize_ass(ass, keep_brand=True).decode("utf-8-sig").split("\r\n")
        assert out[: len(comments) + 2] == ["[Script Info]", *comments, "Title: x"]


def test_ass_file_len_iter() -> None:
    events = [AssDialogue(text=str(i)) for i in range(100)]
    ass = AssFile(events=events)
    assert len(ass) == 100
    for i, ev in enumerate(ass):
        assert ev == events[i]


def test_assfile_constructors_and_serializers() -> None:
    """AssFile.from_text / from_bytes / serialize / export do the same as the parser and writer
    functions they import at run time."""
    text = "\n".join(
        [
            "[Script Info]",
            "Title: x",
            "ScriptType: v4.00+",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
        ]
    )
    expected = parse_ass_lines(decode_subtitle_bytes(text.encode("utf-8")))

    from_text = AssFile.from_text(text)
    assert from_text.serialize() == serialize_ass(expected)
    assert from_text.export() == serialize_export(expected)
    assert from_text.serialize(save_ui_state=False) == serialize_ass(expected, save_ui_state=False)
    assert from_text.events[0].text == "hello"

    # CRLF, a BOM and trailing blanks change nothing (the lines are trimmed)
    crlf = "﻿" + text.replace("\n", "   \r\n")
    assert AssFile.from_text(crlf).serialize() == from_text.serialize()

    data = from_text.serialize()
    assert AssFile.from_bytes(data).serialize() == data
    utf16 = b"\xff\xfe" + data.decode("utf-8-sig").encode("utf-16-le")
    assert AssFile.from_bytes(utf16).serialize() == data
    with pytest.raises(UnsupportedEncodingError):
        AssFile.from_bytes(b"[Script Info]\r\nTitle: \xff\xfe\xff")

    # the version argument reaches the parser (read_ass_file passes 0 for a .ssa file)
    for version in (0, 1):
        assert AssFile.from_text(text, version).serialize() == serialize_ass(
            parse_ass_lines(split_subtitle_text(text), version)
        )
        assert AssFile.from_bytes(data, version).serialize() == serialize_ass(
            parse_ass_lines(decode_subtitle_bytes(data), version)
        )


def test_assfile_from_path(tmp_path: Path) -> None:
    ass = AssFile.from_text("[Script Info]\nTitle: x\n")
    p = tmp_path / "x.ass"
    p.write_bytes(ass.serialize())
    assert AssFile.from_path(p).get_script_info("Title") == "x"
    assert AssFile.from_path(p).serialize() == ass.serialize()


def test_top_level_exports() -> None:
    """The model classes are reachable from the package root (aegisub_render.AssFile)."""
    import aegisub_render
    import aegisub_render.ass

    names = [
        "AssAttachment",
        "AssDialogue",
        "AssFile",
        "AssInfo",
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
    ]
    for name in names:
        assert name in aegisub_render.__all__, name
        assert getattr(aegisub_render, name) is getattr(aegisub_render.ass, name), name
    assert aegisub_render.AssFile is AssFile


class _MyInfo(AssInfo):
    __slots__ = ()


class _MyStyle(AssStyle):
    __slots__ = ()


class _MyDialogue(AssDialogue):
    __slots__ = ()


class _MyAttachment(AssAttachment):
    __slots__ = ()


@dataclass(slots=True)
class _MyExtradata(ExtradataEntry):
    pass


class _MyAssFile(AssFile):
    __slots__ = ()


class _MyParser(AssParser):
    """Parses into the subclasses above, without touching any of the parsing."""

    @classmethod
    @override
    def make_file(cls) -> _MyAssFile:
        return _MyAssFile()

    @override
    def make_info(self, key: str, value: str) -> _MyInfo:
        return _MyInfo(key, value)

    @override
    def make_style(self, data: str, version: int) -> _MyStyle:
        return _MyStyle.parse(data, version)

    @override
    def make_dialogue(self, data: str) -> _MyDialogue:
        return _MyDialogue.parse(data)

    @override
    def make_attachment(self, data: str, group: Group) -> _MyAttachment:
        return _MyAttachment(data, group)

    @override
    def make_extradata(self, ident: int, key: str, value: str) -> _MyExtradata:
        return _MyExtradata(ident, key, value)


_SUBCLASS_TEXT = "\n".join(
    [
        "[Script Info]",
        "Title: x",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold,"
        " Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1",
        "",
        "[Fonts]",
        "fontname: a_0.ttf",
        "!!!!!%),2E#",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
        "",
        "[Aegisub Extradata]",
        "Data: 1,key,evalue",
    ]
)


def test_parser_subclass_builds_its_own_classes() -> None:
    """A parser subclass decides every class the result is made of, via the make_* hooks."""
    ass = parse_ass_text(_SUBCLASS_TEXT, parser_cls=_MyParser)
    assert type(ass) is _MyAssFile
    assert [type(e) for e in ass.info] == [_MyInfo]
    assert [type(e) for e in ass.styles] == [_MyStyle]
    assert [type(e) for e in ass.events] == [_MyDialogue]
    assert [type(e) for e in ass.attachments] == [_MyAttachment]
    assert [type(e) for e in ass.extradata] == [_MyExtradata]
    assert ass.styles[0].name == "Default" and ass.events[0].text == "hello"
    assert ass.attachments[0].filename == "a_0.ttf" and ass.extradata[0].value == "value"

    # the parsing itself is unchanged: same bytes out as the plain parser (serialize() runs
    # clean_extradata, which drops the entry no event refers to, so it comes after the checks)
    plain = parse_ass_text(_SUBCLASS_TEXT)
    assert ass.serialize() == plain.serialize()

    # the same class reaches every entry point
    assert type(AssFile.from_text(_SUBCLASS_TEXT, parser_cls=_MyParser)) is _MyAssFile
    data = plain.serialize()
    assert type(AssFile.from_bytes(data, parser_cls=_MyParser)) is _MyAssFile
    assert type(parse_ass_bytes(data, parser_cls=_MyParser)) is _MyAssFile
    assert type(parse_ass_lines(split_subtitle_text(_SUBCLASS_TEXT), parser_cls=_MyParser)) is _MyAssFile
    # ... and without it the plain classes are used
    assert type(AssFile.from_text(_SUBCLASS_TEXT)) is AssFile


def test_parser_subclass_from_path(tmp_path: Path) -> None:
    p = tmp_path / "x.ass"
    p.write_bytes(parse_ass_text(_SUBCLASS_TEXT).serialize())
    assert type(AssFile.from_path(p, parser_cls=_MyParser)) is _MyAssFile
    assert type(read_ass_file(p, _MyParser).events[0]) is _MyDialogue


def test_entry_parse_uses_cls() -> None:
    """AssStyle.parse / AssDialogue.parse are classmethods, so a subclass parses into itself."""
    line = "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello"
    assert type(_MyDialogue.parse(line)) is _MyDialogue
    assert type(AssDialogue.parse(line)) is AssDialogue
    assert _MyDialogue.parse(line).entry_data() == AssDialogue.parse(line).entry_data()
    assert type(AssInfo("a", "b").copy()) is AssInfo
    assert type(_MyInfo("a", "b").copy()) is _MyInfo


_STRIPPED_LINE = (
    r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\an8\t(0,248,\clip(1,2,3,4)}hi\Nthere"
    r"{a comment}{\p1}m 0 0 l 5 5{\p0}end"
)


def test_get_stripped_text() -> None:
    """AssDialogue::GetStrippedText: only the plain blocks, so no tags, no comment blocks and no
    drawings; \\N and friends stay as the two characters they are in the text."""
    d = AssDialogue.parse(_STRIPPED_LINE)
    assert d.get_stripped_text() == r"hi\Nthereend"
    before = d.entry_data()
    d.strip_tags()
    assert d.text == r"hi\Nthereend"
    assert d.entry_data() != before
    assert d.strip_tags() is None and d.text == r"hi\Nthereend"  # idempotent

    # an empty line and a line that is only tags
    assert AssDialogue.parse("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,").get_stripped_text() == ""
    assert AssDialogue.parse(r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\b1}").get_stripped_text() == ""


def test_copy_gives_new_ids() -> None:
    """AssDialogue's copy constructor hands out a new Id; AssFile's clones its events."""
    ass = AssFile.from_text("\n".join(["[Events]", "Format: Layer, Start, End, Text", _STRIPPED_LINE]))
    line = ass.events[0]
    clone = line.copy()
    assert clone is not line and clone.id != line.id
    assert clone.text == line.text and clone.margin == line.margin and clone.margin is not line.margin

    other = ass.copy()
    assert other.serialize() == ass.serialize()
    assert [e.id for e in other.events] != [e.id for e in ass.events]
    other.events[0].text = "changed"
    other.events[0].margin[0] = 99
    other.info.append(AssInfo("Title", "y"))
    assert ass.events[0].text != "changed" and ass.events[0].margin[0] == 0
    assert [i.key for i in ass.info] == []
    # unlike Aegisub's copy constructor, the properties and the header comments come along
    ass.properties.video_file = "v.mkv"
    assert ass.copy().properties.video_file == "v.mkv"

    style = AssStyle()
    style.name = "s"
    assert style.copy() is not style and style.copy().name == "s"


def test_ssa_to_ass_alignment() -> None:
    """AssStyle::Parse's SSA table, which the legacy \\a tag uses too."""
    assert [ssa_to_ass_alignment(n) for n in (1, 2, 3, 5, 6, 7, 9, 10, 11)] == [1, 2, 3, 7, 8, 9, 4, 5, 6]
    assert [ssa_to_ass_alignment(n) for n in (0, 4, 8, 12, -1)] == [2, 2, 2, 2, 2]
    # what a v4 (SSA) style line parses to, which is where the table comes from: 18 fields,
    # alignment 5 (top left in SSA) is 7 in an \an
    v4 = "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00FF0000,&H00000000,-1,0,1,2,2,5,10,10,10,0,1"
    assert AssStyle.parse(v4, version=0).alignment == ssa_to_ass_alignment(5) == 7


def test_parse_override_tags_nested_transform() -> None:
    r"""ParseTags' depth only rises on a `(` seen at depth 0, so a \t with tags in its parameters
    is split in the middle and str(tag) does not join back into the input text.

    Faithful to AssDialogueBlockOverride::ParseTags + AssOverrideTag::operator std::string();
    pinned here because it looks like a bug and must not be "fixed" into real bracket counting.
    Aegisub writes Text back verbatim unless a line was edited, which is why files survive it.
    """
    text = r"\t(0,248,\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080)))"
    tags = parse_override_tags(text)
    assert [t.name for t in tags] == [r"\t", r"\t"]
    assert str(tags[0]) == r"\t(0,248,\clip(690,0,1230,1080))"
    assert str(tags[1]) == r"\t(586,922,\clip(1230,0,1230,1080),))"
    assert "".join(str(t) for t in tags) != text
    # the parameters are shifted too: an empty accel in the first half, a stray ')' in the second
    assert [p.value for p in tags[0].params] == ["0", "248", "", r"\clip(690,0,1230,1080)"]
    assert [p.value for p in tags[1].params] == ["586", "922", r"\clip(1230,0,1230,1080)", ")"]

    # the block itself keeps the text exactly, so saving an unedited line is byte-identical
    blocks = parse_dialogue_blocks("{" + text + "}hi")
    assert [b.text for b in blocks] == [text, "hi"]
    assert [b.type for b in blocks] == [BlockType.OVERRIDE, BlockType.PLAIN]

    # one level of parentheses is fine, which is every tag Aegisub's own UI writes
    simple = r"\t(0,248,\fscx120)\fscy120"
    assert "".join(str(t) for t in parse_override_tags(simple)) == simple


_NESTED_T = r"\t(0,248,\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080)))"


def test_strict_tag_parsing_counts_brackets() -> None:
    r"""strict=True keeps a \t with tags in its parameters in one piece, and str(tag) then joins
    back into the input text -- what a caller rebuilding a line of its own wants."""
    tags = parse_override_tags(_NESTED_T, strict=True)
    assert [t.name for t in tags] == [r"\t"]
    assert str(tags[0]) == _NESTED_T
    assert "".join(str(t) for t in tags) == _NESTED_T
    # the nested animation is the last parameter instead of being cut in half
    assert [p.value for p in tags[0].params] == [
        "0",
        "248",
        "",
        r"\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080))",
    ]

    # a block parses with it too, and the default (Aegisub) still gives two tags
    assert len(parse_dialogue_blocks("{" + _NESTED_T + "}hi", strict=True)[0].tags) == 1
    assert len(parse_dialogue_blocks("{" + _NESTED_T + "}hi")[0].tags) == 2

    # one level of parentheses parses the same either way
    simple = r"\an8\pos(10,20)\t(0,248,\fscx120)\clip(m 0 0 l 5 5)"
    assert [str(t) for t in parse_override_tags(simple, strict=True)] == [
        str(t) for t in parse_override_tags(simple, strict=False)
    ]

    # strict fixes the splitting, not the normalisation: a missing ')' in the input is still
    # closed by AssOverrideTag::operator std::string()
    unbalanced = r"\t(0,248,\clip(690,0,1230,1080)"
    assert str(parse_override_tags(unbalanced, strict=True)[0]) == unbalanced + ")"


def test_set_strict_tag_parsing_switches_the_default() -> None:
    """The process-wide switch changes what callers that pass no strict= get, and an explicit
    strict= still wins."""
    assert strict_tag_parsing() is False  # Aegisub's behaviour is the default
    previous = set_strict_tag_parsing(True)
    try:
        assert previous is False
        assert strict_tag_parsing() is True
        assert len(parse_override_tags(_NESTED_T)) == 1
        assert len(parse_dialogue_blocks("{" + _NESTED_T + "}hi")[0].tags) == 1
        assert len(parse_override_tags(_NESTED_T, strict=False)) == 2
        # it reaches the model classes as well
        line = AssDialogue.parse(r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{" + _NESTED_T + r"}hi\Nthere")
        assert line.get_stripped_text() == r"hi\Nthere"
    finally:
        assert set_strict_tag_parsing(previous) is True
    assert strict_tag_parsing() is False
    assert len(parse_override_tags(_NESTED_T)) == 2
