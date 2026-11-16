"""TranStation (.transtation.txt), ported from src/subtitle_format_transtation.cpp (writing only).

TranStationSubtitleFormat::WriteFile, faithfully, including the quirks: the vertical and
horizontal alignment letters come from the line's *style*, a line whose text contains `\\i1`
anywhere is marked italic whatever the style says, the end timestamp of a line that touches the
next one is pulled back by one frame because TranStation's end time is inclusive, and the file
ends with a bare `SUB[` line.

Aegisub asks for the frame rate with a dialog (AskForFPS, with VFR not offered); here it is the
`fps` argument -- a Framerate, so the CLI's --fps or --timecodes can supply it. Unlike Aegisub a
VFR timecode file is accepted and used as it is.
"""

from typing import ClassVar, override

from aegisub_render.ass.entries import AssDialogue
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode, TextWriter, UnsupportedOperationError
from aegisub_render.ass.parser import AssParser
from aegisub_render.video.vfr import END, Framerate, SmpteFormatter


class TranStationSubtitleFormat(SubtitleFormat):
    """TranStationSubtitleFormat. `fps` is required to write (Aegisub asks for it)."""

    name: ClassVar[str] = "TranStation"
    write_wildcards: ClassVar[tuple[str, ...]] = ("transtation.txt",)
    default_tags: ClassVar[TagMode] = TagMode.STRIP

    def __init__(
        self,
        tags: TagMode | str | None = None,
        parser_cls: type[AssParser] | None = None,
        fps: Framerate | None = None,
    ) -> None:
        super().__init__(tags, parser_cls)
        self.fps = fps

    @override
    def write(self, file: AssFile) -> bytes:
        fps = self.fps
        if fps is None or not fps.is_loaded():
            raise UnsupportedOperationError(
                "TranStation files are timed in SMPTE timecodes, so writing one needs a frame rate "
                "(pass fps=, or --fps / --timecodes on the command line)"
            )

        copy = self.prepare(file, newline="\r\n")
        ft = SmpteFormatter(fps)
        writer = TextWriter()
        prev: AssDialogue | None = None
        for cur in copy.events:
            if prev is not None:
                writer.line(self.convert_line(copy, prev, fps, ft, cur.start))
                writer.line("")
            prev = cur

        if prev is not None:  # flush last line
            writer.line(self.convert_line(copy, prev, fps, ft, -1))

        writer.line("SUB[")  # every file must end with this line
        return writer.data()

    @staticmethod
    def convert_line(
        file: AssFile,
        current: AssDialogue,
        fps: Framerate,
        ft: SmpteFormatter,
        nextl_start: int,
    ) -> str:
        """TranStationSubtitleFormat::ConvertLine."""
        valign = 0
        halign = " "  # default is centered
        line_type = "N"  # no special style
        style = file.get_style(current.style)
        if style is not None:
            if style.alignment >= 4:
                valign = 4
            if style.alignment >= 7:
                valign = 9
            if style.alignment in (1, 4, 7):
                halign = "L"
            if style.alignment in (3, 6, 9):
                halign = "R"
            if style.italic:
                line_type = "I"

        # Hack: If an italics-tag (\i1) appears anywhere in the line, make it all italics
        if "\\i1" in current.text:
            line_type = "I"

        end = current.end
        # Subtract one frame if the end time of the current line is equal to the start of the
        # next one, since the end timestamp is inclusive and the lines would overlap as is.
        if nextl_start > 0 and end == nextl_start:
            end = fps.time_at_frame(fps.frame_at_time(end, END) - 1, END)

        header = f"SUB[{valign}{halign}{line_type} {ft.to_smpte(current.start)}>{ft.to_smpte(end)}]\r\n"
        return header + current.text
