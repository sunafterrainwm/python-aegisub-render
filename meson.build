# Builds the two extension modules of aegisub-render (run native/build.py, which also checks the
# vendored sources and copies the results into the package):
#
#   aegisub_render/lua/_luajit.pyd  - LuaJIT as Aegisub 3.5.0 builds it, plus lpeg, luabins and the
#                                     host shim, with the Cython binding (_luajit.pyx/.pxd)
#   aegisub_render/host/_gdi.pyd    - GDI text measurement for aegisub.text_extents (_gdi.pyx/.pxd)
#
# The Lua runtime mirrors Aegisub v3.5.0 (meson.build, libaegisub/meson.build, subprojects/):
#   - LuaJIT from native/subprojects/luajit.wrap (copied verbatim, same source/patch sha256),
#     with the exact options Aegisub sets: static, amalgam, lua52compat, no luajit binary
#   - luabins from native/subprojects/luabins (copied verbatim)
#   - lpeg 0.10 from libaegisub/lua/modules/lpeg.c (native/src/lpeg.c, copied verbatim)
#   - MSVC x64, buildtype=release, dynamic CRT (Aegisub's CI "Windows MSVC Release")
project('aegisub_render', ['c', 'cython'],
        license: 'BSD-3-Clause',
        meson_version: '>=1.3.0',
        subproject_dir: 'native/subprojects',
        default_options: [
            'warning_level=3',
            'buildtype=release',
            'b_ndebug=if-release',
            'cython_language=c',

            'luajit:default_library=static',
            'luajit:amalgam=true',
            'luajit:lua52compat=true',
            'luajit:luajit=false',
        ],
        version: '3.5.0')

py = import('python').find_installation(pure: false)
cc = meson.get_compiler('c')

subproject('luajit')
luajit = dependency('luajit')
assert(luajit.type_name() == 'internal', 'System luajit used instead of built-in luajit')

luabins_dep = subproject('luabins').get_variable('luabins_dep')

py.extension_module('_luajit',
    'aegisub_render/lua/_luajit.pyx',
    'native/src/shim.c',
    'native/src/lpeg.c',
    include_directories: include_directories('native/src'),
    dependencies: [luajit, luabins_dep],
    # Cython-generated C trips many warnings that say nothing about this code
    override_options: ['warning_level=1'],
)

py.extension_module('_gdi',
    'aegisub_render/host/_gdi.pyx',
    dependencies: [cc.find_library('gdi32')],
    override_options: ['warning_level=1'],
)
