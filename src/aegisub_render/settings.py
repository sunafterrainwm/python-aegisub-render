"""Loading config.py (typed module constants) and Aegisub's own options file."""

import json
import os
import runpy
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from aegisub_render.paths import AegisubPaths
from aegisub_render.wsl import translate_path

# the repository's own config.py, two levels up from this package (src/aegisub_render/ -> ../../)
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.py"


class ConfigError(Exception):
    pass


@dataclass(slots=True)
class Settings:
    aegisub_root: Path = Path(r"C:\Program Files\Aegisub")
    load_appdata: bool | None = None
    default_script: str = "kara-templater.lua"
    default_macro: str = "Apply karaoke template"
    trace_level: int = 3
    timeout_seconds: float = 120.0
    memory_limit_bytes: int = 512_000_000
    max_output_lines: int = 50_000
    max_input_lines: int = 100_000
    font_dirs: list[Path] = field(default_factory=list[Path])
    source: Path | None = None


_FIELDS: dict[str, tuple[str, tuple[type, ...]]] = {
    "AEGISUB_ROOT": ("aegisub_root", (Path, str)),
    "LOAD_APPDATA": ("load_appdata", (bool, type(None))),
    "DEFAULT_SCRIPT": ("default_script", (str,)),
    "DEFAULT_MACRO": ("default_macro", (str,)),
    "TRACE_LEVEL": ("trace_level", (int,)),
    "TIMEOUT_SECONDS": ("timeout_seconds", (int, float)),
    "MEMORY_LIMIT_BYTES": ("memory_limit_bytes", (int,)),
    "MAX_OUTPUT_LINES": ("max_output_lines", (int,)),
    "MAX_INPUT_LINES": ("max_input_lines", (int,)),
    "FONT_DIRS": ("font_dirs", (list,)),
}


def find_config(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    env = os.environ.get("AEGISUB_RENDER_CONFIG")
    if env:
        return Path(env)
    return REPO_CONFIG if REPO_CONFIG.is_file() else None


def load_settings(explicit: Path | None = None) -> Settings:
    settings = Settings()
    path = find_config(explicit)
    if path is None:
        return settings
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    namespace = runpy.run_path(str(path))
    for name, (attr, types) in _FIELDS.items():
        if name not in namespace:
            continue
        value: object = namespace[name]
        wanted = " or ".join(t.__name__ for t in types)
        if (isinstance(value, bool) and bool not in types) or not isinstance(value, types):
            raise ConfigError(f"{path}: {name} must be {wanted}, got {type(value).__name__}")
        match attr:
            case "aegisub_root":
                assert isinstance(value, (str, Path))
                settings.aegisub_root = translate_path(value)
            case "font_dirs":
                assert isinstance(value, list)
                items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
                if not all(isinstance(v, (str, Path)) for v in items):
                    raise ConfigError(f"{path}: FONT_DIRS must be a list of paths")
                settings.font_dirs = [translate_path(str(v)) for v in items]
            case "timeout_seconds":
                assert isinstance(value, (int, float))
                settings.timeout_seconds = float(value)
            case _:
                setattr(settings, attr, value)
    settings.source = path
    return settings


@dataclass(slots=True)
class AegisubOptions:
    """The few Aegisub options (?user/config.json) that change what a run produces."""

    include: str = "?user/automation/include/|?data/automation/include/"
    autoload: str = "?user/automation/autoload/|?data/automation/autoload/"
    save_ui_state: bool = True
    open_audio: bool = True


def read_aegisub_options(paths: AegisubPaths) -> AegisubOptions:
    opts = AegisubOptions()
    user = paths.get("?user")
    if not user:
        return opts
    try:
        data = json.loads((Path(user) / "config.json").read_text(encoding="utf-8-sig"))
    except OSError, ValueError:
        return opts
    if not isinstance(data, dict):
        return opts

    def get(*keys: str) -> object:
        cur = cast(object, data)
        for k in keys:
            if not isinstance(cur, dict):
                return None
            table: dict[str, object] = cur  # pyright: ignore[reportUnknownVariableType]
            cur = table.get(k)
        return cur

    include = get("Path", "Automation", "Include")
    if isinstance(include, str):
        opts.include = include
    autoload = get("Path", "Automation", "Autoload")
    if isinstance(autoload, str):
        opts.autoload = autoload
    save_ui = get("App", "Save UI State")
    if isinstance(save_ui, bool):
        opts.save_ui_state = save_ui
    open_audio = get("Video", "Open Audio")
    if isinstance(open_audio, bool):
        opts.open_audio = open_audio
    return opts


def automation_dirs(paths: AegisubPaths, option: str) -> list[str]:
    """Decode a '|'-separated Path/Automation option; keep absolute, existing directories."""
    out: list[str] = []
    for tok in option.split("|"):
        d = paths.decode(tok)
        if Path(d).is_absolute() and Path(d).is_dir():
            out.append(d)
    return out
