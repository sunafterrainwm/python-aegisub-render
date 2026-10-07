"""Aegisub path tokens (libaegisub/common/path.cpp, windows/path_win.cpp, main.cpp)."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

TOKENS = ("?audio", "?data", "?dictionary", "?local", "?script", "?temp", "?user", "?video")


def _find_token(text: str) -> str | None:
    if len(text) < 5 or text[0] != "?":
        return None
    for tok in TOKENS:
        if text.startswith(tok) and tok[1] == text[1] and tok[4] == text[4]:
            return tok
    return None


def is_dummy_path(text: str) -> bool:
    return text.startswith(("?dummy", "dummy-audio:"))


def make_preferred(text: str) -> str:
    return text.replace("/", "\\")


@dataclass
class AegisubPaths:
    tokens: dict[str, str] = field(default_factory=dict[str, str])

    @staticmethod
    def create(aegisub_root: Path, load_appdata: bool | None) -> "AegisubPaths":
        data = str(aegisub_root.resolve())
        if load_appdata is None:
            # main.cpp: a config.json next to aegisub.exe means a portable installation
            load_appdata = not (aegisub_root / "config.json").exists()
        p = AegisubPaths()
        p.set_token("?data", data)
        p.set_token("?temp", tempfile.gettempdir())
        if load_appdata:
            p.set_token("?user", str(Path(os.environ.get("APPDATA", "")) / "Aegisub"))
            p.set_token("?local", str(Path(os.environ.get("LOCALAPPDATA", "")) / "Aegisub"))
        else:
            p.set_token("?user", data)
            p.set_token("?local", data)
        p.set_token("?dictionary", p.decode("?data/dictionaries"))
        return p

    def set_token(self, token: str, value: str) -> None:
        """Path::SetToken: relative values clear the token; a file path is replaced by its directory."""
        if not value:
            self.tokens[token] = ""
            return
        pv = PureWindowsPath(value)
        if not pv.is_absolute():
            self.tokens[token] = ""
            return
        value = make_preferred(value)
        if Path(value).is_file():
            value = str(PureWindowsPath(value).parent)
        self.tokens[token] = value

    def get(self, token: str) -> str:
        return self.tokens.get(token, "")

    def decode(self, text: str) -> str:
        """Path::Decode."""
        tok = _find_token(text)
        if tok is None or not self.tokens.get(tok):
            return make_preferred(text)
        rest = text[len(tok) :]
        if rest and rest[0] in "/\\":
            rest = rest[1:]
        if not rest:
            return self.tokens[tok]
        return make_preferred(join(self.tokens[tok], rest))

    def make_absolute(self, text: str, token: str) -> str:
        """Path::MakeAbsolute."""
        if not text:
            return text
        text = make_preferred(text)
        if is_dummy_path(text):
            return text
        base = self.tokens.get(token, "")
        if not base or PureWindowsPath(text).is_absolute():
            return text
        return join(base, text)


def join(base: str, rest: str) -> str:
    """std::filesystem::path operator/ for the common (relative rest) case."""
    if not base:
        return rest
    if PureWindowsPath(rest).is_absolute():
        return rest
    if base.endswith(("\\", "/")):
        return base + rest
    return base + "\\" + rest
