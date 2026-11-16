"""Fix Styles (src/export_fixstyle.cpp)."""

from typing import ClassVar, override

from aegisub_render.ass.file import AssFile
from aegisub_render.automation.base import AutomationContext, ExportFilter


class FixStylesFilter(ExportFilter):
    """AssFixStylesFilter: any line whose style the file does not have becomes Default."""

    name: ClassVar[str] = "Fix Styles"
    description: ClassVar[str] = "Fixes styles by replacing any style that isn't available on file with Default."
    priority: ClassVar[int] = -5000

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        del ctx
        styles = {name.lower() for name in file.get_styles()}
        for diag in file.events:
            if diag.style.lower() not in styles:
                diag.style = "Default"
