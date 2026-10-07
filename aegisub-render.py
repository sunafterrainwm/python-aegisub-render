"""Command line entry point: uv run aegisub-render.py render input.ass -o output.ass

The project is not installed as a package; this launcher puts the repository on sys.path
so it works from any working directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aegisub_render.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
