# Builds aegisub_lua.dll: the same Lua runtime Aegisub 3.5.0 links into aegisub.exe.
#
# Mirrors Aegisub v3.5.0 (meson.build, libaegisub/meson.build, subprojects/):
#   - LuaJIT from subprojects/luajit.wrap (copied verbatim, same source/patch sha256),
#     with the exact options Aegisub sets: static, amalgam, lua52compat, no luajit binary
#   - luabins from subprojects/luabins (copied verbatim)
#   - lpeg 0.10 from libaegisub/lua/modules/lpeg.c (copied verbatim)
#   - Windows: MSVC x64, buildtype=release, dynamic CRT (Aegisub's CI "Windows MSVC Release")
#
# The only addition is src/shim.c (memory-limited allocator, panic handler, preload of
# lpeg/luabins), and the whole thing is linked into one DLL so Python can load it with ctypes.
project('aegisub_lua', ['c'],
        license: 'BSD-3-Clause',
        meson_version: '>=1.3.0',
        default_options: [
            'warning_level=3',
            'buildtype=release',
            'b_ndebug=if-release',

            'luajit:default_library=static',
            'luajit:amalgam=true',
            'luajit:lua52compat=true',
            'luajit:luajit=false',
        ],
        version: '3.5.0')

subproject('luajit')
luajit = dependency('luajit')
assert(luajit.type_name() == 'internal', 'System luajit used instead of built-in luajit')

luabins_dep = subproject('luabins').get_variable('luabins_dep')

shared_library('aegisub_lua',
    'src/shim.c',
    'src/lpeg.c',
    dependencies: [luajit, luabins_dep],
    vs_module_defs: 'aegisub_lua.def',
    name_prefix: '',
    install: false,
)
