"""Export filters: the filtergraph syntax, the two built-in filters, and resolving a name."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, override

import pytest

from aegisub_render.ass.entries import AssDialogue, AssInfo, AssStyle
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.override import ParamClass, parse_dialogue_blocks, set_strict_tag_parsing
from aegisub_render.automation import (
    AutomationContext,
    AutomationError,
    ExportFilter,
    FilterArgs,
    FilterResolver,
    FilterSpecError,
    FixStylesFilter,
    TransformFramerateFilter,
    escape,
    filters,
    get_filter,
    get_macro,
    is_builtin,
    load_plugins,
    macros,
    parse_filter_chain,
    plugin_filters,
    plugin_macros,
    python_files,
    register_filter,
    trunc_cs,
    unregister_filter,
    unregister_macro,
)
from aegisub_render.automation.resolve import CACHE_VERSION, _Scan, cache_path
from aegisub_render.cli import main
from aegisub_render.cutil import float_to_string
from aegisub_render.paths import AegisubPaths
from aegisub_render.runner import RenderOptions, export_filter_chain, garbage_export_filter_names
from aegisub_render.script_render import (
    LUA_SUFFIXES,
    RenderError,
    ScriptRefused,
    ScriptRender,
    resolve_script,
)
from aegisub_render.settings import (
    AUTOLOAD_DIRS_ENV,
    AUTOLOAD_DIRS_PREPEND_ENV,
    Settings,
    effective_autoload,
    extra_autoload_dirs,
)
from aegisub_render.tools import ShiftTimesFilter, parse_offset, shift_events
from aegisub_render.video.vfr import Framerate

CASES = Path(__file__).parent / "cases"


def file_with(*events: tuple[int, int, str], styles: list[AssStyle] | None = None) -> AssFile:
    ass = AssFile()
    ass.info.append(AssInfo("ScriptType", "v4.00+"))
    ass.styles.extend(styles or [AssStyle()])
    for start, end, text in events:
        ass.events.append(AssDialogue(start=start, end=end, text=text))
    return ass


# -- the filtergraph syntax (ffmpeg's) ---------------------------------------------------------


type ChainCase = tuple[str, list[tuple[str, list[str], dict[str, str]]]]

_CHAIN_CASES: list[ChainCase] = [
    ("", []),
    ("Fix Styles", [("Fix Styles", [], {})]),
    ("a,b", [("a", [], {}), ("b", [], {})]),
    ("a,,b", [("a", [], {}), ("b", [], {})]),  # a stray comma is not a filter
    ("a=1", [("a", ["1"], {})]),
    ("a=1:2", [("a", ["1", "2"], {})]),
    ("a=k=v", [("a", [], {"k": "v"})]),
    ("a=1:k=v:j=w", [("a", ["1"], {"k": "v", "j": "w"})]),
    ("a=k=v,b=2", [("a", [], {"k": "v"}), ("b", ["2"], {})]),
    ("  Fix Styles  ,  b  ", [("Fix Styles", [], {}), ("b", [], {})]),
    ("a=", [("a", [], {})]),
    (r"Odd\,Name", [("Odd,Name", [], {})]),
    (r"a=x\:y", [("a", ["x:y"], {})]),
    (r"a=x\=y", [("a", ["x=y"], {})]),
    ("'Quoted,Name'=1", [("Quoted,Name", ["1"], {})]),
    (r"a=b\\c", [("a", ["b\\c"], {})]),
    ("a='x:y'", [("a", ["x:y"], {})]),
]


@pytest.mark.parametrize(("spec", "expected"), _CHAIN_CASES)
def test_parse_filter_chain(spec: str, expected: list[tuple[str, list[str], dict[str, str]]]):
    chain = parse_filter_chain(spec)
    assert [(f.name, f.positional, f.options) for f in chain] == expected


@pytest.mark.parametrize("spec", ["'unterminated", "back\\", "=noname", ":x", "a=k=v=w", "a=k=1:k=2"])
def test_parse_filter_chain_rejects(spec: str):
    with pytest.raises(FilterSpecError):
        parse_filter_chain(spec)


def test_escape_round_trips_a_name_with_syntax_in_it():
    for name in ("Clean Tags - 整理特效標籤", "a,b:c=d", "it's", "back\\slash"):
        assert parse_filter_chain(escape(name))[0].name == name


def test_unparse_round_trips():
    args = FilterArgs("Transform Framerate", ["23.976"], {"output": "25", "odd": "a:b"})
    back = parse_filter_chain(args.unparse())[0]
    assert (back.name, back.positional, back.options) == (args.name, args.positional, args.options)


# -- the registry ------------------------------------------------------------------------------


def test_the_built_in_filters_are_registered_in_priority_order():
    assert [(f.name, f.priority) for f in filters()] == [
        ("Transform Framerate", 1000),
        ("Shift Times", 0),
        ("Fix Styles", -5000),
    ]
    assert get_filter("Fix Styles") is FixStylesFilter
    assert get_filter("No Such Filter") is None


def test_register_filter_inserts_by_priority_and_refuses_a_clash():
    class Middle(ExportFilter):
        name: ClassVar[str] = "Middle"
        priority: ClassVar[int] = 0

        @override
        def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
            pass

    class Clash(ExportFilter):
        name: ClassVar[str] = "Middle"

        @override
        def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
            pass

    register_filter(Middle)
    try:
        # priority 0, inserted before the first *lower* one, so after the 0 already there
        assert [f.name for f in filters()] == ["Transform Framerate", "Shift Times", "Middle", "Fix Styles"]
        with pytest.raises(ValueError, match="already registered"):
            register_filter(Clash)
        register_filter(Middle)  # registering the same class again is not a clash
        assert [f.name for f in filters()].count("Middle") == 1
    finally:
        unregister_filter(Middle)
    assert "Middle" not in [f.name for f in filters()]


def test_from_args_reports_a_bad_option():
    with pytest.raises(AutomationError, match="unexpected keyword argument"):
        TransformFramerateFilter.from_args(parse_filter_chain("Transform Framerate=nope=1")[0])
    with pytest.raises(AutomationError, match="positional options"):
        FixStylesFilter.from_args(parse_filter_chain("Fix Styles=1")[0])
    with pytest.raises(AutomationError, match="both by position and by name"):
        TransformFramerateFilter.from_args(parse_filter_chain("Transform Framerate=25:input=24")[0])


# -- Fix Styles --------------------------------------------------------------------------------


def test_fix_styles_replaces_unknown_styles():
    ass = file_with((0, 1, "a"), (0, 1, "b"), (0, 1, "c"), styles=[AssStyle(), AssStyle(name="Sign")])
    ass.events[0].style = "Missing"
    ass.events[1].style = "sign"  # the comparison is case insensitive, as in Aegisub
    ass.events[2].style = "Default"
    FixStylesFilter().process_filter(ass, AutomationContext())
    assert [e.style for e in ass.events] == ["Default", "sign", "Default"]


# -- Transform Framerate -----------------------------------------------------------------------


def transform(spec: str, *events: tuple[int, int, str], fps: Framerate | None = None) -> AssFile:
    ass = file_with(*events)
    flt = TransformFramerateFilter.from_args(parse_filter_chain(spec)[0])
    flt.process_filter(ass, AutomationContext(fps=fps or Framerate()))
    return ass


def test_trunc_cs():
    # C division truncates toward zero, which is what the C++ does with a negative time
    assert (trunc_cs(0), trunc_cs(9), trunc_cs(1234), trunc_cs(-5), trunc_cs(-15)) == (0, 0, 1230, 0, -10)


def test_transform_framerate_retimes_lines():
    ass = transform("Transform Framerate=input=23.976:output=25", (1000, 5000, "x"))
    assert (ass.events[0].start, ass.events[0].end) == (1040, 5220)


def test_transform_framerate_is_a_no_op_between_equal_rates():
    ass = transform("Transform Framerate=input=25:output=25", (1000, 5000, "x"))
    assert (ass.events[0].start, ass.events[0].end) == (1000, 5000)


def test_transform_framerate_retimes_karaoke_and_relative_times():
    ass = transform(
        "Transform Framerate=input=23.976:output=25",
        (1000, 5000, r"{\k20}a{\k30}b{\fad(200,300)}{\move(0,0,10,10,100,400)}"),
    )
    assert ass.events[0].text == r"{\k21}a{\k31}b{\fad(211,319)}{\move(0,0,10,10,107,420)}"


def test_transform_framerate_keeps_a_nonzero_relative_time_nonzero():
    # a \fad of 1 ms must not become 0, which would mean "the end of the line"
    ass = transform("Transform Framerate=input=1000:output=1", (0, 5000, r"{\fad(1,0)}x"))
    assert r"\fad(1," in ass.events[0].text


def test_transform_framerate_reverse_swaps_the_rates():
    forward = transform("Transform Framerate=input=23.976:output=25", (1000, 5000, "x"))
    back = transform("Transform Framerate=input=25:output=23.976:reverse=1", (1000, 5000, "x"))
    assert (forward.events[0].start, forward.events[0].end) == (back.events[0].start, back.events[0].end)


def test_transform_framerate_positional_options():
    by_name = transform("Transform Framerate=input=23.976:output=25", (1000, 5000, "x"))
    by_position = transform("Transform Framerate=23.976:25", (1000, 5000, "x"))
    assert by_position.events[0].start == by_name.events[0].start


def test_transform_framerate_defaults_the_missing_rate_to_the_project():
    ass = transform("Transform Framerate=input=23.976", (1000, 5000, "x"), fps=Framerate.from_ratio(25, 1))
    assert (ass.events[0].start, ass.events[0].end) == (1040, 5220)


def test_transform_framerate_needs_at_least_one_rate():
    with pytest.raises(AutomationError, match="needs input= or output="):
        transform("Transform Framerate", (0, 1000, "x"))
    with pytest.raises(AutomationError, match="no project frame rate"):
        transform("Transform Framerate=input=25", (0, 1000, "x"))


def test_transform_framerate_reports_a_bad_rate():
    with pytest.raises(AutomationError, match="input"):
        transform("Transform Framerate=input=nonsense:output=25", (0, 1000, "x"))


def test_transform_framerate_takes_a_timecodes_file(tmp_path: Path):
    tc = tmp_path / "v2.txt"
    tc.write_text("# timecode format v2\n" + "".join(f"{i * 40}\n" for i in range(50)), encoding="utf-8")
    # a Windows path has to be quoted: ':' separates options and '\' escapes
    ass = transform(f"Transform Framerate=input='{tc}':output=25", (1000, 2000, "x"))
    assert (ass.events[0].start, ass.events[0].end) == (1000, 2000)  # that file is 25 fps


# -- Shift Times (tools/offset.py as a filter) --------------------------------------------------


def shift(spec: str, *events: tuple[int, int, str], fps: Framerate | None = None) -> AssFile:
    ass = file_with(*events)
    flt = ShiftTimesFilter.from_args(parse_filter_chain(spec)[0])
    flt.process_filter(ass, AutomationContext(fps=fps or Framerate()))
    return ass


def test_shift_times_is_registered_as_a_built_in_filter():
    assert get_filter("Shift Times") is ShiftTimesFilter
    assert is_builtin(ShiftTimesFilter)
    assert [f.name for f in filters()] == ["Transform Framerate", "Shift Times", "Fix Styles"]


def test_shift_times_by_a_timestamp():
    ass = shift("Shift Times=+0:00:01.50", (1000, 5000, "x"))
    assert (ass.events[0].start, ass.events[0].end) == (2500, 6500)
    back = shift("Shift Times=-1.0", (2500, 6500, "x"))
    assert (back.events[0].start, back.events[0].end) == (1500, 5500)


def test_shift_times_does_not_need_the_colons_escaped():
    plain = shift("Shift Times=+0:00:01.50", (1000, 5000, "x"))
    quoted = shift("Shift Times='+0:00:01.50'", (1000, 5000, "x"))
    named = shift("Shift Times=by='+0:00:01.50'", (1000, 5000, "x"))
    assert plain.events[0].start == quoted.events[0].start == named.events[0].start == 2500


def test_shift_times_by_frames_needs_a_frame_rate():
    ass = shift("Shift Times=-24", (2000, 5000, "x"), fps=Framerate.from_ratio(24, 1))
    # TimeAtFrame(i, START) is the midpoint between two frames, as `macro offset` has it
    assert (ass.events[0].start, ass.events[0].end) == (979, 3979)
    with pytest.raises(AutomationError, match="without a video, timecodes, or --fps"):
        shift("Shift Times=-24", (2000, 5000, "x"))


def test_shift_times_rejects_nonsense():
    with pytest.raises(AutomationError, match="needs the amount to shift by"):
        shift("Shift Times", (0, 1000, "x"))
    with pytest.raises(AutomationError, match="bad offset"):
        shift("Shift Times=soon", (0, 1000, "x"))


def test_shift_events_clamps():
    ass = file_with((0, 1000, "x"))
    shift_events(ass, parse_offset("-9:00:00.0"))
    assert (ass.events[0].start, ass.events[0].end) == (0, 0)


def test_convert_runs_shift_times(tmp_path: Path):
    out = tmp_path / "out.ass"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--export-filters", "Shift Times=+0:00:01.50"],
        ]
    )
    assert code == 0
    assert "Dialogue: 0,0:00:04.32,0:00:06.17," in out.read_bytes().decode("utf-8-sig")


# -- what the filter needed from ass.override ---------------------------------------------------


def test_float_to_string_matches_the_c_version():
    assert [float_to_string(v) for v in (1.0, 1.5, 100.0, -0.25, 0.0, 1.0005)] == [
        "1",
        "1.5",
        "100",
        "-0.25",
        "0",
        "1",
    ]


def test_set_and_update_text_round_trip():
    line = AssDialogue(text=r"{\k20}a{\k30}b")
    blocks = parse_dialogue_blocks(line.text)
    for block in blocks:
        block.process_parameters(lambda name, par: par.set_int(par.get_int(0) * 2) if name == "\\k" else None)
    line.update_text(blocks)
    assert line.text == r"{\k40}a{\k60}b"


def test_process_parameters_walks_into_a_transform_block():
    seen: list[tuple[str, str]] = []
    for block in parse_dialogue_blocks(r"{\t(0,100,\fscx120)}x"):
        block.process_parameters(lambda name, par: seen.append((name, par.get_str())))
    assert ("\\fscx", "120") in seen
    assert ("\\t", "\\fscx120") in seen


def test_update_text_leaves_the_line_alone_for_an_empty_block_list():
    line = AssDialogue(text="unchanged")
    line.update_text([])
    assert line.text == "unchanged"


def test_set_int_writes_an_alpha_parameter_in_its_own_form():
    blocks = parse_dialogue_blocks(r"{\1a&H80&}x")
    param = blocks[0].tags[0].params[0]
    assert param.classification is ParamClass.ALPHA
    param.set_int(0x40)
    assert blocks[0].get_text() == r"{\1a&H40&}"


def test_strict_parsing_makes_a_nested_transform_survive_update_text():
    text = r"{\t(0,248,\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080)))}x"
    line = AssDialogue(text=text)
    line.update_text(parse_dialogue_blocks(line.text))
    assert line.text != text  # Aegisub's own splitting does not round trip, as documented

    previous = set_strict_tag_parsing(True)
    try:
        line = AssDialogue(text=text)
        line.update_text(parse_dialogue_blocks(line.text))
        assert line.text == text
    finally:
        set_strict_tag_parsing(previous)


# -- resolving a name --------------------------------------------------------------------------


def resolver(settings: Settings, scan: bool = False, use_cache: bool = True) -> FilterResolver:
    paths = AegisubPaths.create(settings.aegisub_root, settings.load_appdata)
    return FilterResolver(
        settings, paths, "?user/automation/autoload|?data/automation/autoload", scan=scan, use_cache=use_cache
    )


def test_resolve_finds_a_built_in_filter(settings: Settings):
    resolved = resolver(settings).resolve(parse_filter_chain("Fix Styles,Transform Framerate=25:25"))
    assert [r.name for r in resolved] == ["Fix Styles", "Transform Framerate"]
    assert resolved[0].builtin is FixStylesFilter
    assert resolved[0].is_builtin and resolved[0].priority == -5000


def test_resolve_refuses_an_unknown_name_without_scanning(settings: Settings):
    with pytest.raises(AutomationError, match="--scan-autoload"):
        resolver(settings).resolve(parse_filter_chain("No Such Filter"))


def test_the_cache_is_dropped_when_a_script_changes(tmp_path: Path, settings: Settings):
    r = resolver(settings)
    scan = _Scan(["a-dir"], {"s.lua": [1, 2]}, {"Some Filter": ("s.lua", 7, "d")})
    r._save_cache(scan)  # pyright: ignore[reportPrivateUsage]
    assert r._load_cache(["a-dir"], {"s.lua": [1, 2]}) is not None  # pyright: ignore[reportPrivateUsage]
    # a different size or mtime for the same script, a new script, another directory: all stale
    assert r._load_cache(["a-dir"], {"s.lua": [1, 3]}) is None  # pyright: ignore[reportPrivateUsage]
    assert r._load_cache(["a-dir"], {"s.lua": [1, 2], "t.lua": [1, 1]}) is None  # pyright: ignore[reportPrivateUsage]
    assert r._load_cache(["b-dir"], {"s.lua": [1, 2]}) is None  # pyright: ignore[reportPrivateUsage]
    r.forget_cache()
    assert r._load_cache(["a-dir"], {"s.lua": [1, 2]}) is None  # pyright: ignore[reportPrivateUsage]


def test_a_cache_from_another_version_is_ignored(settings: Settings):
    r = resolver(settings)
    cache_path().parent.mkdir(parents=True, exist_ok=True)
    cache_path().write_text(json.dumps({"version": CACHE_VERSION + 1, "dirs": [], "scripts": {}}), encoding="utf-8")
    assert r._load_cache([], {}) is None  # pyright: ignore[reportPrivateUsage]
    r.forget_cache()


# -- Python filter plugins ---------------------------------------------------------------------

PLUGIN = """
from typing import ClassVar, override

from aegisub_render import AssFile
from aegisub_render.automation import ExportFilter, AutomationContext, register_filter


class DropSigns(ExportFilter):
    name: ClassVar[str] = "Drop Signs"
    description: ClassVar[str] = "Remove every line whose style starts with a prefix"
    priority: ClassVar[int] = 0
    positional: ClassVar[tuple[str, ...]] = ("prefix",)

    def __init__(self, prefix: str = "Sign") -> None:
        self.prefix = prefix

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        file.events = [e for e in file.events if not e.style.startswith(self.prefix)]


register_filter(DropSigns)
"""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Iterator[Path]:
    """A directory with one .py filter plugin in it, unregistered again afterwards."""
    (tmp_path / "drop-signs.py").write_text(PLUGIN, encoding="utf-8")
    (tmp_path / "_private.py").write_text("raise AssertionError('a _ file must not be loaded')", encoding="utf-8")
    before = set(filters())
    try:
        yield tmp_path
    finally:
        for added in set(filters()) - before:
            unregister_filter(added)


def test_python_files_skips_private_names(plugin_dir: Path):
    assert [p.name for p in python_files([str(plugin_dir)])] == ["drop-signs.py"]


def test_a_plugin_registers_its_filter_and_takes_options(plugin_dir: Path):
    registered = load_plugins([str(plugin_dir)])
    assert [f.name for f in registered] == ["Drop Signs"]
    cls = get_filter("Drop Signs")
    assert cls is not None and cls.priority == 0

    ass = file_with((0, 1, "a"), (0, 1, "b"))
    ass.events[0].style = "OP-sign"
    flt = cls.from_args(parse_filter_chain("Drop Signs=OP")[0])
    flt.process_filter(ass, AutomationContext())
    assert len(ass.events) == 1


def test_loading_a_plugin_twice_is_a_no_op(plugin_dir: Path):
    assert load_plugins([str(plugin_dir)])
    assert load_plugins([str(plugin_dir)]) == []  # already imported, nothing new registered


def test_a_broken_plugin_is_reported_and_skipped(tmp_path: Path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')", encoding="utf-8")
    seen: list[tuple[str, str]] = []
    assert load_plugins([str(tmp_path)], lambda path, error: seen.append((path.name, str(error)))) == []
    assert seen == [("broken.py", "boom")]


def test_is_builtin_separates_the_built_in_filters(plugin_dir: Path):
    load_plugins([str(plugin_dir)])
    plugin = get_filter("Drop Signs")
    assert plugin is not None and not is_builtin(plugin)
    assert is_builtin(FixStylesFilter)
    assert is_builtin(TransformFramerateFilter)


def test_a_python_file_is_an_automation_file(tmp_path: Path, settings: Settings):
    """A .py is not refused: it is an automation file that happens to hold filters."""
    paths = AegisubPaths.create(settings.aegisub_root, settings.load_appdata)
    plugin = tmp_path / "drop-signs.py"
    plugin.write_text(PLUGIN, encoding="utf-8")
    assert resolve_script(str(plugin), paths, "?user/automation/autoload") == plugin.resolve()
    # a caller that can only load Lua still says so
    with pytest.raises(RenderError, match="not one of the file types"):
        resolve_script(str(plugin), paths, "?user/automation/autoload", LUA_SUFFIXES)


def test_script_render_runs_a_filter_of_a_python_automation_file(plugin_dir: Path, settings: Settings):
    ass = file_with((0, 1000, "a"), (0, 1000, "b"))
    ass.events[0].style = "Sign-x"
    plugin = plugin_dir / "drop-signs.py"
    with ScriptRender(ass, plugin, settings, filter="Drop Signs", progress=False, quiet=True) as sr:
        assert sr.is_python
        assert [name for name, _, _ in sr.features().filters] == ["Drop Signs"]
        assert sr.features().macros == []
        sr.run()
    assert len(ass.events) == 1


MACRO_PLUGIN = """
from typing import ClassVar, override

from aegisub_render import AssFile
from aegisub_render.automation import ExportFilter, Macro, AutomationContext, register


class MyCoolTool(Macro, ExportFilter):
    name: ClassVar[str] = "My Cool Tool"
    description: ClassVar[str] = "A macro and an export filter at once"
    priority: ClassVar[int] = 50
    positional: ClassVar[tuple[str, ...]] = ("suffix",)

    def __init__(self, suffix: str = "!") -> None:
        self.suffix = suffix

    @override
    def validate_macro(self, file, selected_lines, active_line, ctx) -> bool:
        return bool(file.events)

    @override
    def process_macro(self, file, selected_lines, active_line, ctx) -> None:
        file.info.append(type(file.info[0])("Macro ran", f"{len(selected_lines)},{active_line}"))
        self.process_filter(file, ctx)

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        for line in file.events:
            line.text += self.suffix


register(MyCoolTool)
"""


@pytest.fixture
def macro_plugin(tmp_path: Path) -> Iterator[Path]:
    """A .py automation file that registers one class as both a macro and a filter."""
    path = tmp_path / "cool-tool.py"
    path.write_text(MACRO_PLUGIN, encoding="utf-8")
    before_macros, before_filters = set(macros()), set(filters())
    try:
        yield path
    finally:
        for added in set(macros()) - before_macros:
            unregister_macro(added)
        for added in set(filters()) - before_filters:
            unregister_filter(added)


def test_one_class_can_be_both(macro_plugin: Path):
    found_macros = plugin_macros(macro_plugin)
    found_filters = plugin_filters(macro_plugin)
    assert [m.name for m in found_macros] == ["My Cool Tool"]
    assert found_filters == found_macros  # the same class, in both registries
    assert get_macro("My Cool Tool") is found_macros[0]
    assert get_filter("My Cool Tool") is found_macros[0]


def test_the_filter_half_takes_options(macro_plugin: Path):
    cls = plugin_filters(macro_plugin)[0]
    ass = file_with((0, 1000, "a"))
    cls.from_args(parse_filter_chain("My Cool Tool=?")[0]).process_filter(ass, AutomationContext())
    assert ass.events[0].text == "a?"


def test_script_render_runs_the_macro_half(macro_plugin: Path, settings: Settings):
    ass = file_with((0, 1000, "a"))
    with ScriptRender(ass, macro_plugin, settings, progress=False, quiet=True) as sr:
        assert sr.macro_can_run()
        assert [name for name, _ in sr.features().macros] == ["My Cool Tool"]
        assert [name for name, _, _ in sr.features().filters] == ["My Cool Tool"]
        sr.run()
    assert ass.events[0].text == "a!"  # the macro built it with no options
    # the selection Aegisub would pass is empty for now, and the macro saw that
    assert ass.get_script_info("Macro ran") == "0,0"


def test_a_macro_that_says_it_cannot_run(macro_plugin: Path, settings: Settings):
    empty = file_with()
    with ScriptRender(empty, macro_plugin, settings, progress=False, quiet=True) as sr:
        assert not sr.macro_can_run()
        assert "cannot run on this file" in (sr.refusal_reason() or "")
        with pytest.raises(ScriptRefused):
            sr.run()


def test_a_file_with_only_filters_has_no_macro(plugin_dir: Path, settings: Settings):
    ass = file_with((0, 1000, "a"))
    with ScriptRender(ass, plugin_dir / "drop-signs.py", settings, progress=False, quiet=True) as sr:
        assert not sr.macro_can_run()
        with pytest.raises(ScriptRefused, match="registers no macros"):
            sr.run()


# -- the extra autoload directories ---------------------------------------------------------------


def test_effective_autoload_puts_the_extra_directories_around_aegisubs(tmp_path: Path):
    settings = Settings(autoload_dirs=[tmp_path / "after"], autoload_dirs_prepend=[tmp_path / "before"])
    assert effective_autoload(settings, "?user/auto|?data/auto").split("|") == [
        str(tmp_path / "before"),
        "?user/auto",
        "?data/auto",
        str(tmp_path / "after"),
    ]


def test_the_environment_adds_to_the_same_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(AUTOLOAD_DIRS_ENV, str(tmp_path / "env-after"))
    monkeypatch.setenv(AUTOLOAD_DIRS_PREPEND_ENV, os.pathsep.join([str(tmp_path / "e1"), str(tmp_path / "e2")]))
    settings = Settings(autoload_dirs=[tmp_path / "cfg-after"])
    prepend, append = extra_autoload_dirs(settings)
    assert prepend == [tmp_path / "e1", tmp_path / "e2"]
    assert append == [tmp_path / "cfg-after", tmp_path / "env-after"]


def test_a_plugin_in_an_extra_directory_needs_no_aegisub(plugin_dir: Path):
    """The whole point: the filter is found without looking for an installation at all."""
    settings = Settings(autoload_dirs=[plugin_dir])
    resolver = FilterResolver(settings)  # no paths, no env_factory: there is nothing to find
    resolved = resolver.resolve(parse_filter_chain("Drop Signs=OP"))
    assert resolved[0].is_builtin and resolved[0].name == "Drop Signs"
    assert resolved[0].args.positional == ["OP"]


def test_an_unknown_name_still_says_what_to_do(plugin_dir: Path):
    resolver = FilterResolver(Settings(autoload_dirs=[plugin_dir]))
    with pytest.raises(AutomationError, match="--autoload-dirs"):
        resolver.resolve(parse_filter_chain("No Such Filter"))


def test_convert_finds_a_plugin_through_the_command_line(tmp_path: Path, plugin_dir: Path):
    out = tmp_path / "out.ass"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--autoload-dirs", str(plugin_dir)],
            *["--export-filters", "Drop Signs=Def"],
        ]
    )
    assert code == 0
    assert "Dialogue:" not in out.read_bytes().decode("utf-8-sig")  # every line was a Default one


# -- the file's own list of filters --------------------------------------------------------------


def with_garbage(*names: str) -> AssFile:
    ass = file_with((0, 1000, "x"))
    ass.properties.export_filters = "|".join(names)
    return ass


def test_garbage_export_filter_names():
    assert garbage_export_filter_names(with_garbage("Fix Styles", "Clean Tags")) == ["Fix Styles", "Clean Tags"]
    assert garbage_export_filter_names(with_garbage()) == []
    # the names are not a filtergraph: nothing in them is escaped or split
    assert garbage_export_filter_names(with_garbage("Odd, Name=1")) == ["Odd, Name=1"]


def test_export_filter_chain_prefers_the_command_line():
    ass = with_garbage("Fix Styles")
    options = RenderOptions(export_filters=["Transform Framerate=25:25"], garbage_export_filters=None)
    assert [a.name for a in export_filter_chain(options, ass)] == ["Transform Framerate"]


@pytest.mark.parametrize(
    ("garbage", "confirm", "expected"),
    [
        (True, None, ["Fix Styles"]),
        (False, None, []),
        (None, None, []),  # undecided and nobody to ask: do not run them
        (None, True, ["Fix Styles"]),
        (None, False, []),
    ],
)
def test_export_filter_chain_from_the_file(garbage: bool | None, confirm: bool | None, expected: list[str]):
    asked: list[list[str]] = []

    def answer(names: list[str]) -> bool:
        asked.append(names)
        return bool(confirm)

    options = RenderOptions(
        garbage_export_filters=garbage,
        confirm_export_filters=answer if confirm is not None else None,
    )
    assert [a.name for a in export_filter_chain(options, with_garbage("Fix Styles"))] == expected
    assert asked == ([["Fix Styles"]] if garbage is None and confirm is not None else [])


def test_a_file_without_a_list_asks_nothing():
    asked: list[list[str]] = []
    options = RenderOptions(confirm_export_filters=lambda names: bool(asked.append(names)))
    assert export_filter_chain(options, with_garbage()) == []
    assert asked == []


def test_convert_runs_the_files_own_filters(tmp_path: Path):
    src = tmp_path / "in.ass"
    text = (CASES / "01-basic-syl.ass").read_bytes().decode("utf-8-sig")
    src.write_text(
        text.replace("[V4+ Styles]", "[Aegisub Project Garbage]\nExport Filters: Fix Styles\n\n[V4+ Styles]", 1),
        encoding="utf-8-sig",
        newline="",
    )
    out = tmp_path / "out.ass"
    assert main(["convert", str(src), "-o", str(out), "-y", "-q", "--garbage-export-filters"]) == 0
    assert out.exists()
    assert main(["convert", str(src), "-o", str(out), "-y", "-q", "--no-garbage-export-filters"]) == 0


def test_the_two_filter_options_are_exclusive(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out.ass"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--garbage-export-filters", "--export-filters", "Fix Styles"],
        ]
    )
    assert code == 1
    assert "cannot be given together" in capsys.readouterr().err


# -- the command line --------------------------------------------------------------------------


def test_convert_runs_a_built_in_filter(tmp_path: Path):
    """A chain of built-in filters needs no Aegisub installation and runs no macro."""
    out = tmp_path / "out.ass"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--export-filters", "Transform Framerate=input=23.976:output=25"],
        ]
    )
    assert code == 0
    text = out.read_bytes().decode("utf-8-sig")
    assert "Dialogue: 0,0:00:02.94,0:00:04.87," in text  # 2.82 -> 2.94 at 25/23.976
    assert "[Aegisub Project Garbage]" not in text  # a filter chain writes like File > Export


def test_convert_runs_a_chain_in_the_order_written(tmp_path: Path):
    out = tmp_path / "out.srt"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--export-filters", "Fix Styles,Transform Framerate=input=23.976:output=25"],
        ]
    )
    assert code == 0
    assert "00:00:02,940 --> " in out.read_bytes().decode("utf-8-sig")


def test_convert_reports_an_unknown_filter(tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out.ass"
    code = main(
        [
            *["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q"],
            *["--export-filters", "No Such Filter"],
        ]
    )
    assert code == 1
    assert "No Such Filter" in capsys.readouterr().err
    assert not out.exists()


def test_render_runs_the_macro_and_then_the_chain(tmp_path: Path, settings: Settings):
    """render always runs a macro (kara-templater by default); the chain comes after it."""
    out = tmp_path / "out.ass"
    code = main(
        [
            *["render", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y", "-q", "--no-progress"],
            *["--export-filters", "Fix Styles"],
        ]
    )
    assert code == 0
    text = out.read_bytes().decode("utf-8-sig")
    assert ",fx," in text  # the templates were applied
    assert "[Aegisub Project Garbage]" not in text  # and then written like File > Export
