"""Running under WSL: Windows paths in config.py and on the command line mean the Windows drives.

config.py names the Windows Aegisub installation (C:\\Program Files\\Aegisub); under WSL the same
files are at /mnt/c/Program Files/Aegisub. %APPDATA% (for ?user) is asked of Windows itself.
"""

import functools
import re
import subprocess
import sys
from pathlib import Path

_DRIVE_PATH = re.compile(r"([A-Za-z]):(?:[\\/](.*))?", re.DOTALL)


@functools.cache
def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def translate(path: str) -> str:
    """`X:\\dir\\file` -> `/mnt/x/dir/file` under WSL (what `wslpath -u` gives for drive paths);
    anything else, and everything outside WSL, is returned unchanged."""
    if not is_wsl():
        return path
    m = _DRIVE_PATH.fullmatch(path)
    if not m:
        return path
    rest = (m.group(2) or "").replace("\\", "/").strip("/")
    return f"/mnt/{m.group(1).lower()}" + (f"/{rest}" if rest else "")


def translate_path(path: Path | str) -> Path:
    return Path(translate(str(path)))


@functools.cache
def windows_env(name: str) -> str | None:
    """A Windows environment variable, translated if it is a path; None outside WSL or if unset."""
    if not is_wsl():
        return None
    try:
        r = subprocess.run(
            ["cmd.exe", "/d", "/c", f"echo %{name}%"],
            cwd="/mnt/c",  # a UNC working directory makes cmd.exe complain
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    value = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not value or value == f"%{name}%":
        return None
    return translate(value)
