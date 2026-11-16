"""SubtitleFormat: the base class every format in this package derives from.

A port of src/subtitle_format.cpp's shared half. `SubtitleFormat` is what Aegisub's class of the
same name is -- a name, the extensions it reads and writes, `read`/`write`/`export`, `can_save`,
and the conversion helpers (Sort, StripComments, RecombineOverlaps, MergeIdentical,
ConvertNewlines, StripTags) a subclass runs over a copy of the file before writing it. Subclass
it to add a format, or to change one: every piece a format needs is a method, so

    class MySrt(srt.SRTSubtitleFormat):
        @override
        def write(self, file: AssFile) -> bytes:
            ...

    register_format(MySrt, first=True)   # now MySrt handles .srt for read_subtitles / write_*

is all it takes. Aegisub's own LoadFormats order is what `formats/__init__.py` registers, and
`get_reader` / `get_writer` pick the first registered format that accepts the file name.

What is deliberately not Aegisub's: `TagMode`. Aegisub's exporters either translate the few
override tags a format knows (SRT) or throw them all away; here the default for the formats a
media player reads (.srt, .vtt, .ttxt) is to leave `{...}` alone, because most players parse ASS
override tags. The plain-text formats (.txt, TranStation) still strip them: they are meant to be
read by people. Pass `tags=` to a format's constructor to choose.
"""

from abc import ABC
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from aegisub_render.ass.entries import AssDialogue, AssStyle, SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.parser import AssParser, UnsupportedEncodingError
from aegisub_render.textenc import from_bytes, to_bytes

BOM = b"\xef\xbb\xbf"
LINEBREAK = "\r\n"  # TextFileWriter::newline on Windows, which is what this tool reproduces

# A blank AssDialogue as Aegisub's `new AssDialogue` is; parsed rather than constructed so that
# an AssParser subclass's make_dialogue hook decides the class (see SubtitleFormat.new_dialogue).
_BLANK_DIALOGUE = "Dialogue: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,,"


class UnknownSubtitleFormatError(SubtitleFormatParseError):
    """No registered format reads or writes that file name (Aegisub's UnknownSubtitleFormatError)."""


class UnsupportedOperationError(SubtitleFormatParseError):
    """This format does not read, or does not write (Aegisub's base class simply does nothing)."""


class TagMode(StrEnum):
    """What a writer does with the `{...}` override tags in a line.

    KEEP
        Leave the text as it is. The default for .srt, .vtt and .ttxt: players built on libass
        understand the tags, and a style table is not written either way.
    STRIP
        `AssDialogue.get_stripped_text()`, i.e. SubtitleFormat::StripTags. The default for the
        plain-text formats, and what Aegisub does for all of them.
    HTML
        The `<b>`/`<i>`/`<u>`/`<s>` translation of SRTSubtitleFormat::ConvertTags (only .srt and
        .vtt accept it; every other tag is dropped, as in Aegisub).
    """

    KEEP = "keep"
    STRIP = "strip"
    HTML = "html"


class TextWriter:
    """TextFileWriter: a UTF-8 BOM, then whole lines ending in CRLF."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def line(self, text: str = "", add_linebreak: bool = True) -> None:
        self.parts.append(text + LINEBREAK if add_linebreak else text)

    def data(self) -> bytes:
        return BOM + to_bytes("".join(self.parts))


def decode_text(raw: bytes) -> str:
    """Decode a subtitle file the way TextFileReader does, as one string with LF line endings.

    Like ass.parser.decode_subtitle_bytes, only UTF-8 (BOM optional) and UTF-16 with a BOM are
    accepted; Aegisub would run charset detection, which this tool deliberately does not
    reproduce. Unlike that function the lines are not trimmed: a format whose parsing depends on
    a line being empty rather than blank (WebVTT) has to see them as they are.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        text = raw[2:].decode(codec, "surrogatepass")
        text = from_bytes(text.encode("utf-8", "surrogatepass"))
    else:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UnsupportedEncodingError(
                f"input is not valid UTF-8 (byte offset {e.start}); only UTF-8 and UTF-16 with BOM are supported"
            ) from None
        text = from_bytes(raw)
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def split_lines(raw: bytes) -> list[str]:
    """decode_text split into lines (no trailing empty line for a file ending in a newline)."""
    lines = decode_text(raw).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def set_script_info(target: AssFile, parser: AssParser, key: str, value: str, index: int | None = None) -> None:
    """Set a [Script Info] key, replacing the line that is already there if there is one.

    `index` is where to insert a key the file does not have yet (default: at the end), so that a
    reader can put PlayResX/PlayResY where Aegisub's LoadDefault would have.
    """
    lk = key.lower()
    for info in target.info:
        if info.key.lower() == lk:
            info.value = value
            return
    entry = parser.make_info(key, value)
    if index is None:
        target.info.append(entry)
    else:
        target.info.insert(index, entry)


class SubtitleFormat(ABC):  # noqa: B024  # noqa: B024
    """A subtitle format: ``SRTSubtitleFormat().read(data)``, ``...write(file)``.

    `name`, `read_wildcards` and `write_wildcards` are the class's identity (GetName,
    GetReadWildcards, GetWriteWildcards); an empty wildcard tuple means the format cannot be read
    or written, exactly as in Aegisub. `default_tags` is this port's addition: the TagMode a
    format uses when the caller does not pick one.
    """

    name: ClassVar[str] = ""
    read_wildcards: ClassVar[tuple[str, ...]] = ()
    write_wildcards: ClassVar[tuple[str, ...]] = ()
    default_tags: ClassVar[TagMode] = TagMode.STRIP

    def __init__(self, tags: TagMode | str | None = None, parser_cls: type[AssParser] | None = None) -> None:
        """`tags`: what to do with override tags when writing (None: the format's default).

        `parser_cls`: the AssParser subclass whose make_* hooks build the entries when reading,
        so that a caller who parses .ass files into its own classes gets those here as well.
        """
        self.tags = TagMode(tags) if tags is not None else self.default_tags
        self.parser_cls = parser_cls

    # -- identity -----------------------------------------------------------------------------

    @classmethod
    def can_read_file(cls, filename: Path | str) -> bool:
        """SubtitleFormat::CanReadFile: does the name end in one of read_wildcards?"""
        low = str(filename).lower()
        return any(low.endswith("." + ext) for ext in cls.read_wildcards)

    @classmethod
    def can_write_file(cls, filename: Path | str) -> bool:
        """SubtitleFormat::CanWriteFile: does the name end in one of write_wildcards?"""
        low = str(filename).lower()
        return any(low.endswith("." + ext) for ext in cls.write_wildcards)

    def can_save(self, file: AssFile) -> bool:
        """SubtitleFormat::CanSave: no attachments, no non-default style, no override tags.

        Aegisub uses this to warn that an export will lose something. Nothing here calls it --
        with the default TagMode the tags survive -- but a caller that wants the warning can.
        """
        if file.attachments:
            return False
        return all(line.style == "Default" and line.get_stripped_text() == line.text for line in file.events)

    # -- reading and writing ------------------------------------------------------------------

    def read(self, raw: bytes) -> AssFile:
        """SubtitleFormat::ReadFile, on the file's bytes."""
        raise UnsupportedOperationError(f"{self.name} files cannot be read")

    def read_path(self, path: Path) -> AssFile:
        """`read` on the contents of `path` (a format that needs the file itself overrides this)."""
        return self.read(path.read_bytes())

    def write(self, file: AssFile) -> bytes:
        """SubtitleFormat::WriteFile: the bytes of `file` in this format."""
        raise UnsupportedOperationError(f"{self.name} files cannot be written")

    def export(self, file: AssFile) -> bytes:
        """SubtitleFormat::ExportFile, which by default is WriteFile."""
        return self.write(file)

    def write_path(self, file: AssFile, path: Path) -> None:
        """Write `file` to `path` atomically (ass.writer.write_ass_file)."""
        from aegisub_render.ass.writer import write_ass_file

        write_ass_file(self.write(file), path)

    # -- the conversion helpers SubtitleFormat offers its subclasses --------------------------

    @staticmethod
    def sort(file: AssFile) -> None:
        """AssFile::Sort() with its default comparator, CompStart (stable, as in Aegisub)."""
        file.events.sort(key=lambda d: d.start)

    @staticmethod
    def strip_tags(file: AssFile) -> None:
        """SubtitleFormat::StripTags."""
        for line in file.events:
            line.strip_tags()

    @staticmethod
    def convert_newlines(file: AssFile, newline: str, merge_line_breaks: bool = True) -> None:
        r"""SubtitleFormat::ConvertNewlines: `\h` becomes a space, `\n` and `\N` the newline."""
        for line in file.events:
            repl = line.text.replace("\\h", " ")  # replace_all: case sensitive, unlike the next
            repl = repl.replace("\\n", newline).replace("\\N", newline)  # ireplace_all("\\n", ..)
            if merge_line_breaks:
                dbl = newline + newline
                while dbl in repl:
                    repl = repl.replace(dbl, newline)
            line.text = repl

    @staticmethod
    def strip_comments(file: AssFile) -> None:
        """SubtitleFormat::StripComments: drop every commented and empty line."""
        file.events = [line for line in file.events if not line.comment and line.text]

    @staticmethod
    def recombine_overlaps(file: AssFile) -> None:
        """SubtitleFormat::RecombineOverlaps: split and merge so that no two lines overlap.

        http://devel.aegisub.org/wiki/Technical/SplitMerge. The C++ walks an intrusive list with
        two iterators and unlinks the two lines it replaced by destroying them; here `nxt` is the
        index the `next` iterator would have, which the insertions never move (they all land at
        or after it) and the two removals move back by two.
        """
        events = file.events
        nxt = 1
        while nxt < len(events):
            cur = nxt - 1
            if nxt == 0 or events[cur].end <= events[nxt].start:
                nxt += 1
                continue

            prevdlg = events[cur]
            curdlg = events[nxt]
            nxt += 1

            def insert_line(newdlg: AssDialogue, nxt: int = nxt) -> None:
                pos = next((i for i in range(nxt, len(events)) if events[i].start >= newdlg.start), len(events))
                events.insert(pos, newdlg)

            # Is there an A part before the overlap?
            if curdlg.start > prevdlg.start:
                newdlg = prevdlg.copy()
                newdlg.start = prevdlg.start
                newdlg.end = curdlg.start
                newdlg.text = prevdlg.text
                insert_line(newdlg)

            # Overlapping A+B part; an ASS format hard linewrap goes between the two texts
            newdlg = prevdlg.copy()
            newdlg.start = curdlg.start
            newdlg.end = min(prevdlg.end, curdlg.end)
            newdlg.text = curdlg.text + "\\N" + prevdlg.text
            insert_line(newdlg)

            # Is there an A part after the overlap?
            if prevdlg.end > curdlg.end:
                newdlg = prevdlg.copy()
                newdlg.start = curdlg.end
                newdlg.end = prevdlg.end
                newdlg.text = prevdlg.text
                insert_line(newdlg)

            # Is there a B part after the overlap?
            if curdlg.end > prevdlg.end:
                newdlg = prevdlg.copy()
                newdlg.start = prevdlg.end
                newdlg.end = curdlg.end
                newdlg.text = curdlg.text
                insert_line(newdlg)

            del events[cur : cur + 2]  # the two lines the new ones replaced
            nxt -= 2

    @staticmethod
    def merge_identical(file: AssFile) -> None:
        """SubtitleFormat::MergeIdentical: merge sequential identical lines."""
        events = file.events
        dropped: set[int] = set()
        for i in range(len(events) - 1):
            cur, nxt = events[i], events[i + 1]
            if cur.end == nxt.start and cur.text == nxt.text:
                nxt.start = min(nxt.start, cur.start)
                nxt.end = max(nxt.end, cur.end)
                dropped.add(id(cur))
        if dropped:
            file.events = [line for line in events if id(line) not in dropped]

    def prepare(self, file: AssFile, newline: str | None = None, merge_line_breaks: bool = True) -> AssFile:
        """The conversion every writer here starts with, on a copy of the file.

        Sort, StripComments, RecombineOverlaps, MergeIdentical, StripTags (only for
        TagMode.STRIP), then ConvertNewlines if `newline` is given -- the order Aegisub's TTXT
        and TranStation writers use. TagMode.HTML is left to the format, which translates each
        line as it writes it.
        """
        copy = file.copy()
        self.sort(copy)
        self.strip_comments(copy)
        self.recombine_overlaps(copy)
        self.merge_identical(copy)
        if self.tags is TagMode.STRIP:
            self.strip_tags(copy)
        if newline is not None:
            self.convert_newlines(copy, newline, merge_line_breaks)
        return copy

    # -- the pieces a reader builds a file out of ---------------------------------------------

    def new_file(self) -> tuple[AssFile, AssParser]:
        """An empty file and a parser for it, so the make_* hooks decide which classes are built."""
        cls = self.parser_cls or AssParser
        target = cls.make_file()
        return target, cls(target, 1)

    def new_dialogue(self, parser: AssParser | None = None) -> AssDialogue:
        """A blank event, through the parser's make_dialogue hook (`new AssDialogue` in Aegisub)."""
        return (parser if parser is not None else self.new_file()[1]).make_dialogue(_BLANK_DIALOGUE)

    def load_default(self, target: AssFile, parser: AssParser, include_dialogue_line: bool = False) -> None:
        """AssFile::LoadDefault with Aegisub's defaults (no style catalog, resolution left auto)."""
        for key, value in (
            ("Title", "Default Aegisub file"),
            ("ScriptType", "v4.00+"),
            ("WrapStyle", "0"),
            ("ScaledBorderAndShadow", "yes"),
        ):
            target.info.append(parser.make_info(key, value))
        target.styles.append(parser.make_style(AssStyle().entry_data(), 1))
        if include_dialogue_line:
            target.events.append(self.new_dialogue(parser))
