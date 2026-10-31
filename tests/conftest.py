import io
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest

from aegisub_render.ass.file import AssFile
from aegisub_render.ass.writer import serialize_ass
from aegisub_render.host.text_extents import register_private_fonts
from aegisub_render.output import Output
from aegisub_render.runner import RenderOptions, prepare
from aegisub_render.settings import Settings, load_settings
from aegisub_render.worker import Result, execute
from aegisub_render.wsl import is_wsl, translate, windows_env

FIXTURES = Path(__file__).parent / "fixtures"

# Under WSL, fontconfig does not know the Windows fonts. The ones the tests use are copied here
# and loaded like FONT_DIRS (FcConfigAppFontAddDir), rather than adding all of C:\Windows\Fonts.
WSL_FONTS = Path(__file__).parent / ".fonts"
TEST_FONTS = (
    "arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf",
    "georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf",
    "msjh.ttc", "msjhbd.ttc",
    "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf",
    "YuGothR.ttc", "YuGothB.ttc",
)  # fmt: skip


def wsl_test_fonts() -> Path | None:
    windows = Path(translate(windows_env("WINDIR") or "C:\\Windows")) / "Fonts"
    WSL_FONTS.mkdir(exist_ok=True)
    for name in TEST_FONTS:
        source, copy = windows / name, WSL_FONTS / name
        if source.is_file() and not copy.exists():
            shutil.copyfile(source, copy)
    return WSL_FONTS if any(WSL_FONTS.iterdir()) else None


MINIMAL_ASS = """[Script Info]
Title: test
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\k20}first {\\k30}line
Dialogue: 0,0:00:04.00,0:00:06.00,Default,actor,0,0,0,karaoke,second line
"""


@dataclass
class Run:
    result: Result
    log: str

    @property
    def ass(self) -> AssFile:
        assert self.result.ass is not None, self.log
        return self.result.ass

    def text(self) -> str:
        return serialize_ass(self.ass).decode("utf-8-sig")


class RunLua(Protocol):
    def __call__(
        self,
        lua: str,
        ass: str = ...,
        macro: str = ...,
        options: RenderOptions | None = ...,
        script_name: str = ...,
    ) -> Run: ...


@pytest.fixture(scope="session", autouse=True)
def _wsl_fonts_in_this_process() -> None:  # pyright: ignore[reportUnusedFunction]
    """Tests that measure text in this process see the copied fonts too (renders get FONT_DIRS)."""
    if is_wsl() and (fonts := wsl_test_fonts()) is not None:
        register_private_fonts([fonts])


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = load_settings()
    if not (s.aegisub_root / "automation").is_dir():
        pytest.skip(f"Aegisub installation not found at {s.aegisub_root}")
    if is_wsl() and (fonts := wsl_test_fonts()) is not None:
        s.font_dirs = [*s.font_dirs, fonts]
    return s


@pytest.fixture
def run_lua(tmp_path: Path, settings: Settings) -> RunLua:
    """Run a macro script (Lua source) against an ASS file in-process and capture the log."""

    def runner(
        lua: str,
        ass: str = MINIMAL_ASS,
        macro: str = "test",
        options: RenderOptions | None = None,
        script_name: str = "test-script.lua",
    ) -> Run:
        script = tmp_path / script_name
        script.write_text(lua, encoding="utf-8")
        sub = tmp_path / "input.ass"
        sub.write_text(ass, encoding="utf-8-sig")
        opts = options or RenderOptions()
        opts.script = str(script)
        opts.macro = macro if opts.filter is None else None
        opts.progress = False
        stream = io.StringIO()
        out = Output(stream=stream, progress=False)
        prepared = prepare(sub, settings, opts, out)
        result = execute(prepared.job, out)
        if result.message:
            out.message(result.message)
        return Run(result, stream.getvalue())

    return runner
