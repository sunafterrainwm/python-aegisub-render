"""Command line entry point: uv run aegisub-render.py render input.ass -o output.ass

This launcher puts the repository's src/ on sys.path, so it works from any working directory even in an
environment where the project itself was not installed (`uv add --editable <repo>` installs it as a
link to the repository and provides the same entry point as the `aegisub-render` console script).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from aegisub_render.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
