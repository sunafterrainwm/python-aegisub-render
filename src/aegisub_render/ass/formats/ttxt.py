"""MPEG-4 Timed Text (.ttxt), ported from src/subtitle_format_ttxt.cpp (writing only).

Aegisub can read TTXT as well; this port writes it, which is what the format is used for. The
structure, the attribute values and the order they are written in are TTXTSubtitleFormat's,
including the detail that WriteHeader nests TextSampleDescription inside TextStreamHeader and
FontTable, TextBox and Style inside that.

`default_duration` is Aegisub's `Timing/Default Duration`, the end time ConvertToTTXT gives the
blank line it appends. It never reaches the file -- WriteLine only ever writes `sampleTime`, which
is that line's *start* -- and is here because the upstream code has it.

Two things are not byte-for-byte Aegisub's, because they are wxWidgets', not Aegisub's: the XML
is written the way wxXmlDocument::Save writes it (two-space indentation, LF line endings, no byte
order mark, an element with a text child on one line) as closely as this reproduces it, and the
`Timing/Default Duration` option behind the trailing blank sample is the stock 2000 ms unless
`default_duration` says otherwise.
"""

from typing import ClassVar, override

from aegisub_render.ass.entries import AssDialogue
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode
from aegisub_render.ass.parser import AssParser
from aegisub_render.ass.time import ass_formatted, clamp_time
from aegisub_render.textenc import to_bytes

DEFAULT_DURATION = 2000  # OPT_GET("Timing/Default Duration"), Aegisub's stock value


class _Node:
    """Just enough of wxXmlNode to write the document the same way."""

    __slots__ = ("attrs", "children", "name", "text")

    def __init__(self, name: str, **attrs: str) -> None:
        self.name = name
        self.attrs: list[tuple[str, str]] = list(attrs.items())
        self.children: list[_Node] = []
        self.text: str | None = None

    def attr(self, name: str, value: str) -> _Node:
        """Add an attribute whose name is not a Python identifier (xml:space)."""
        self.attrs.append((name, value))
        return self

    def child(self, node: _Node) -> _Node:
        self.children.append(node)
        return node


def _escape(text: str, attribute: bool) -> str:
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if attribute:
        out = out.replace('"', "&quot;").replace("\t", "&#x9;").replace("\n", "&#xA;").replace("\r", "&#xD;")
    return out


def _write_node(out: list[str], node: _Node, indent: int, step: int) -> None:
    pad = " " * indent
    attrs = "".join(f' {name}="{_escape(value, True)}"' for name, value in node.attrs)
    if node.text is None and not node.children:
        out.append(f"{pad}<{node.name}{attrs}/>\n")
        return
    if node.text is not None:
        # wx writes an element with a text child on one line, whatever the text contains
        out.append(f"{pad}<{node.name}{attrs}>{_escape(node.text, False)}</{node.name}>\n")
        return
    out.append(f"{pad}<{node.name}{attrs}>\n")
    for child in node.children:
        _write_node(out, child, indent + step, step)
    out.append(f"{pad}</{node.name}>\n")


class TTXTSubtitleFormat(SubtitleFormat):
    """TTXTSubtitleFormat (MPEG-4 Streaming Text)."""

    name: ClassVar[str] = "MPEG-4 Streaming Text"
    write_wildcards: ClassVar[tuple[str, ...]] = ("ttxt",)
    default_tags: ClassVar[TagMode] = TagMode.KEEP

    def __init__(
        self,
        tags: TagMode | str | None = None,
        parser_cls: type[AssParser] | None = None,
        default_duration: int = DEFAULT_DURATION,
    ) -> None:
        super().__init__(tags, parser_cls)
        self.default_duration = default_duration

    @override
    def write(self, file: AssFile) -> bytes:
        copy = self.convert_to_ttxt(file)

        root = _Node("TextStream", version="1.1")
        self.write_header(root)
        prev: AssDialogue | None = None
        for current in copy.events:
            self.write_line(root, prev, current)
            prev = current

        out = ['<?xml version="1.0" encoding="UTF-8"?>\n']
        _write_node(out, root, 0, 2)
        return to_bytes("".join(out))

    def convert_to_ttxt(self, file: AssFile) -> AssFile:
        """TTXTSubtitleFormat::ConvertToTTXT, plus the trailing blank line it appends."""
        copy = self.prepare(file, newline="\r\n")
        last_time = copy.events[-1].end if copy.events else 0
        blank = self.new_dialogue()
        blank.start = last_time
        blank.end = clamp_time(last_time + self.default_duration)
        copy.events.append(blank)
        return copy

    @staticmethod
    def write_header(root: _Node) -> None:
        """TTXTSubtitleFormat::WriteHeader, with its nesting as the C++ builds it."""
        header = root.child(
            _Node("TextStreamHeader", width="400", height="60", layer="0", translation_x="0", translation_y="0")
        )
        description = header.child(
            _Node(
                "TextSampleDescription",
                horizontalJustification="center",
                verticalJustification="bottom",
                backColor="0 0 0 0",
                verticalText="no",
                fillTextRegion="no",
                continuousKaraoke="no",
                scroll="None",
            )
        )
        font_table = description.child(_Node("FontTable"))
        font_table.child(_Node("FontTableEntry", fontName="Sans", fontID="1"))
        description.child(_Node("TextBox", top="0", left="0", bottom="60", right="400"))
        description.child(_Node("Style", styles="Normal", fontID="1", fontSize="18", color="ff ff ff ff"))

    @staticmethod
    def write_line(root: _Node, prev: AssDialogue | None, line: AssDialogue) -> None:
        """TTXTSubtitleFormat::WriteLine: a blank sample fills a gap after the previous line."""
        if prev is not None and prev.end != line.start:
            blank = root.child(_Node("TextSample", sampleTime="0" + ass_formatted(prev.end, True)))
            blank.attr("xml:space", "preserve")
            blank.text = ""
        node = root.child(_Node("TextSample", sampleTime="0" + ass_formatted(line.start, True)))
        node.attr("xml:space", "preserve")
        node.text = line.text
