"""ScriptRender: the Lua worker's lifecycle, the validate/run pair, and what run() updates."""

import dataclasses
import io
from pathlib import Path

import pytest

from aegisub_render.ass.file import AssFile
from aegisub_render.output import Output
from aegisub_render.script_render import (
    RenderError,
    ScriptCancelled,
    ScriptFailed,
    ScriptRefused,
    ScriptRender,
)
from aegisub_render.settings import Settings

from .conftest import MINIMAL_ASS

# A macro whose validation function answers what the file says, and whose macro records that it
# ran. The counter proves validate and run share one Lua state: the macro refuses to run unless
# the validation function ran first, in the same state.
SCRIPT = """
script_name = "lifecycle"
script_description = "validate and run in one state"
script_author = "tests"
script_version = "1"

validated = 0

function can_run(subs, sel)
    validated = validated + 1
    for i = 1, #subs do
        if subs[i].class == "dialogue" and subs[i].text == "no" then return false end
    end
    return true
end

function apply(subs, sel)
    local line = subs[#subs]
    line.text = "validated=" .. validated
    subs[#subs] = line
end

aegisub.register_macro("Run me", "help text", apply, can_run)
aegisub.register_filter("Filter me", "filter help", 100, function(subs, cfg) end)
"""

CANCEL = """
script_name = "cancel"
script_description = "cancels"
function apply(subs, sel) aegisub.cancel() end
aegisub.register_macro("Cancel", "", apply)
"""

FAIL = """
script_name = "fail"
script_description = "raises"
function apply(subs, sel) error("on purpose") end
aegisub.register_macro("Fail", "", apply)
"""


def make(tmp_path: Path, lua: str, ass_text: str = MINIMAL_ASS, name: str = "lifecycle.lua") -> tuple[Path, AssFile]:
    (tmp_path / name).write_text(lua, encoding="utf-8")
    return tmp_path / name, AssFile.from_text(ass_text)


def render(
    tmp_path: Path, settings: Settings, lua: str, *, ass_text: str = MINIMAL_ASS, macro: str = "Run me", **kw: object
) -> ScriptRender:
    script, ass = make(tmp_path, lua, ass_text)
    return ScriptRender(
        ass,
        script,
        settings,
        subtitle_path=tmp_path / "input.ass",
        macro=macro,
        progress=False,
        out=Output(stream=io.StringIO(), progress=False),
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("isolate", [True, False], ids=["worker", "in-process"])
def test_validate_and_run_share_one_lua_state(tmp_path: Path, settings: Settings, isolate: bool) -> None:
    """macro_can_run() then run(): the macro sees that the validation function ran, in the same
    state, which is what Aegisub does."""
    with render(tmp_path, settings, SCRIPT, isolate=isolate) as sr:
        assert sr.macro_can_run() is True
        assert sr.refusal_reason() is None
        ass = sr.run()
        # validated == 2: once for macro_can_run(), once for the check run() does itself
        assert ass.events[-1].text == "validated=2"


def test_run_updates_the_assfile_it_was_given(tmp_path: Path, settings: Settings) -> None:
    """The worker is another process, but run() moves its result into the caller's object."""
    script, ass = make(tmp_path, SCRIPT)
    sr = ScriptRender(
        ass, script, settings, subtitle_path=tmp_path / "input.ass", macro="Run me", progress=False,
        out=Output(stream=io.StringIO(), progress=False),
    )  # fmt: skip
    with sr:
        returned = sr.run()
    assert returned is ass
    assert ass.events[-1].text.startswith("validated=")


def test_macro_can_run_says_no_with_a_reason(tmp_path: Path, settings: Settings) -> None:
    refusing = MINIMAL_ASS.replace("second line", "no")
    with render(tmp_path, settings, SCRIPT, ass_text=refusing) as sr:
        assert sr.macro_can_run() is False
        reason = sr.refusal_reason()
        assert reason is not None and "is disabled for this file" in reason and "--force" in reason
        with pytest.raises(ScriptRefused, match="is disabled for this file"):
            sr.run()


def test_force_runs_a_refused_macro(tmp_path: Path, settings: Settings) -> None:
    refusing = MINIMAL_ASS.replace("second line", "no")
    with render(tmp_path, settings, SCRIPT, ass_text=refusing, force=True) as sr:
        assert sr.macro_can_run() is False  # the answer does not change ...
        assert sr.run().events[-1].text.startswith("validated=")  # ... but force runs it anyway


def test_features_lists_what_the_script_registers(tmp_path: Path, settings: Settings) -> None:
    with render(tmp_path, settings, SCRIPT) as sr:
        features = sr.features()
        assert features.script_name == "lifecycle"
        assert [name for name, _ in features.macros] == ["Run me"]
        assert [name for name, _, _ in features.filters] == ["Filter me"]
        assert sr.features() is features  # cached: one request


def test_a_filter_has_nothing_to_validate(tmp_path: Path, settings: Settings) -> None:
    script, ass = make(tmp_path, SCRIPT)
    sr = ScriptRender(
        ass, script, settings, subtitle_path=tmp_path / "input.ass", filter="Filter me", progress=False,
        out=Output(stream=io.StringIO(), progress=False),
    )  # fmt: skip
    with sr:
        assert sr.is_filter is True
        assert sr.macro_can_run() is True
        sr.run()


def test_no_such_macro_is_refused(tmp_path: Path, settings: Settings) -> None:
    with render(tmp_path, settings, SCRIPT, macro="Nope") as sr, pytest.raises(ScriptRefused, match="named 'Nope'"):
        sr.macro_can_run()


def test_cancel_and_failure_become_exceptions(tmp_path: Path, settings: Settings) -> None:
    with render(tmp_path, settings, CANCEL, macro="Cancel") as sr, pytest.raises(ScriptCancelled):
        sr.run()
    with render(tmp_path, settings, FAIL, macro="Fail") as sr, pytest.raises(ScriptFailed):
        sr.run()


def test_the_worker_is_gone_after_the_with_block(tmp_path: Path, settings: Settings) -> None:
    sr = render(tmp_path, settings, SCRIPT)
    with sr:
        assert sr.alive
        proc = sr._proc
        assert proc is not None
        sr.run()
    assert not sr.alive
    assert not proc.is_alive()
    with pytest.raises(RenderError, match="was stopped"):
        sr.run()


def test_an_unstarted_render_starts_itself(tmp_path: Path, settings: Settings) -> None:
    """A one-liner must work: no with, no start_lua_worker()."""
    sr = render(tmp_path, settings, SCRIPT)
    try:
        assert sr.run().events[-1].text == "validated=1"  # only run()'s own check validated
        assert sr.alive  # it stays open until it is stopped
    finally:
        sr.stop_lua_worker()
    assert not sr.alive


def test_running_twice_applies_the_macro_twice(tmp_path: Path, settings: Settings) -> None:
    with render(tmp_path, settings, SCRIPT) as sr:
        assert sr.run().events[-1].text == "validated=1"
        assert sr.run().events[-1].text == "validated=2"


def test_timeout_kills_the_worker(tmp_path: Path, settings: Settings) -> None:
    spin = """
script_name = "spin"
function apply(subs, sel) while true do end end
aegisub.register_macro("Spin", "", apply)
"""
    quick = dataclasses.replace(settings, timeout_seconds=1.0)
    script, ass = make(tmp_path, spin, MINIMAL_ASS, "spin.lua")
    sr = ScriptRender(
        ass, script, quick, subtitle_path=tmp_path / "input.ass", macro="Spin", progress=False,
        out=Output(stream=io.StringIO(), progress=False),
    )  # fmt: skip
    with sr, pytest.raises(ScriptFailed, match="ran longer than 1 seconds"):
        sr.run()
    assert not sr.alive
