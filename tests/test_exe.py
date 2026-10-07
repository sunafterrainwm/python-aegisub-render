"""aegisub-render.exe built by scripts/build_exe.py."""

import subprocess
import sys
from pathlib import Path

from aegisub_render.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_exe_runs_the_cli(tmp_path: Path, settings: Settings) -> None:
    exe = tmp_path / "bin" / "aegisub-render.exe"
    exe.parent.mkdir()
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_exe.py"), "-o", str(exe), "--python", sys.executable],
        check=True,
        capture_output=True,
        timeout=60,
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        # from another directory: the .exe holds absolute paths
        return subprocess.run(
            [str(exe), *args],
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )

    r = run("--help")
    assert r.returncode == 0 and r.stdout.startswith("usage: aegisub-render"), r.stderr
    assert run("render", "missing.ass").returncode == 1  # exit codes pass through the launcher

    case = ROOT / "tests" / "cases" / "01-basic-syl.ass"
    r = run("render", str(case), "-o", "out.ass")  # runs the Lua worker process too
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out.ass").read_bytes() == (ROOT / "tests" / "snapshots" / case.name).read_bytes()
