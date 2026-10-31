"""The Lua host: environment, sandbox, aegisub API and the subtitles object."""

import json
import textwrap
from pathlib import Path

from aegisub_render.runner import RenderOptions
from aegisub_render.settings import Settings
from aegisub_render.video.context import VideoOptions

from .conftest import RunLua

# what native/sync_upstream.py expects the built LuaJIT to call itself
LOCK = json.loads((Path(__file__).resolve().parent.parent / "native" / "upstream.json").read_text(encoding="utf-8"))


def macro(body: str) -> str:
    return textwrap.dedent(
        """
        script_name = "test"
        local function check(cond, msg) if not cond then error("CHECK FAILED: " .. msg, 2) end end
        aegisub.register_macro("test", "", function(subs, sel, active)
        %s
        end)
        """
    ) % textwrap.indent(textwrap.dedent(body), "    ")


def test_luajit_runtime(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            check(jit.version == "@LUAJIT_VERSION@", jit.version)
            check(tostring(1.0) == "1", "tostring(1.0)")
            check(tostring(10000000000000000) == "1e+16", "big numbers")
            check(string.format("%d", 1.5) == "1", "format %d truncates")
            check(table.pack ~= nil and table.unpack ~= nil, "lua52compat")
            check(#setmetatable({}, {__len = function() return 3 end}) == 3, "__len on tables")
            check(type(loadstring) == "function" and type(setfenv) == "function", "5.1 functions")
            check(bit.band(6, 3) == 2, "bit")
            math.randomseed(42)
            local a = math.random(1, 1000)
            math.randomseed(42)
            check(a == math.random(1, 1000), "seeded random is repeatable")
            aegisub.debug.out(0, "random=%d\\n", a)
            """.replace("@LUAJIT_VERSION@", LOCK["luajit"]["version"])
        )
    )
    assert r.result.status == "ok", r.log


def test_sandbox_blocks(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            local function blocked(f, what)
                local ok, err = pcall(f)
                check(not ok, what .. " should fail")
                check(tostring(err):find("is blocked %(sandbox%)"), what .. ": " .. tostring(err))
            end
            blocked(function() return io.open end, "io")
            blocked(function() return package.path end, "package")
            blocked(function() dofile("x") end, "dofile")
            blocked(function() loadfile("x") end, "loadfile")
            blocked(function() module("x") end, "module")
            blocked(function() return os.getenv("PATH") end, "os.getenv")
            blocked(function() return os.execute end, "os.execute")
            blocked(function() return debug.getinfo end, "debug.getinfo")
            blocked(function() return string.dump(print) end, "string.dump")
            blocked(function() return ("x"):dump() end, "string method dump")
            blocked(function() return collectgarbage("stop") end, "collectgarbage")
            blocked(function() return require("io").open end, "require io")
            check(type(os.time()) == "number" and type(os.clock()) == "number", "os.time/clock")
            check(type(os.date("%Y")) == "string" and os.difftime(2, 1) == 1, "os.date/difftime")
            check(type(debug.traceback("x")) == "string", "debug.traceback")
            check(getmetatable("") == nil, "getmetatable on strings")
            check(getmetatable(setmetatable({}, {x = 1})).x == 1, "getmetatable on tables")
            check(collectgarbage("count") > 0, "collectgarbage count")
            local f, err = loadstring(string.dump(function() end) or "")
            local chunk, berr = load("\\027LJ\\002", "bin", "b")
            check(chunk == nil and tostring(berr):find("wrong mode"), "binary chunks: " .. tostring(berr))
            check(loadstring("return 1 + 1")() == 2, "text chunks load")
            """
        )
    )
    assert r.result.status == "error"  # string.dump is blocked, so the unused line above raises
    r = run_lua(
        macro(
            """
            local chunk, berr = load("\\027LJ\\002", "bin", "b")
            check(chunk == nil and tostring(berr):find("wrong mode"), "binary chunks: " .. tostring(berr))
            check(loadstring("return 1 + 1")() == 2, "text chunks load")
            check(require("os") == os, "require os gives the restricted table")
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_ffi_gate(run_lua: RunLua, tmp_path: Path) -> None:
    (tmp_path / "uses_ffi.lua").write_text(
        'local ffi = require "ffi"\nreturn { os = ffi.os, sizeof = ffi.sizeof("int") }\n', encoding="utf-8"
    )
    r = run_lua(
        macro(
            """
            -- trusted: a file loaded through the module loader (the script directory is an include path)
            local m = require("uses_ffi")
            check(m.os == jit.os and m.sizeof == 4, "ffi from an include file")
            -- untrusted: code compiled at run time (like kara-templater template code)
            local f = loadstring('return require("ffi")', "template code")
            local ok, err = pcall(f)
            check(not ok and tostring(err):find("blocked"), "ffi from template code: " .. tostring(err))
            -- the script file itself is trusted
            check(require("ffi").os == jit.os, "ffi from the script")
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_aegisub_modules(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            local re = require "aegisub.re"
            local m = re.match("abc123def", "([a-z]+)([0-9]+)")
            check(m and m[2].str == "abc" and m[3].str == "123" and m[3].first == 4, "re.match")
            check(re.sub("a-b-c", "-", "+") == "a+b+c", "re.sub")
            check(re.sub("hello world", "(\\\\w+)", "<$1>") == "<hello> <world>", "re.sub groups")
            local parts = re.split("a,b,,c", ",")
            check(#parts == 4 and parts[3] == "", "re.split")
            check(re.find("日本語テキスト", "語.") [1].first == 7, "re byte offsets")
            local ok, err = pcall(re.compile, "(")
            check(not ok, "bad pattern errors")
            check(re.match("ABC", "abc", re.ICASE) ~= nil, "flags")
            local unicode = require "aegisub.unicode"
            check(unicode.len("日本語") == 3, "unicode.len")
            check(unicode.to_upper_case("straße") == "STRASSE", "to_upper_case")
            check(unicode.to_lower_case("ÀÉ") == "àé", "to_lower_case")
            local util = require "aegisub.util"
            check(util.ass_color(255, 0, 0) == "&H0000FF&", "util.ass_color")
            local lfs = require "aegisub.lfs"
            check(type(lfs.currentdir()) == "string", "lfs.currentdir")
            check(lfs.attributes(aegisub.decode_path("?data"), "mode") == "directory", "lfs.attributes")
            local clip = require "aegisub.clipboard"
            local ok2, cerr = pcall(clip.get)
            check(not ok2 and tostring(cerr):find("unreachable"), "clipboard is unreachable")
            include("utils.lua")
            check(type(table.copy) == "function", "include utils.lua")
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_new_in_3_5_1(run_lua: RunLua, tmp_path: Path) -> None:
    """aegisub.version, and include() running the file under lua_pcall."""
    for name, code in {
        "inc_values.lua": "return 1, nil, 3\n",
        "inc_error.lua": 'error("boom")\n',
        "inc_table.lua": "error({})\n",
        "inc_syntax.lua": "return (\n",
    }.items():
        (tmp_path / name).write_text(code, encoding="utf-8")
    r = run_lua(
        macro(
            """
            local v = aegisub.version
            check(v.string == "3.5.1" and v.major == 3 and v.minor == 5 and v.patch == 1 and v.build == 9914,
                "aegisub.version")
            check(select("#", include("inc_values.lua")) == 3 and select(3, include("inc_values.lua")) == 3,
                "include returns all values")
            local function err_of(name)
                local ok, err = pcall(function() include(name) end)
                check(not ok, name)
                return err
            end
            local e = err_of("inc_error.lua")
            check(e:find('^[^\\n]*:%d+: Error running Lua include "inc_error.lua":\\n') and e:find(":1: boom$"), e)
            e = err_of("inc_table.lua")
            check(e:find(":%d+: bad argument #%-1 to 'include' %(string expected, got table%)$"), e)
            e = err_of("inc_syntax.lua")
            check(e:find('Error loading Lua include "inc_syntax.lua":\\n', 1, true), e)
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_subtitles_object(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            check(#subs == subs.n, "#subs")
            local w, h = subs.script_resolution()
            check(w == 1280 and h == 720, "script_resolution")
            local first_dialogue
            for i = 1, #subs do
                if subs[i].class == "dialogue" then first_dialogue = i break end
            end
            check(sel[1] == first_dialogue and active == first_dialogue, "default selection")
            local l = subs[first_dialogue]
            check(l.start_time == 1000 and l.end_time == 3000 and l.margin_b == l.margin_t, "dialogue fields")
            check(l.raw == "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\\\k20}first {\\\\k30}line", l.raw)
            local kara = aegisub.parse_karaoke_data(l)
            check(kara[0].text == "" and #kara == 2 and kara[1].text_stripped == "first ", "parse_karaoke_data")
            check(kara[2].start_time == 200 and kara[2].duration == 300 and kara[2].tag == "\\\\k", "karaoke times")
            l.start_time = 1234
            l.text = "edited"
            l.extra = { mykey = "my,value" }
            subs[first_dialogue] = l
            check(subs[first_dialogue].start_time == 1230, "times round to centiseconds when read back")
            check(subs[first_dialogue].extra.mykey == "my,value", "extradata round trip")
            local new = subs[first_dialogue]
            new.text = "appended"
            subs.append(new)
            subs[0] = new
            subs[-first_dialogue] = new
            local ok, err = pcall(function() return subs[999] end)
            -- agi::lua::error prefixes the position of the Lua code that called the C function
            local here = '^%[string "[^"]*"%]:%d+: '
            check(not ok and err:find(here .. "Requested out%-of%-range line from subtitle file: 999$"), tostring(err))
            ok, err = pcall(function() return subs.nope end)
            check(not ok and err:find(here .. "Invalid indexing in Subtitle File object: 'nope'$"), tostring(err))
            ok, err = pcall(subs.append, { class = "bogus" })
            check(not ok and err == "Found line with unknown class: bogus", tostring(err))
            ok, err = pcall(subs.append, { class = "dialogue" })
            check(not ok and err == "Invalid or missing field 'comment' in 'dialogue' class subtitle line (expected boolean)", tostring(err))
            ok, err = pcall(subs.insert, 0, new)
            check(not ok and err == "bad argument #1 to '?' (Out of range line index)", tostring(err))
            ok, err = pcall(function() subs.insert(0, new) end)
            check(not ok and err:find(here .. "bad argument #1 to 'insert' %(Out of range line index%)$"), tostring(err))
            subs.delete(#subs)
            subs.deleterange(#subs, #subs)
            aegisub.set_undo_point("x")
            local info = subs[1]
            check(info.class == "info" and info.key == "Title", "info lines")
            info.value = "renamed"
            subs[1] = info
            """
        )
    )
    assert r.result.status == "ok", r.log
    text = r.text()
    assert "Title: renamed" in text
    assert "Dialogue: 0,0:00:01.23,0:00:03.00,Default,,0,0,0,,{=1}edited" in text
    assert "[Aegisub Extradata]\r\nData: 1,mykey,emy#2Cvalue\r\n" in text
    assert text.count("}appended") == 1  # two of the three copies were deleted again


def test_runtime_error_and_cancel(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            local function inner() error("boom") end
            inner()
            """
        )
    )
    assert r.result.status == "error"
    assert "Lua reported a runtime error:" in r.log
    assert "boom" in r.log
    assert 'File "' in r.log and "test-script.lua" in r.log

    r = run_lua(macro("aegisub.debug.out('about to cancel\\n'); aegisub.cancel()"))
    assert r.result.status == "cancel"
    assert "about to cancel" in r.log
    assert "runtime error" not in r.log


def test_debug_out_levels(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            aegisub.debug.out(3, "shown %d\\n", 3)
            aegisub.debug.out(4, "hidden\\n")
            aegisub.log("plain %s\\n", "log")
            print("print goes to level 4")
            """
        ),
        options=RenderOptions(trace_level=3),
    )
    assert r.result.status == "ok", r.log
    assert "shown 3" in r.log and "plain log" in r.log
    assert "hidden" not in r.log and "print goes" not in r.log


def test_video_api_without_video(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            check(aegisub.video_size() == nil, "video_size")
            check(aegisub.frame_from_ms(1000) == nil, "frame_from_ms")
            check(aegisub.ms_from_frame(10) == nil, "ms_from_frame")
            check(type(aegisub.keyframes()) == "table" and #aegisub.keyframes() == 0, "keyframes")
            local ok, err = pcall(aegisub.get_audio_selection)
            check(not ok and tostring(err):find("not implemented"), "get_audio_selection")
            check(aegisub.file_name() == "input.ass", "file_name")
            check(aegisub.decode_path("?script"):find("input") == nil, "decode ?script")
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_video_api_with_dummy_video(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            local w, h, ar, art = aegisub.video_size()
            check(w == 640 and h == 480 and art == 0, "video_size " .. tostring(w))
            check(math.abs(ar - 4 / 3) < 1e-9, "aspect ratio")
            check(aegisub.frame_from_ms(1000) == 24, "frame_from_ms " .. tostring(aegisub.frame_from_ms(1000)))
            check(aegisub.ms_from_frame(24) == 980, "ms_from_frame " .. tostring(aegisub.ms_from_frame(24)))
            """
        ),
        options=RenderOptions(video=VideoOptions(video="?dummy:23.976000:1000:640:480:47:163:254:")),
    )
    assert r.result.status == "ok", r.log


def test_text_extents(run_lua: RunLua) -> None:
    r = run_lua(
        macro(
            """
            local style
            for i = 1, #subs do if subs[i].class == "style" then style = subs[i] break end end
            local w, h, d, e = aegisub.text_extents(style, "Hello")
            check(w > 50 and w < 150 and h > 30 and h < 60, ("extents %g %g"):format(w, h))
            local w0, h0 = aegisub.text_extents(style, "")
            -- GDI measures nothing for empty text; a Pango layout (Aegisub elsewhere) is one line high
            check(w0 == 0 and h0 == (jit.os == "Windows" and 0 or style.fontsize), "empty text")
            style.scale_x = 200
            check(math.abs(aegisub.text_extents(style, "Hello") - 2 * w) < 1e-9, "scale_x")
            """
        )
    )
    assert r.result.status == "ok", r.log


def test_limits(run_lua: RunLua, settings: Settings) -> None:
    original = settings.max_output_lines
    settings.max_output_lines = 5
    try:
        r = run_lua(macro("for i = 1, 10 do subs.append(subs[#subs]) end"))
    finally:
        settings.max_output_lines = original
    assert r.result.status == "error"
    assert "output line limit exceeded" in r.log

    original_mem = settings.memory_limit_bytes
    settings.memory_limit_bytes = 20_000_000
    try:
        r = run_lua(macro("local t = {} for i = 1, 1e7 do t[i] = ('x'):rep(100) .. i end"))
    finally:
        settings.memory_limit_bytes = original_mem
    assert r.result.status == "error"
    assert "memory" in r.log


def test_dependency_control_is_sealed(run_lua: RunLua, tmp_path: Path) -> None:
    # fake DepCtrl files in the script directory (the first include directory)
    (tmp_path / "l0" / "DependencyControl").mkdir(parents=True)
    (tmp_path / "l0" / "DependencyControl.lua").write_text("return { fake = true }\n", encoding="utf-8")
    (tmp_path / "l0" / "DependencyControl" / "Constants.lua").write_text("return {}\n", encoding="utf-8")
    (tmp_path / "l0" / "Other.lua").write_text("return { ok = true }\n", encoding="utf-8")
    r = run_lua(
        macro(
            """
            local function missing(name)
                local ok, err = pcall(require, name)
                check(not ok, name .. " must not load")
                return err
            end
            local real = missing("no.such.module")
            local sealed = missing("l0.DependencyControl")
            check(sealed == real:gsub("no%.such%.module", "l0.DependencyControl"), sealed)
            missing("l0.DependencyControl.Constants")
            missing("L0.DEPENDENCYCONTROL")
            missing("l0/DependencyControl")
            local ok, err = pcall(include, "l0/DependencyControl.lua")
            check(not ok and tostring(err):find("Lua include not found: l0/DependencyControl.lua", 1, true), tostring(err))
            check(require("l0.Other").ok, "other modules still load")
            local have_depctrl = pcall(require, "l0.DependencyControl")
            check(not have_depctrl, "the usual fallback pattern sees no DepCtrl")
            aegisub.debug.out(0, "%s\\n", sealed)
            """
        )
    )
    assert r.result.status == "ok", r.log
    assert r.log == "module 'l0.DependencyControl' not found:\n\tno field package.preload['l0.DependencyControl']\n"
