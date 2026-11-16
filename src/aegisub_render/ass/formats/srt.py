"""SubRip (.srt), ported from src/subtitle_format_srt.cpp.

The reader is Aegisub's state machine (the algorithm at
<http://devel.aegisub.org/wiki/SubtitleFormats/SRT>) and its SrtTagParser, which turns the HTML
subset SubRip files use into override tags. The writer is SRTSubtitleFormat::WriteFile, with one
deliberate difference: `tags` decides what happens to the `{...}` tags, and the default here is
TagMode.KEEP rather than the translation of the four tags Aegisub knows (see base.TagMode).
"""

import re
from typing import ClassVar, override

from aegisub_render.ass.color import Color
from aegisub_render.ass.entries import AssDialogue, SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode, TextWriter, split_lines
from aegisub_render.ass.override import BlockType, parse_dialogue_blocks
from aegisub_render.ass.time import parse_time, srt_formatted
from aegisub_render.cutil import trim

_DIGITS = "0123456789"
_SPACE = " \t\n\v\f\r"
_TOGGLE_TAGS = "bisu"

# "hh:mm:ss,fff --> hh:mm:ss,fff" (e.g. "00:00:04,070 --> 00:00:10,04")
TIMESTAMP_RE = re.compile(
    r"^([0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2},[0-9]{1,}) --> ([0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2},[0-9]{1,})"
)

_TAG_RE = re.compile(r"<(/?b|/?i|/?u|/?s|/?font)([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(rf"[{_SPACE}]+(face|size|color)=('[^']*'|\"[^\"]*\"|[^{_SPACE}]+)", re.IGNORECASE)


class SRTParseError(SubtitleFormatParseError):
    pass


def _all_digits(text: str) -> bool:
    """boost::algorithm::all(s, boost::is_digit()): the classic C locale, so ASCII only."""
    return all(c in _DIGITS for c in text)


class ToggleTag:
    """subtitle_format_srt.cpp's ToggleTag: nested `<b>` only opens the override tag once."""

    __slots__ = ("level", "tag")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.level = 0

    def open(self, out: list[str]) -> None:
        if self.level == 0:
            out.append("{\\" + self.tag + "1}")
        self.level += 1

    def close(self, out: list[str]) -> None:
        if self.level == 1:
            out.append("{\\" + self.tag + "}")
        if self.level > 0:
            self.level -= 1


class SrtTagParser:
    """SrtTagParser: the HTML subset of SubRip as ASS override tags.

    `<b>`, `<i>`, `<u>` and `<s>` become `{\\b1}` / `{\\b}` and friends; `<font face=.. size=..
    color=..>` becomes `{\\fn..}{\\fs..}{\\c..}`, with a stack so that `</font>` restores the
    enclosing values (and `{\\fn}` etc. when there is nothing to restore). Anything else is
    copied through unchanged, with its tag name lower-cased as Aegisub leaves it.

    Shared with the Matroska reader, which runs it over S_TEXT/UTF8 tracks just as Aegisub does.
    """

    def to_ass(self, srt: str) -> str:
        toggles = {t: ToggleTag(t) for t in _TOGGLE_TAGS}
        # each entry is (face, size, color): the {\fn..}/{\fs..}/{\c..} currently in force
        font_stack: list[tuple[str, str, str]] = []

        out: list[str] = []
        cursor = 0
        while cursor < len(srt):
            m = _TAG_RE.search(srt, cursor)
            if m is None:
                out.append(srt[cursor:])  # no more tags could be matched, end of string
                break

            tag_name = m.group(1).lower()
            tag_attrs = m.group(2)
            out.append(srt[cursor : m.start()])  # the text before the tag goes through unchanged
            cursor = m.end()

            if len(tag_name) == 1 and tag_name in _TOGGLE_TAGS:
                toggles[tag_name].open(out)
            elif len(tag_name) == 2 and tag_name[0] == "/" and tag_name[1] in _TOGGLE_TAGS:
                toggles[tag_name[1]].close(out)
            elif tag_name == "font":
                self._open_font(out, tag_attrs, font_stack)
            elif tag_name == "/font":
                self._close_font(out, font_stack)
            else:
                out.append("<" + tag_name + tag_attrs + ">")  # unknown tag, replicate it

        # make it a little prettier, join tag groups
        return "".join(out).replace("}{", "")

    @staticmethod
    def _open_font(out: list[str], tag_attrs: str, font_stack: list[tuple[str, str, str]]) -> None:
        old = font_stack[-1] if font_stack else ("", "", "")
        new = list(old)
        pos = 0
        while (am := _ATTR_RE.match(tag_attrs, pos)) is not None:  # boost::match_continuous
            attr_name = am.group(1).lower()
            attr_value = am.group(2)
            if len(attr_value) >= 2 and attr_value[0] in "'\"" and attr_value[-1] == attr_value[0]:
                attr_value = attr_value[1:-1]
            if attr_name == "face":
                new[0] = "{\\fn%s}" % attr_value
            elif attr_name == "size":
                new[1] = "{\\fs%s}" % attr_value
            elif attr_name == "color":
                new[2] = "{\\c%s}" % Color.parse(attr_value).ass_override()
            pos = am.end()
        # the attributes that changed from the old ones are the ones written out
        for i in range(3):
            if new[i] != old[i]:
                out.append(new[i])
        font_stack.append((new[0], new[1], new[2]))

    @staticmethod
    def _close_font(out: list[str], font_stack: list[tuple[str, str, str]]) -> None:
        if not font_stack:  # this requires a font stack entry
            return
        cur_attribs = font_stack.pop()
        old = font_stack[-1] if font_stack else ("", "", "")
        for i, empty in enumerate(("{\\fn}", "{\\fs}", "{\\c}")):
            if cur_attribs[i] != old[i]:
                out.append(old[i] or empty)


class _ParseState:
    INITIAL = 0
    TIMESTAMP = 1
    FIRST_LINE_OF_BODY = 2
    REST_OF_BODY = 3
    LAST_WAS_BLANK = 4


class _TagState:
    __slots__ = ("tag", "value")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.value = False


class SRTSubtitleFormat(SubtitleFormat):
    """SRTSubtitleFormat."""

    name: ClassVar[str] = "SubRip"
    read_wildcards: ClassVar[tuple[str, ...]] = ("srt",)
    write_wildcards: ClassVar[tuple[str, ...]] = ("srt",)
    default_tags: ClassVar[TagMode] = TagMode.KEEP

    @override
    def read(self, raw: bytes) -> AssFile:
        """SRTSubtitleFormat::ReadFile: a default ASS file whose events come from the .srt."""
        target, parser = self.new_file()
        self.load_default(target, parser)

        tag_parser = SrtTagParser()
        state = _ParseState.INITIAL
        line_num = 0
        linebreak_debt = 0
        line: AssDialogue | None = None
        text = ""

        for raw_line in split_lines(raw):
            text_line = trim(raw_line)
            line_num += 1

            if state == _ParseState.INITIAL:
                if not text_line:
                    continue  # ignore leading blank lines
                if _all_digits(text_line):
                    state = _ParseState.TIMESTAMP  # the subtitle index; throw it away
                    continue
                m = TIMESTAMP_RE.search(text_line)
                if m is None:
                    raise SRTParseError(f"Parsing SRT: Expected subtitle index at line {line_num}")
                stamps = (m.group(1), m.group(2))
            elif state == _ParseState.TIMESTAMP:
                m = TIMESTAMP_RE.search(text_line)
                if m is None:
                    raise SRTParseError(f"Parsing SRT: Expected timestamp pair at line {line_num}")
                stamps = (m.group(1), m.group(2))
            elif state == _ParseState.FIRST_LINE_OF_BODY:
                if not text_line:
                    # that's not very interesting... blank subtitle?
                    state = _ParseState.LAST_WAS_BLANK
                    linebreak_debt = 0  # no previous line that needs a line break after
                    continue
                text += text_line
                state = _ParseState.REST_OF_BODY
                continue
            elif state == _ParseState.REST_OF_BODY:
                if not text_line:
                    # Might be the gap between two subtitles or just a blank line in the middle
                    # of one, so defer the line break until we see what is on the next line
                    state = _ParseState.LAST_WAS_BLANK
                    linebreak_debt = 1
                    continue
                text += "\\N" + text_line
                continue
            else:  # LAST_WAS_BLANK
                linebreak_debt += 1
                if not text_line:
                    continue
                if _all_digits(text_line):
                    # Hopefully the start of a new subtitle, the blank line(s) being the gap
                    state = _ParseState.TIMESTAMP
                    continue
                m = TIMESTAMP_RE.search(text_line)
                if m is None:
                    # assume it's a continuation: resolve the line break debt and append
                    text += "\\N" * linebreak_debt
                    linebreak_debt = 0
                    text += text_line
                    state = _ParseState.REST_OF_BODY
                    continue
                stamps = (m.group(1), m.group(2))

            # Every branch above either continued, raised, or found a timestamp pair: what is
            # left is `if (found_timestamps)`, which by here is always true.
            if line is not None:
                line.text = tag_parser.to_ass(text)  # finalize the active line
                text = ""
            line = self.new_dialogue(parser)
            line.start = parse_time(stamps[0])
            line.end = parse_time(stamps[1])
            target.events.append(line)
            state = _ParseState.FIRST_LINE_OF_BODY

        if state in (_ParseState.TIMESTAMP, _ParseState.FIRST_LINE_OF_BODY):
            raise SRTParseError("Parsing SRT: Incomplete file")

        if line is not None:  # an unfinalized line
            line.text = tag_parser.to_ass(text)
        return target

    @override
    def write(self, file: AssFile) -> bytes:
        """SRTSubtitleFormat::WriteFile. `tags` is this port's addition (see base.TagMode)."""
        copy = self.prepare(file, newline="\r\n", merge_line_breaks=False)
        writer = TextWriter()
        for i, current in enumerate(copy.events, start=1):
            writer.line(str(i))
            writer.line(f"{srt_formatted(current.start)} --> {srt_formatted(current.end)}")
            writer.line(self.convert_tags(current) if self.tags is TagMode.HTML else current.text)
            writer.line("")
        return writer.data()

    @override
    def can_save(self, file: AssFile) -> bool:
        """SRTSubtitleFormat::CanSave: no attachments, no non-default style, only `\\b\\i\\s\\u`.

        Only meaningful for `tags=TagMode.HTML`, which is what Aegisub always does; with the
        default TagMode.KEEP nothing but the style table is lost.
        """
        if file.attachments:
            return False
        for line in file.events:
            if line.style != "Default":
                return False
            for block in parse_dialogue_blocks(line.text):
                if block.type is not BlockType.OVERRIDE:
                    continue
                for tag in block.tags:
                    if len(tag.name) != 2 or tag.name[1] not in _TOGGLE_TAGS:
                        return False
        return True

    @staticmethod
    def convert_tags(diag: AssDialogue) -> str:
        """SRTSubtitleFormat::ConvertTags: `\\b`/`\\i`/`\\s`/`\\u` as `<b>` and friends.

        Every other override tag, and every drawing, is dropped -- this is what Aegisub writes.
        Tags left open at the end of the line are closed (Aegisub bug #809).
        """
        states = [_TagState(t) for t in "bisu"]
        final: list[str] = []
        for block in parse_dialogue_blocks(diag.text):
            if block.type is BlockType.OVERRIDE:
                for tag in block.tags:
                    if not tag.valid or len(tag.name) != 2:
                        continue
                    for state in states:
                        if state.tag != tag.name[1]:
                            continue
                        temp = bool(tag.params) and tag.params[0].get_int(0) != 0
                        if temp and not state.value:
                            final.append(f"<{state.tag}>")
                        if not temp and state.value:
                            final.append(f"</{state.tag}>")
                        state.value = temp
            elif block.type is BlockType.PLAIN:
                final.append(block.text)
        for state in states:
            if state.value:
                final.append(f"</{state.tag}>")
        return "".join(final)
