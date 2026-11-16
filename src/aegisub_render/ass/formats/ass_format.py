"""Advanced Substation Alpha (.ass/.ssa), ported from src/subtitle_format_ass.cpp.

A thin SubtitleFormat over what `ass.parser` and `ass.writer` already do, so that the dispatcher
in this package can treat .ass like any other format. Reading an .ssa file parses it as SSA (v4)
exactly as ass.parser.read_ass_file does; writing always writes ASS, since Aegisub's .ssa writer
(subtitle_format_ssa.cpp) is not ported.
"""

from pathlib import Path
from typing import ClassVar, override

from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat, TagMode
from aegisub_render.ass.parser import AssParser


class AssSubtitleFormat(SubtitleFormat):
    """AssSubtitleFormat. `tags` is ignored: an .ass file keeps its text as it is."""

    name: ClassVar[str] = "Advanced Substation Alpha"
    read_wildcards: ClassVar[tuple[str, ...]] = ("ass", "ssa")
    write_wildcards: ClassVar[tuple[str, ...]] = ("ass",)
    default_tags: ClassVar[TagMode] = TagMode.KEEP

    def __init__(
        self,
        tags: TagMode | str | None = None,
        parser_cls: type[AssParser] | None = None,
        save_ui_state: bool = True,
        keep_brand: bool = False,
    ) -> None:
        super().__init__(tags, parser_cls)
        self.save_ui_state = save_ui_state
        self.keep_brand = keep_brand

    @override
    def read(self, raw: bytes) -> AssFile:
        """Parse .ass bytes. A v4 (SSA) file is recognised by its own `ScriptType:` line."""
        return AssFile.from_bytes(raw, 1, self.parser_cls)

    @override
    def read_path(self, path: Path) -> AssFile:
        """read_ass_file: an .ssa suffix starts the parser in SSA mode, as Aegisub does."""
        return AssFile.from_path(path, self.parser_cls)

    @override
    def write(self, file: AssFile) -> bytes:
        """SubsController::Save (ass.writer.serialize_ass)."""
        return file.serialize(self.save_ui_state, self.keep_brand)

    @override
    def export(self, file: AssFile) -> bytes:
        """File > Export (ass.writer.serialize_export)."""
        return file.export(self.save_ui_state, self.keep_brand)
