"""Untimed plain text (.txt), ported from src/subtitle_format_txt.cpp (writing only).

Aegisub can import plain text too, but only behind a dialog that asks how to read it (actor
separator, comment starter, whether to keep blank lines), so there is nothing headless to
reproduce; this port writes.

TXTSubtitleFormat::WriteFile, faithfully: the Actor field is written as `Name: ` only if more
than half the dialogue lines have one, commented lines come out prefixed with `# `, a line whose
text is empty is skipped, and nothing is sorted, recombined or merged -- the file is the script's
lines in order, which is the point of exporting to text. The header line names the Aegisub
version followed, with ` with aegisub-render <ver>` appended the way ass.writer.header_lines
does; `keep_brand` writes Aegisub's line alone.
"""

from pathlib import Path
from typing import ClassVar, override

from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode, TextWriter
from aegisub_render.ass.parser import AssParser
from aegisub_render.version import AEGISUB_VERSION_STRING, __version__


class TXTSubtitleFormat(SubtitleFormat):
    """TXTSubtitleFormat (Plain-Text)."""

    name: ClassVar[str] = "Plain-Text"
    write_wildcards: ClassVar[tuple[str, ...]] = ("txt",)
    default_tags: ClassVar[TagMode] = TagMode.STRIP

    def __init__(
        self,
        tags: TagMode | str | None = None,
        parser_cls: type[AssParser] | None = None,
        keep_brand: bool = False,
    ) -> None:
        super().__init__(tags, parser_cls)
        self.keep_brand = keep_brand

    @classmethod
    @override
    def can_write_file(cls, filename: Path | str) -> bool:
        """TXTSubtitleFormat::CanWriteFile: .txt, but not the other two formats that end in it."""
        low = str(filename).lower()
        return low.endswith(".txt") and not low.endswith((".encore.txt", ".transtation.txt"))

    @override
    def write(self, file: AssFile) -> bytes:
        num_actor_names = 0
        num_dialogue_lines = 0
        for dia in file.events:
            if not dia.comment:
                num_dialogue_lines += 1
                if dia.actor:
                    num_actor_names += 1

        # If too few lines have Actor filled out, don't write it
        write_actors = num_actor_names > num_dialogue_lines // 2

        writer = TextWriter()
        brand = f"# Exported by Aegisub {AEGISUB_VERSION_STRING}"
        writer.line(brand if self.keep_brand else f"{brand} with aegisub-render {__version__}")

        for dia in file.events:
            out_line = "# " if dia.comment else ""
            if write_actors:
                out_line += dia.actor + ": "
            out_text = dia.text if self.tags is TagMode.KEEP else dia.get_stripped_text()
            out_line += out_text
            if out_text:
                writer.line(out_line)
        return writer.data()
