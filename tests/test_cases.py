"""Snapshot tests over the generated cases in tests/cases (see scripts/gen_cases.py).

* tests/snapshots/<case>.ass is what aegisub-render produced when the snapshot was recorded
  (unverified). Re-record with:  AEGISUB_RENDER_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cases.py
* tests/aegisub/<case>.ass, when present, is the same case applied and saved in Aegisub 3.5.0.
  The output must then match it byte for byte, except for the Project Garbage lines that only
  record GUI state (scroll position, active line, video position, zoom).
"""

import io
import os
from pathlib import Path

import pytest

from aegisub_render.ass.writer import serialize_ass
from aegisub_render.output import Output
from aegisub_render.runner import RenderOptions, prepare
from aegisub_render.settings import Settings
from aegisub_render.worker import execute

HERE = Path(__file__).parent
CASES = sorted((HERE / "cases").glob("*.ass"))
SNAPSHOTS = HERE / "snapshots"
AEGISUB = HERE / "aegisub"
UPDATE = os.environ.get("AEGISUB_RENDER_UPDATE_SNAPSHOTS") == "1"
GUI_STATE_KEYS = ("Scroll Position: ", "Active Line: ", "Video Position: ", "Video Zoom Percent: ")


def render_case(path: Path, settings: Settings) -> tuple[bytes, str]:
    stream = io.StringIO()
    out = Output(stream=stream, progress=False)
    prepared = prepare(path, settings, RenderOptions(progress=False), out)
    result = execute(prepared.job, out)
    assert result.status == "ok", stream.getvalue() + result.message
    assert result.ass is not None
    return serialize_ass(result.ass, prepared.save_ui_state), stream.getvalue()


def without_gui_state(data: bytes) -> list[str]:
    return [ln for ln in data.decode("utf-8-sig").split("\r\n") if not ln.startswith(GUI_STATE_KEYS)]


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_case(case: Path, settings: Settings) -> None:
    first, log = render_case(case, settings)
    second, _ = render_case(case, settings)
    assert first == second, "rendering is not deterministic"
    assert "runtime error" not in log, log

    snapshot = SNAPSHOTS / case.name
    if UPDATE or not snapshot.exists():
        if not UPDATE:
            pytest.fail(f"no snapshot for {case.name}; record it with AEGISUB_RENDER_UPDATE_SNAPSHOTS=1")
        SNAPSHOTS.mkdir(exist_ok=True)
        snapshot.write_bytes(first)
    else:
        assert first == snapshot.read_bytes(), f"output differs from {snapshot.relative_to(HERE)}"

    reference = AEGISUB / case.name
    if reference.exists():
        assert without_gui_state(first) == without_gui_state(reference.read_bytes())
