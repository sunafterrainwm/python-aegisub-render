"""WebVTT (.vtt): reading and writing.

Not an Aegisub format -- there is no upstream to be faithful to here, so this is a port of the
W3C WebVTT specification as far as ASS can express it, built out of the same SubtitleFormat
pieces as the ported formats.

Reading turns a cue into one event:

* the cue payload's span tags become override tags (`<b>`, `<i>`, `<u>`, `<c.class>`, `<lang>`,
  `<ruby>`), with the CSS of the file's STYLE blocks behind the classes -- see `vtt_style`;
* `<v Name>` goes into the Name/Actor field, and if a `::cue(v[voice="Name"])` rule exists, a
  style of that name is added to the file and the event uses it;
* the cue's `align` and `line` settings become `\\an`, and `line`/`position`/`size` become
  MarginL/MarginR/MarginV -- which need a canvas, so a file whose cues are positioned gets
  PlayResX/PlayResY (`play_res`, 1280x720 by default); one whose cues are not stays resolution
  free, like Aegisub's own LoadDefault;
* `<00:00:01.000>` timestamp tags become `\\k` karaoke timings;
* a line break in the payload becomes `\\N`, `&nbsp;` becomes `\\h`, and the other character
  references are decoded.

Deliberately dropped, because ASS has nowhere to put them: cue identifiers, REGION and NOTE
blocks, `vertical:` (ASS cannot rotate a line's layout), `::cue(#id)` and element selectors, and
the annotation of a `<rt>` ruby span (the base text is kept).

Writing is the mirror image, except that no STYLE block is written -- a .vtt from here carries no
style sheet, and by default keeps its `{...}` override tags for the players that understand them
(see base.TagMode).
"""

import re
from typing import ClassVar, override

from aegisub_render.ass.entries import SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode, TextWriter, set_script_info, split_lines
from aegisub_render.ass.formats.srt import SRTSubtitleFormat
from aegisub_render.ass.formats.vtt_style import BOLD, ITALIC, UNDERLINE, CueRules, CueStyle, TagStack
from aegisub_render.ass.parser import AssParser
from aegisub_render.ass.time import parse_time, vtt_formatted
from aegisub_render.cutil import mid

SIGNATURE = "WEBVTT"

_TIME = r"(?:[0-9]{2,}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"
_TIMING_RE = re.compile(rf"^({_TIME})[ \t]+-->[ \t]+({_TIME})[ \t]*(.*)$")
_TIMESTAMP_TAG_RE = re.compile(rf"^{_TIME}$")
_SETTING_RE = re.compile(r"([A-Za-z]+):([^ \t]*)")
_PERCENT_RE = re.compile(r"^([+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+))%$")
_INT_RE = re.compile(r"^[+-]?[0-9]+$")

_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": "\\h",  # ASS's own hard space
    "lrm": "\u200e",
    "rlm": "\u200f",
}
_ENTITY_RE = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);")

# Horizontal part of \an for each WebVTT text alignment, and the row offset for each line region
_ALIGN_COLUMN = {"start": 1, "left": 1, "center": 2, "middle": 2, "end": 3, "right": 3}
_ANCHOR = {1: 0.0, 2: 0.5, 3: 1.0}  # where `position` sits inside the cue box, per alignment
_DEFAULT_ALIGNMENT = 2  # AssStyle's alignment, so an n tag for it would say nothing


class VTTParseError(SubtitleFormatParseError):
    pass


def _unescape(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name.startswith("#"):
            try:
                code = int(name[2:], 16) if name[1] in "xX" else int(name[1:])
            except ValueError:
                return m.group(0)
            return chr(code) if 0 < code < 0x110000 else m.group(0)
        return _ENTITIES.get(name.lower(), m.group(0))

    return _ENTITY_RE.sub(repl, text)


def _escape(text: str, ampersand: bool) -> str:
    """Escape a cue payload. `>` too, which also takes care of a stray `-->` in the text.

    `ampersand` is off while the override tags are kept: `{\\c&H00FF00&}` has to reach the player
    as it is, and a bare `&` that does not start a character reference is read as one by every
    WebVTT parser there is (the spec's own error recovery).
    """
    if ampersand:
        text = text.replace("&", "&amp;")
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _percent(value: str) -> float | None:
    m = _PERCENT_RE.match(value)
    return float(m.group(1)) if m is not None else None


class _Cue:
    __slots__ = ("lines", "settings", "start", "stop")

    def __init__(self, start: int, stop: int, settings: str, lines: list[str]) -> None:
        self.start = start
        self.stop = stop
        self.settings: dict[str, str] = {k.lower(): v for k, v in _SETTING_RE.findall(settings)}
        self.lines = lines


class VTTSubtitleFormat(SubtitleFormat):
    """WebVTT. `play_res` is the canvas positioned cues are mapped onto when reading."""

    name: ClassVar[str] = "WebVTT"
    read_wildcards: ClassVar[tuple[str, ...]] = ("vtt",)
    write_wildcards: ClassVar[tuple[str, ...]] = ("vtt",)
    default_tags: ClassVar[TagMode] = TagMode.KEEP

    def __init__(
        self,
        tags: TagMode | str | None = None,
        parser_cls: type[AssParser] | None = None,
        play_res: tuple[int, int] = (1280, 720),
    ) -> None:
        super().__init__(tags, parser_cls)
        self.play_res = play_res

    # -- reading ------------------------------------------------------------------------------

    @override
    def read(self, raw: bytes) -> AssFile:
        lines = split_lines(raw)
        if not lines or not self._valid_signature(lines[0]):
            raise VTTParseError("Parsing WebVTT: the file does not start with the WEBVTT signature")

        rules = CueRules()
        cues: list[_Cue] = []
        for block in self._blocks(lines[1:]):
            head = block[0]
            keyword = head.split(" ")[0].split("\t")[0]
            if keyword == "STYLE":
                rules.add_css("\n".join(block[1:]))
            elif keyword in ("NOTE", "REGION"):
                continue  # nothing in an ASS file holds these
            else:
                cue = self._parse_cue(block)
                if cue is not None:
                    cues.append(cue)

        target, parser = self.new_file()
        self.load_default(target, parser)
        if rules.cue:
            rules.cue.apply_to(target.styles[0])
        for voice, style in rules.voices.items():
            entry = parser.make_style(target.styles[0].entry_data(), 1)
            entry.name = voice.replace(",", ";")
            style.apply_to(entry)
            target.styles.append(entry)

        positioned = False
        for cue in cues:
            line = self.new_dialogue(parser)
            line.start = cue.start
            line.end = cue.stop
            text, actor = self.cue_text_to_ass(cue.lines, rules, cue.start, cue.stop)
            alignment, margin, has_margin = self.cue_position(cue.settings)
            if alignment != _DEFAULT_ALIGNMENT:
                text = ("{\\an%d}" % alignment) + text
            line.text = text.replace("}{", "")
            line.actor = actor
            if actor and rules.voices.get(actor) is not None:
                line.style = actor
            if has_margin:
                line.margin = margin
                positioned = True
            target.events.append(line)

        if positioned:
            # margins are in script units, so the file has to say what the script is
            index = next(
                (i + 1 for i, info in enumerate(target.info) if info.key.lower() == "scripttype"),
                len(target.info),
            )
            set_script_info(target, parser, "PlayResX", str(self.play_res[0]), index)
            set_script_info(target, parser, "PlayResY", str(self.play_res[1]), index + 1)
        return target

    @staticmethod
    def _valid_signature(first: str) -> bool:
        if not first.startswith(SIGNATURE):
            return False
        rest = first[len(SIGNATURE) :]
        return rest == "" or rest[0] in " \t"

    @staticmethod
    def _blocks(lines: list[str]) -> list[list[str]]:
        """The file's blocks: runs of lines separated by one or more empty lines."""
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if line == "":
                if current:
                    blocks.append(current)
                    current = []
            else:
                current.append(line)
        if current:
            blocks.append(current)
        return blocks

    @staticmethod
    def _parse_cue(block: list[str]) -> _Cue | None:
        """A cue block: an optional identifier line, the timings, then the payload."""
        index = 0 if "-->" in block[0] else 1
        if index >= len(block):
            return None  # an identifier with no timings: not a cue
        m = _TIMING_RE.match(block[index])
        if m is None:
            return None  # not a cue block and not a keyword we know; skip it
        return _Cue(parse_time(m.group(1)), parse_time(m.group(2)), m.group(3), block[index + 1 :])

    def cue_position(self, settings: dict[str, str]) -> tuple[int, list[int], bool]:
        """A cue's settings as (`\\an` value, [MarginL, MarginR, MarginV], any margin set)."""
        align = settings.get("align", "center").lower()
        column = _ALIGN_COLUMN.get(align, 2)

        line_setting = settings.get("line", "")
        line_pct = _percent(line_setting.split(",")[0]) if line_setting else None
        row = 0  # bottom
        if line_pct is not None:
            row = 2 if line_pct <= 100 / 3 else (0 if line_pct >= 200 / 3 else 1)
        elif line_setting and _INT_RE.match(line_setting.split(",")[0]):
            row = 0 if int(line_setting.split(",")[0]) < 0 else 2
        alignment = column + row * 3

        width, height = self.play_res
        size = _percent(settings.get("size", "")) if "size" in settings else None
        pos = _percent(settings.get("position", "").split(",")[0]) if "position" in settings else None
        margin = [0, 0, 0]
        if size is not None or pos is not None:
            box = 100.0 if size is None else mid(0.0, size, 100.0)
            anchor = _ANCHOR[column]
            left = (anchor * 100.0 if pos is None else pos) - box * anchor
            margin[0] = max(0, round(left / 100.0 * width))
            margin[1] = max(0, round((100.0 - left - box) / 100.0 * width))
        if line_pct is not None and row != 1:
            margin[2] = max(0, round((line_pct if row == 2 else 100.0 - line_pct) / 100.0 * height))
        return alignment, margin, any(margin)

    def cue_text_to_ass(self, lines: list[str], rules: CueRules, start: int, stop: int) -> tuple[str, str]:
        """A cue payload as ASS text, plus the voice name (`<v Name>`) if the cue names one."""
        text = "\\N".join(lines)
        stack = TagStack()
        pieces: list[list[str]] = [[]]
        times = [start]
        actor = ""
        skip_rt = False

        pos = 0
        while pos < len(text):
            if text[pos] != "<":
                end = text.find("<", pos)
                if end < 0:
                    end = len(text)
                if not skip_rt:
                    pieces[-1].append(_unescape(text[pos:end]))
                pos = end
                continue
            end = text.find(">", pos)
            if end < 0:
                if not skip_rt:
                    pieces[-1].append(_unescape(text[pos:]))
                break
            inside = text[pos + 1 : end]
            pos = end + 1
            out = pieces[-1]

            if _TIMESTAMP_TAG_RE.match(inside):
                times.append(parse_time(inside))
                pieces.append([])
                continue
            if inside.startswith("/"):
                name = inside[1:].split(".")[0].strip().lower()
                if name == "rt":
                    skip_rt = False
                else:
                    stack.pop(out)
                continue
            head, _, annotation = re.split(r"([ \t])", inside, maxsplit=1)[0], " ", ""
            parts = re.split(r"[ \t]", inside, maxsplit=1)
            head = parts[0]
            annotation = parts[1] if len(parts) > 1 else ""
            names = head.split(".")
            name = names[0].strip().lower()
            if name == "rt":
                skip_rt = True
                continue
            style = CueStyle()
            for cls in names[1:]:
                if cls:
                    style = style.merge(rules.class_style(cls))
            if name == "b":
                style = BOLD.merge(style)
            elif name == "i":
                style = ITALIC.merge(style)
            elif name == "u":
                style = UNDERLINE.merge(style)
            elif name == "v":
                voice = _unescape(annotation).strip()
                if voice and not actor:
                    actor = voice  # the event's Name field, and its style if the CSS names it
                elif voice and voice != actor:
                    # a second speaker in the same cue has nowhere to go but inline tags
                    style = style.merge(rules.voices.get(voice) or CueStyle())
            stack.push(style, out)

        if len(pieces) > 1:
            times.append(stop)
            for i, piece in enumerate(pieces):
                if not piece and times[i + 1] == times[i]:
                    continue  # a timestamp tag at the very start of the cue times nothing
                piece.insert(0, "{\\k%d}" % max(0, (times[i + 1] - times[i]) // 10))
        return "".join("".join(piece) for piece in pieces), actor

    # -- writing ------------------------------------------------------------------------------

    @override
    def write(self, file: AssFile) -> bytes:
        """The cues of `file`, with no style sheet (see the module docstring)."""
        copy = self.prepare(file, newline="\r\n", merge_line_breaks=True)
        writer = TextWriter()
        writer.line(SIGNATURE)
        writer.line("")
        for current in copy.events:
            writer.line(f"{vtt_formatted(current.start)} --> {vtt_formatted(current.end)}")
            if self.tags is TagMode.HTML:
                # the text is already HTML; only `&` can still need escaping
                writer.line(SRTSubtitleFormat.convert_tags(current).replace("&", "&amp;"))
            else:
                writer.line(_escape(current.text, ampersand=self.tags is TagMode.STRIP))
            writer.line("")
        return writer.data()
