-- aegisub-render host bootstrap.
--
-- Lua-side half of the Automation 4 host. This reproduces what Aegisub 3.5.0 does in C++
-- (src/auto4_lua.cpp, auto4_lua_assfile.cpp, auto4_lua_progresssink.cpp and
-- libaegisub/lua/{script_reader,utils,modules}.cpp); the Python side only stores data and
-- talks to the operating system. Every Python function arrives as a raw dispatcher closure
-- returning `true, results...` or `false, message`, and is wrapped below so that failures
-- become ordinary Lua errors raised from Lua code.
--
-- This chunk runs with the full standard library; everything it needs is captured in locals
-- before the global environment is sandboxed.

local py, cfg = ...

local INTERNAL = "=aegisub-render"

local _G = _G
local assert, error, type, tostring, tonumber, select, pairs, ipairs, next =
	assert, error, type, tostring, tonumber, select, pairs, ipairs, next
local rawget, rawset, rawequal, setmetatable, getmetatable =
	rawget, rawset, rawequal, setmetatable, getmetatable
local pcall, xpcall, unpack, loadstring, load, setfenv, require, newproxy, collectgarbage =
	pcall, xpcall, unpack, loadstring, load, setfenv, require, newproxy, collectgarbage
local string, table, math, debug, io, os, package = string, table, math, debug, io, os, package
local sformat, sfind, ssub, sgsub, slower, sgmatch = string.format, string.find, string.sub, string.gsub, string.lower, string.gmatch
local tconcat = table.concat
local floor, ceil, min = math.floor, math.ceil, math.min
local getinfo, traceback = debug.getinfo, debug.traceback
local ffi = require("ffi")

------------------------------------------------------------------------------ Python bridge

local function finish(ok, ...)
	if ok then return ... end
	error((...), 0)
end

local P = {}
for name, raw in pairs(py) do
	P[name] = function(...) return finish(raw(...)) end
end

------------------------------------------------------------------------------ C-style argument errors

-- agi::lua::argerror: the function name comes from how the caller called the API function.
-- `level` is the stack level of that API function as seen from the caller of argerror.
local function argerror(level, narg, extramsg)
	local ar = getinfo(level + 1, "n")
	local name = ar and ar.name or "?"
	if ar and ar.namewhat == "method" then
		narg = narg - 1
		if narg == 0 then
			error(sformat("calling '%s' on bad self (%s)", name, extramsg), 0)
		end
	end
	error(sformat("bad argument #%d to '%s' (%s)", narg, name, extramsg), 0)
end

local function typename_at(n, ...)
	if n > select("#", ...) then return "no value" end
	return type((select(n, ...)))
end

local function typerror(level, narg, tname, got)
	argerror(level + 1, narg, sformat("%s expected, got %s", tname, got))
end

-- agi::lua::check_string (lua_tolstring: strings, and numbers converted to strings)
local function check_string(level, n, ...)
	local v = select(n, ...)
	local t = type(v)
	if t == "string" then return v end
	if t == "number" then return tostring(v) end
	typerror(level + 1, n, "string", typename_at(n, ...))
end

local function trunc(x)
	if x >= 0 then return floor(x) end
	return ceil(x)
end

-- lua_tointeger: numbers and numeric strings, anything else is 0
local function tointeger(v)
	local n = tonumber(v)
	if type(v) ~= "number" and type(v) ~= "string" then n = nil end
	if not n or n ~= n then return 0 end
	return trunc(n)
end

-- lua_tointeger stored into a C int (64-bit truncation, then wrap to 32 bits)
local function toint32(v)
	local n = tonumber(v)
	if type(v) ~= "number" and type(v) ~= "string" then n = nil end
	if not n or n ~= n or n >= 2^63 or n < -2^63 then return 0 end
	n = trunc(n) % 4294967296
	if n >= 2147483648 then n = n - 4294967296 end
	return n
end

local function isnumber(v)
	return type(v) == "number" or (type(v) == "string" and tonumber(v) ~= nil)
end

-- agi::lua::check_uint on a value that is the `narg`th stack slot
local function check_uint(level, narg, v)
	local n = tointeger(v)
	if n == 0 and not isnumber(v) then
		typerror(level + 1, narg, "number", v == nil and "nil" or type(v))
	end
	if n < 0 then argerror(level + 1, narg, "must be >= 0") end
	return n
end

------------------------------------------------------------------------------ add_stack_trace

local runner_marker = {}
local moon_raw = {}

local function moon_line(lua_line, file)
	local ok, line_tables = pcall(require, "moonscript.line_tables")
	if not ok or type(line_tables) ~= "table" then return lua_line end
	local tbl = rawget(line_tables, file)
	if type(tbl) ~= "table" then return lua_line end
	local char_pos = rawget(tbl, lua_line)
	if type(char_pos) ~= "number" then return lua_line end
	local raw = moon_raw[file]
	if type(raw) ~= "string" then return lua_line end
	local _, count = sgsub(ssub(raw, 1, min(#raw, char_pos)), "\n", "")
	return count + 1
end

-- agi::lua::add_stack_trace. Frames belonging to this host (which stands in for Aegisub's
-- C++ code) are shown the way Aegisub shows its C functions.
local function add_stack_trace(msg, lvl)
	local level = 1
	if type(lvl) == "number" then level = lvl end
	if type(msg) ~= "string" and type(msg) ~= "number" then return msg end
	msg = tostring(msg)

	local nl = sfind(msg, "\n", 1, true)
	local head = nl and ssub(msg, 1, nl - 1) or msg
	local _, e = sfind(head, '^%[string ".*"%]:%d+: ')
	if e then msg = ssub(msg, e + 1) end

	local raw_frames = {}
	local l = level + 1
	while true do
		local ar = getinfo(l, "Snlf")
		if not ar then break end
		if ar.func == runner_marker.func then break end
		raw_frames[#raw_frames + 1] = ar
		l = l + 1
	end
	-- drop the C frames (xpcall/pcall) that sit right under the host runner
	while #raw_frames > 0 and raw_frames[#raw_frames].what == "C" do
		raw_frames[#raw_frames] = nil
	end

	local frames = { msg }
	local prev_internal = false
	for i = 1, #raw_frames do
		local ar = raw_frames[i]
		local internal = ar.source == INTERNAL
		local next_internal = raw_frames[i + 1] and raw_frames[i + 1].source == INTERNAL
		if internal then
			if not prev_internal then
				frames[#frames + 1] = '    File "<C function>", line -1\n?'
			end
		elseif ar.what == "C" and next_internal then
			-- the `error` builtin called by a host wrapper: part of that "C function"
		else
			local file = ar.source
			local is_moon = false
			if file == "=[C]" then
				file = "<C function>"
			elseif ssub(file, -5) == ".moon" then
				is_moon = true
			end
			local function real_line(line)
				if is_moon then return moon_line(line, file) end
				return line
			end
			local func = ar.name or ""
			if ssub(ar.what, 1, 1) == "m" then
				func = "<main>"
			elseif ssub(ar.what, 1, 1) == "C" then
				func = "?"
			elseif (ar.namewhat or "") == "" then
				func = sformat("<anonymous function at lines %d-%d>", real_line(ar.linedefined), real_line(ar.lastlinedefined - 1))
			end
			frames[#frames + 1] = sformat('    File "%s", line %d\n%s', file, real_line(ar.currentline), func)
		end
		prev_internal = internal
	end

	local out = {}
	for i = #frames, 1, -1 do out[#out + 1] = frames[i] end
	return tconcat(out, "\n")
end

local function pack(...)
	return { n = select("#", ...), ... }
end

-- Call f(...) the way Aegisub calls into Lua (lua_pcall with add_stack_trace as handler).
-- Deliberately not a tail call: add_stack_trace stops at this frame.
local function run_protected(f, ...)
	local r = pack(xpcall(f, add_stack_trace, ...))
	return unpack(r, 1, r.n)
end
runner_marker.func = run_protected

------------------------------------------------------------------------------ file loading (script_reader.cpp)

local function has_extension(path, ext)
	local e = path:match("%.([^%.\\/]*)$")
	return e ~= nil and slower(e) == ext
end

local moonscript_loadstring

-- Environment of every chunk loaded from a file (see the sandbox section below)
local file_env

-- agi::lua::LoadFile. Returns the compiled chunk, or nil and an error message.
local function load_file(path)
	local ok, content, chunkname = pcall(P.read_file, path)
	if not ok then return nil, content end
	local f, err
	if not has_extension(chunkname, "moon") then
		f, err = loadstring(content, chunkname)
	else
		moon_raw[chunkname] = content
		local ok2
		ok2, f, err = pcall(moonscript_loadstring, content, chunkname)
		if not ok2 then return nil, f end
	end
	if f == nil then return nil, err end
	if file_env then setfenv(f, file_env) end
	return f
end

local package_path = ""
for _, dir in ipairs(cfg.include_dirs) do
	package_path = package_path .. sformat("%s/?.lua;%s/?/init.lua;", dir, dir)
end
package.path = package_path
package.cpath = ""

local function split_semicolon(s)
	local out = {}
	local start = 1
	while true do
		local pos = sfind(s, ";", start, true)
		if not pos then
			out[#out + 1] = ssub(s, start)
			return out
		end
		out[#out + 1] = ssub(s, start, pos - 1)
		start = pos + 1
	end
end

-- agi::lua::module_loader
local function module_loader(name)
	local module = sgsub(name, "%.", "\\")
	for _, tok in ipairs(split_semicolon(package_path)) do
		local filename = sgsub(tok, "%?", function() return module end)
		local path = filename
		if has_extension(path, "lua") then
			local moonpath = ssub(path, 1, -4) .. "moon"
			if P.file_exists(moonpath) then path = moonpath end
		end
		if P.file_exists(path) then
			local f, err = load_file(path)
			if not f then
				error(sformat('Error loading Lua module "%s":\n%s', path, tostring(err)), 0)
			end
			return f
		end
	end
end

local loaders = package.loaders
local preload_loader = loaders[1]
for i = #loaders, 1, -1 do loaders[i] = nil end
loaders[1] = preload_loader
loaders[2] = module_loader

------------------------------------------------------------------------------ mandatory support (Install)

local function load_moonscript(path)
	local f, err = load_file(path)
	if not f then return nil, err end
	-- MoonScript is part of the host: it keeps the unsandboxed standard library.
	local env = setmetatable({ io = io, os = os, package = package, debug = debug, require = require,
		loadstring = loadstring, load = load }, { __index = _G })
	setfenv(f, env)
	local ok, mod = pcall(f)
	if not ok then return nil, mod end
	if type(mod) ~= "table" then return nil, "Bundled moonscript.lua did not return a module table" end
	if type(mod.loadstring) ~= "function" then return nil, "Bundled moonscript.lua has no loadstring function" end
	return mod
end

local moonscript, moon_err = load_moonscript(cfg.moonscript_path)
if not moonscript and cfg.moonscript_fallback then
	P.log_host(sformat("moonscript.lua from the Aegisub installation could not be loaded (%s); using the bundled MoonScript 0.7.0\n", tostring(moon_err)))
	for k in pairs(package.loaded) do
		if ssub(k, 1, 10) == "moonscript" then package.loaded[k] = nil end
	end
	for k in pairs(package.preload) do
		if ssub(k, 1, 10) == "moonscript" then package.preload[k] = nil end
	end
	moonscript, moon_err = load_moonscript(cfg.moonscript_fallback)
end
if not moonscript then
	error("Could not load moonscript.lua: " .. tostring(moon_err), 0)
end
moonscript_loadstring = moonscript.loadstring
package.loaded.moonscript = moonscript

------------------------------------------------------------------------------ ffi-backed Aegisub modules

-- agi::lua::register_lib_table: casts function pointers supplied by Python with the ffi.
local function register_lib_table(spec)
	for _, t in ipairs(spec.types) do
		ffi.cdef(sformat("typedef struct %s %s;", t, t))
	end
	local lib = {}
	for _, fn in ipairs(spec.funcs) do
		lib[fn[1]] = ffi.cast(fn[2], fn[3])
	end
	return lib
end

package.preload["aegisub.__re_impl"] = function() return register_lib_table(cfg.impls.re) end
package.preload["aegisub.__unicode_impl"] = function() return register_lib_table(cfg.impls.unicode) end
package.preload["aegisub.__lfs_impl"] = function() return register_lib_table(cfg.impls.lfs) end

local CLIPBOARD_MSG = "aegisub-render: aegisub.clipboard is unreachable (there is no clipboard in a headless renderer)"
package.preload["aegisub.clipboard"] = function()
	return {
		get = function() error(CLIPBOARD_MSG, 2) end,
		set = function() error(CLIPBOARD_MSG, 2) end,
	}
end

local luaopen_luabins = debug.getregistry()["aegisub-render:luaopen_luabins"]
local LUABINS_WARNING = "This automation script uses the library luabins. This library is deprecated and will be removed in a future version of Aegisub. If your script critically depends on luabins and cannot be adapted to work without it, please let us know at https://github.com/TypesettingTools/Aegisub/issues/382 ."
package.preload["luabins"] = function(...)
	local aegisub = rawget(_G, "aegisub")
	if type(aegisub) == "table" and aegisub.__raise_warning then
		aegisub.__raise_warning(LUABINS_WARNING)
	end
	return luaopen_luabins(...)
end

------------------------------------------------------------------------------ the sandbox

local function blocked_message(what)
	return sformat("aegisub-render: %s is blocked (sandbox)", what)
end

local function make_blocked(name)
	return setmetatable({}, {
		__index = function(_, k) error(blocked_message(name .. "." .. tostring(k)), 2) end,
		__newindex = function(_, k) error(blocked_message(name .. "." .. tostring(k)), 2) end,
		__call = function() error(blocked_message(name), 2) end,
		__tostring = function() return "blocked: " .. name end,
		__metatable = false,
	})
end

local function restricted(name, allowed)
	local t = {}
	for _, k in ipairs(allowed) do t[k] = rawget(name == "os" and os or debug, k) end
	return setmetatable(t, {
		__index = function(_, k) error(blocked_message(name .. "." .. tostring(k)), 2) end,
		__metatable = false,
	})
end

local safe_os = restricted("os", { "clock", "date", "difftime", "time" })
local safe_debug = restricted("debug", { "traceback" })

-- require for the sandbox: io/package are blocked and os/debug give the restricted tables.
-- The ffi is only handed out to code loaded from files (scripts and modules from the
-- include/autoload directories): those chunks run with `file_env`, whose `require` allows it,
-- while code compiled at run time (loadstring/load, e.g. kara-templater templates) sees the
-- global `require`, which does not. Being decided by the function environment rather than by
-- inspecting the stack, this cannot be sidestepped with tail calls.
local blocked_io, blocked_package

local function make_require(allow_ffi)
	return function(name, ...)
		if name == "ffi" and not allow_ffi then
			error(blocked_message('require "ffi" from code that was not loaded from an include or autoload directory'), 2)
		elseif name == "io" then
			return blocked_io
		elseif name == "package" then
			return blocked_package
		elseif name == "os" then
			return safe_os
		elseif name == "debug" then
			return safe_debug
		end
		return require(name, ...)
	end
end

blocked_io = make_blocked("io")
blocked_package = make_blocked("package")
local global_require = make_require(false)
file_env = setmetatable({ require = make_require(true) }, { __index = _G, __newindex = _G })

local trace_level = cfg.trace_level

local function log_at(level, text)
	if level <= trace_level then P.log(text) end
end

local function apply_sandbox()
	_G.io = blocked_io
	_G.package = blocked_package
	_G.dofile = make_blocked("dofile")
	_G.loadfile = make_blocked("loadfile")
	_G.module = make_blocked("module")
	_G.os = safe_os
	_G.debug = safe_debug
	_G.require = global_require
	_G.getmetatable = function(v)
		if type(v) == "table" then return getmetatable(v) end
		return nil
	end
	_G.loadstring = function(s, chunkname)
		return load(s, chunkname, "t")
	end
	_G.load = function(chunk, chunkname, mode, ...)
		if select("#", ...) > 0 then return load(chunk, chunkname, "t", ...) end
		return load(chunk, chunkname, "t")
	end
	string.dump = function() error(blocked_message("string.dump"), 2) end
	_G.collectgarbage = function(opt, ...)
		if opt == nil or opt == "collect" or opt == "count" then return collectgarbage(opt, ...) end
		error(blocked_message(sformat("collectgarbage(%q)", tostring(opt))), 2)
	end
	_G.print = function(...)
		local parts = {}
		for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
		log_at(4, tconcat(parts, "\t") .. "\n")
	end
end

-- include() (LuaScript::LuaInclude)
local script_dir = cfg.script_dir
local include_dirs = cfg.include_dirs

_G.include = function(...)
	local filename = check_string(1, 1, ...)
	local filepath
	if sfind(filename, "[/\\]") then
		filepath = P.path_join(script_dir, filename)
	else
		for _, dir in ipairs(include_dirs) do
			filepath = P.path_join(dir, filename)
			if P.file_exists(filepath) then break end
		end
	end
	if not filepath or not P.file_exists(filepath) then
		error(sformat("Lua include not found: %s", filename), 0)
	end
	local f, err = load_file(filepath)
	if not f then
		error(sformat('Error loading Lua include "%s":\n%s', filename, tostring(err)), 0)
	end
	return f()
end

apply_sandbox()

------------------------------------------------------------------------------ line conversion (LuaAssFile::LuaToAssEntry / AssEntryToLua)

local function bad_field(expected, name, cls)
	error(sformat("Invalid or missing field '%s' in '%s' class subtitle line (expected %s)", name, cls, expected), 0)
end

local function get_string_field(t, name, cls)
	local v = t[name]
	local tv = type(v)
	if tv == "string" then return v end
	if tv == "number" then return tostring(v) end
	bad_field("string", name, cls)
end

local function get_number_field(t, name, cls)
	local v = t[name]
	if type(v) == "number" then return v end
	if type(v) == "string" then
		local n = tonumber(v)
		if n then return n end
	end
	bad_field("string", name, cls)
end

local function get_bool_field(t, name, cls)
	local v = t[name]
	if type(v) ~= "boolean" then bad_field("boolean", name, cls) end
	return v
end

local function lua_tostring_or_default(v)
	local t = type(v)
	if t == "string" then return v end
	if t == "number" then return tostring(v) end
	return "<not a string>"
end

-- Returns a flat array describing the entry: { class, fields... }
local function to_entry(t)
	if type(t) ~= "table" then error("Can't convert a non-table value to AssEntry", 0) end
	local cls = t.class
	if type(cls) ~= "string" and type(cls) ~= "number" then
		error("Table lacks 'class' field, can't convert to AssEntry", 0)
	end
	cls = slower(tostring(cls))
	if cls == "info" then
		return { "info", get_string_field(t, "key", "info"), get_string_field(t, "value", "info") }
	elseif cls == "style" then
		local c = "style"
		return { "style",
			get_string_field(t, "name", c), get_string_field(t, "fontname", c), get_number_field(t, "fontsize", c),
			get_string_field(t, "color1", c), get_string_field(t, "color2", c),
			get_string_field(t, "color3", c), get_string_field(t, "color4", c),
			get_bool_field(t, "bold", c), get_bool_field(t, "italic", c),
			get_bool_field(t, "underline", c), get_bool_field(t, "strikeout", c),
			get_number_field(t, "scale_x", c), get_number_field(t, "scale_y", c),
			get_number_field(t, "spacing", c), get_number_field(t, "angle", c),
			get_number_field(t, "borderstyle", c), get_number_field(t, "outline", c),
			get_number_field(t, "shadow", c), get_number_field(t, "align", c),
			get_number_field(t, "margin_l", c), get_number_field(t, "margin_r", c),
			get_number_field(t, "margin_t", c), get_number_field(t, "encoding", c),
		}
	elseif cls == "dialogue" then
		local c = "dialogue"
		local e = { "dialogue",
			get_bool_field(t, "comment", c), get_number_field(t, "layer", c),
			get_number_field(t, "start_time", c), get_number_field(t, "end_time", c),
			get_string_field(t, "style", c), get_string_field(t, "actor", c),
			get_number_field(t, "margin_l", c), get_number_field(t, "margin_r", c),
			get_number_field(t, "margin_t", c),
			get_string_field(t, "effect", c), get_string_field(t, "text", c),
		}
		local extra = t.extra
		local te = type(extra)
		if te == "table" then
			local kv = {}
			for k, v in next, extra do
				if type(k) == "string" then
					kv[#kv + 1] = k
					kv[#kv + 1] = lua_tostring_or_default(v)
				end
			end
			e[13] = kv
		elseif te ~= "nil" then
			error("dialogue extradata must be a table", 0)
		else
			e[13] = false
		end
		return e
	end
	error(sformat("Found line with unknown class: %s", cls), 0)
end

local function build_line(cls, section, raw, ...)
	local t = {}
	t.section = section
	if cls == "info" then
		local key, value = ...
		t.raw = raw
		t.key = key
		t.value = value
		t.class = "info"
	elseif cls == "dialogue" then
		local comment, layer, start_time, end_time, style, actor, effect, ml, mr, mt, text, extra = ...
		t.raw = raw
		t.comment = comment
		t.layer = layer
		t.start_time = start_time
		t.end_time = end_time
		t.style = style
		t.actor = actor
		t.effect = effect
		t.margin_l = ml
		t.margin_r = mr
		t.margin_t = mt
		t.margin_b = mt
		t.text = text
		local ex = {}
		for i = 1, #extra, 2 do ex[extra[i]] = extra[i + 1] end
		t.extra = ex
		t.class = "dialogue"
	else
		local name, fontname, fontsize, c1, c2, c3, c4, bold, italic, underline, strikeout,
			sx, sy, spacing, angle, borderstyle, outline, shadow, align, ml, mr, mt, encoding = ...
		t.raw = raw
		t.name = name
		t.fontname = fontname
		t.fontsize = fontsize
		t.color1 = c1
		t.color2 = c2
		t.color3 = c3
		t.color4 = c4
		t.bold = bold
		t.italic = italic
		t.underline = underline
		t.strikeout = strikeout
		t.scale_x = sx
		t.scale_y = sy
		t.spacing = spacing
		t.angle = angle
		t.borderstyle = borderstyle
		t.outline = outline
		t.shadow = shadow
		t.align = align
		t.margin_l = ml
		t.margin_r = mr
		t.margin_t = mt
		t.margin_b = mt
		t.encoding = encoding
		t.relative_to = 2
		t.class = "style"
	end
	return t
end

------------------------------------------------------------------------------ the subtitles object (LuaAssFile)

local aegisub = {}

local function make_subs(sid, can_modify, can_set_undo)
	local state = { valid = true }
	local function check_valid()
		if not state.valid then error("Subtitles object is no longer valid", 0) end
	end
	local function check_modify()
		if not can_modify then error("Attempt to modify subtitles in read-only feature context.", 0) end
	end
	local function check_bounds(idx)
		local n = P.subs_count(sid)
		if idx <= 0 or idx > n then
			error(sformat("Requested out-of-range line from subtitle file: %d", idx), 0)
		end
	end
	local function read_line(idx)
		return build_line(P.subs_read(sid, idx))
	end

	-- `level` arguments below are relative to the user-visible function
	local function do_append(level, first_narg, ...)
		for i = 1, select("#", ...) do
			P.subs_append(sid, to_entry((select(i, ...))))
		end
	end

	local function do_insert(level, before_value, ...)
		local before = check_uint(level + 1, 1, before_value)
		local n = P.subs_count(sid)
		if not (before > 0 and before <= n + 1) then
			argerror(level + 1, 1, "Out of range line index")
		end
		if before == n + 1 then
			return do_append(level + 1, 2, ...)
		end
		local entries = {}
		for i = 1, select("#", ...) do
			entries[i] = to_entry((select(i, ...)))
		end
		P.subs_insert(sid, before, entries)
	end

	local function do_delete(level, ...)
		local itemcount = select("#", ...)
		if itemcount == 0 then return end
		local n = P.subs_count(sid)
		local ids = {}
		local first = ...
		if itemcount == 1 and type(first) == "table" then
			for _, v in next, first do
				local id = check_uint(level + 1, -1, v)
				if not (id > 0 and id <= n) then argerror(level + 1, 1, "Out of range line index") end
				ids[#ids + 1] = id
			end
		else
			-- Aegisub 3.5.0 reads the stack top (the last argument) on every iteration
			local last = select(itemcount, ...)
			while itemcount > 0 do
				local id = check_uint(level + 1, -1, last)
				if not (id > 0 and id <= n) then argerror(level + 1, itemcount, "Out of range line index") end
				ids[#ids + 1] = id
				itemcount = itemcount - 1
			end
		end
		P.subs_delete(sid, ids)
	end

	local methods = {
		delete = function(...)
			check_valid()
			check_modify()
			do_delete(1, ...)
		end,
		deleterange = function(...)
			check_valid()
			check_modify()
			local a = check_uint(1, 1, (select(1, ...)))
			local b = check_uint(1, 2, (select(2, ...)))
			P.subs_deleterange(sid, a, b)
		end,
		insert = function(...)
			check_valid()
			check_modify()
			do_insert(1, ...)
		end,
		append = function(...)
			check_valid()
			check_modify()
			do_append(1, 1, ...)
		end,
		script_resolution = function()
			check_valid()
			return P.subs_resolution(sid)
		end,
	}

	local ud = newproxy(true)
	local mt = getmetatable(ud)
	mt.__index = function(self, k)
		check_valid()
		local tk = type(k)
		if tk == "number" then
			local idx = toint32(k)
			check_bounds(idx)
			return read_line(idx)
		elseif tk == "string" then
			if k == "n" then return P.subs_count(sid) end
			local m = methods[k]
			if m then return m end
			error(sformat("Invalid indexing in Subtitle File object: '%s'", k), 0)
		end
		error(sformat("Attempt to index a Subtitle File object with value of type '%s'.", tk), 0)
	end
	mt.__newindex = function(self, k, v)
		check_valid()
		check_modify()
		local n = tointeger(k)
		if n == 0 and not isnumber(k) then
			typerror(1, 2, "number", type(k))
		end
		if n < 0 then
			do_insert(1, -n, v)
		elseif n == 0 then
			do_append(1, 1, v)
		elseif v ~= nil then
			check_bounds(n)
			P.subs_assign(sid, n, to_entry(v))
		else
			-- Aegisub 3.5.0 removes the object and the index before delegating to delete,
			-- leaving only the nil value on the stack
			do_delete(1, nil)
		end
	end
	mt.__len = function()
		check_valid()
		return P.subs_count(sid)
	end
	mt.__ipairs = function(self)
		check_valid()
		local function iter(_, i)
			check_valid()
			i = check_uint(1, 2, i)
			if i >= P.subs_count(sid) then return nil end
			return i + 1, read_line(i + 1)
		end
		return iter, nil, 0
	end

	aegisub.parse_karaoke_data = function(...)
		check_valid()
		local n = select("#", ...)
		local entry = to_entry((select(n, ...)))
		if entry[1] ~= "dialogue" then
			argerror(1, 1, "Subtitle line must be a dialogue line")
		end
		local syls = P.subs_karaoke(sid, entry)
		local result = {}
		result[0] = { duration = 0, start_time = 0, end_time = 0, tag = "", text = "", text_stripped = "" }
		for i = 1, #syls do
			local s = syls[i]
			local syl = {}
			syl.duration = s[1]
			syl.start_time = s[2]
			syl.end_time = s[3]
			syl.tag = s[4]
			syl.text = s[5]
			syl.text_stripped = s[6]
			result[i] = syl
		end
		return result
	end
	aegisub.set_undo_point = function(...)
		check_valid()
		if not can_set_undo then
			error("Attempt to set an undo point in a context where it makes no sense to do so.", 0)
		end
		if P.subs_modified(sid) then
			local msg = check_string(1, 1, ...)
			P.subs_undo_point(sid, msg)
		end
	end

	return ud, state
end

------------------------------------------------------------------------------ the aegisub table

local script_display_name = cfg.pretty_name
local macros, filters = {}, {}
local warnings = {}

aegisub.register_macro = function(...)
	local display = check_string(1, 1, ...)
	local help = select(2, ...)
	if type(help) == "number" then help = tostring(help) end
	if type(help) ~= "string" then help = "" end
	local fn_run, fn_validate, fn_active = select(3, ...)
	if type(fn_run) ~= "function" then error("The macro processing function must be a function", 0) end
	local cmd_name = sformat("automation/lua/%s/%s", cfg.script_stem, display)
	for _, m in ipairs(macros) do
		if m.cmd_name == cmd_name then
			error(sformat("A macro named '%s' is already defined in script '%s'", display, script_display_name), 0)
		end
	end
	macros[#macros + 1] = {
		cmd_name = cmd_name, name = display, help = help, run = fn_run,
		validate = type(fn_validate) == "function" and fn_validate or nil,
		isactive = type(fn_active) == "function" and fn_active or nil,
	}
end

aegisub.register_filter = function(...)
	local name = check_string(1, 1, ...)
	local description = select(2, ...)
	if type(description) == "number" then description = tostring(description) end
	if type(description) ~= "string" then description = "" end
	local priority = tointeger((select(3, ...)))
	local fn_run, fn_config = select(4, ...)
	if type(fn_run) ~= "function" then error("The filter processing function must be a function", 0) end
	filters[#filters + 1] = {
		name = name, description = description, priority = priority, run = fn_run,
		config = type(fn_config) == "function" and fn_config or nil,
	}
end

aegisub.text_extents = function(...)
	local style, text = ...
	if type(style) ~= "table" then argerror(1, 1, "") end
	if type(text) ~= "string" and type(text) ~= "number" then argerror(1, 2, "") end
	local cls = style.class
	if (type(cls) ~= "string" and type(cls) ~= "number") or slower(tostring(cls)) ~= "style" then
		error("Not a style entry", 0)
	end
	local entry = to_entry(style)
	if entry[1] ~= "style" then error("Not a style entry", 0) end
	return P.text_extents(entry, tostring(text))
end

local function last_arg(...)
	local n = select("#", ...)
	if n == 0 then return nil end
	return (select(n, ...))
end

aegisub.frame_from_ms = function(...)
	return P.frame_from_ms(tointeger(last_arg(...)))
end
aegisub.ms_from_frame = function(...)
	return P.ms_from_frame(tointeger(last_arg(...)))
end
aegisub.video_size = function() return P.video_size() end
aegisub.keyframes = function() return P.keyframes() end
aegisub.decode_path = function(...)
	return P.decode_path(check_string(1, 1, ...))
end
aegisub.cancel = function() error(nil, 0) end
aegisub.lua_automation_version = 4
aegisub.__init_clipboard = function() error(CLIPBOARD_MSG, 2) end
aegisub.__raise_warning = function(...)
	warnings[#warnings + 1] = (...)
end
aegisub.file_name = function() return P.file_name() end
aegisub.gettext = function(...)
	return check_string(1, 1, ...)
end
aegisub.project_properties = function() return P.project_properties() end
aegisub.get_audio_selection = function()
	error("aegisub-render: aegisub.get_audio_selection() is not implemented (no audio in a headless renderer)", 2)
end
aegisub.set_status_text = function(...)
	P.status(check_string(1, 1, ...))
end

_G.aegisub = aegisub

------------------------------------------------------------------------------ progress sink (LuaProgressSink)

local function debug_out(...)
	local n = select("#", ...)
	local first = ...
	local start = 1
	if type(first) == "number" then
		if trunc(first) > trace_level then return end
		start = 2
	end
	local msg
	if n - start + 1 > 1 then
		local fmt = _G.string.format
		local ok, res = pcall(fmt, select(start, ...))
		if not ok then error(res, 0) end
		msg = res
	elseif n >= start then
		msg = (select(start, ...))
	end
	local tm = type(msg)
	if tm == "number" then
		msg = tostring(msg)
	elseif tm ~= "string" then
		typerror(1, 1, "string", n >= start and tm or "no value")
	end
	P.log(msg)
end

local function not_implemented(name)
	return function()
		error(sformat("aegisub-render: aegisub.dialog.%s() is not implemented (no user interface in a headless renderer)", name), 2)
	end
end

local function install_progress_sink(allow_dialog)
	aegisub.progress = {
		set = function(...) P.progress_set(tonumber((...)) or 0) end,
		task = function(...) P.progress_task(check_string(1, 1, ...)) end,
		title = function(...) P.progress_title(check_string(1, 1, ...)) end,
		is_cancelled = function() return false end,
	}
	aegisub.debug = { out = debug_out }
	aegisub.log = debug_out
	if allow_dialog then
		aegisub.dialog = {
			display = not_implemented("display"),
			open = not_implemented("open"),
			save = not_implemented("save"),
		}
	end
end

local function remove_progress_sink()
	aegisub.progress = nil
	aegisub.debug = nil
	P.progress_done()
end

------------------------------------------------------------------------------ host entry points (called from Python)

local host = {}

-- LuaScript::Create, after the environment has been prepared
function host.load_script(path)
	local f, err = load_file(path)
	if not f then return false, tostring(err) end
	local ok, run_err = run_protected(f)
	if not ok then
		return false, sformat('Error initialising Lua script "%s":\n\n%s', script_display_name, tostring(run_err))
	end
	local version = rawget(_G, "version")
	if type(version) == "number" and trunc(version) == 3 then
		return false, "Attempted to load an Automation 3 script as an Automation 4 Lua script. Automation 3 is no longer supported."
	end
	local function global_string(name)
		local v = rawget(_G, name)
		if type(v) == "string" then return v end
		if type(v) == "number" then return tostring(v) end
		return ""
	end
	local info = {
		name = global_string("script_name"),
		description = global_string("script_description"),
		author = global_string("script_author"),
		version = global_string("script_version"),
		warnings = warnings,
	}
	aegisub.__raise_warning = function(...)
		local msg = check_string(1, -1, ...)
		P.log_host(sformat("Warning in Automation script '%s':\n%s\n", cfg.script_stem, msg))
	end
	return true, info
end

function host.features()
	local out = { macros = {}, filters = {} }
	for i, m in ipairs(macros) do
		out.macros[i] = { name = m.name, help = m.help, validate = m.validate ~= nil, isactive = m.isactive ~= nil }
	end
	for i, f in ipairs(filters) do
		out.filters[i] = { name = f.name, description = f.description, priority = f.priority, config = f.config ~= nil }
	end
	return out
end

local function finish_run(ok, err, ...)
	collectgarbage("collect")
	remove_progress_sink()
	if ok then return "ok", ... end
	if err ~= nil then
		if type(err) ~= "string" and type(err) ~= "number" then err = "<not a string>" end
		err = tostring(err)
		P.log("\n\nLua reported a runtime error:\n")
		P.log(err)
		return "error", err
	end
	return "cancel"
end

-- LuaCommand::Validate
function host.validate_macro(index, sid, selected, active)
	local m = macros[index]
	if not m.validate then return true end
	local subs, state = make_subs(sid, false, false)
	local ok, result, help = run_protected(m.validate, subs, selected, active)
	state.valid = false
	if not ok then
		P.log_host(sformat("Runtime error in Lua macro validation function:\n%s\n", tostring(result)))
		return false
	end
	return result and true or false, type(help) == "string" and help or nil
end

-- LuaCommand::operator()
function host.run_macro(index, sid, selected, active)
	local m = macros[index]
	local subs, state = make_subs(sid, true, true)
	install_progress_sink(true)
	local status, a, b = finish_run(run_protected(m.run, subs, selected, active))
	state.valid = false
	return status, a, b
end

-- LuaExportFilter::ProcessSubs
function host.run_filter(index, sid)
	local f = filters[index]
	local subs, state = make_subs(sid, true, false)
	install_progress_sink(false)
	local status, a = finish_run(run_protected(f.run, subs, {}))
	state.valid = false
	return status, a
end

return add_stack_trace, host.load_script, host.features, host.validate_macro, host.run_macro, host.run_filter
