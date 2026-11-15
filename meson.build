# Builds the extension modules of aegisub-render (run native/build.py, which also checks the
# vendored sources, copies the results into the package and generates their .pyi stubs):
#
#   src/aegisub_render/lua/_luajit.pyd  - LuaJIT as the Aegisub tree in native/upstream.json builds it,
#                                     plus lpeg, luabins and the host shim, with the Cython binding
#   src/aegisub_render/host/_font.pyd   - text measurement for aegisub.text_extents, one module built from
#                                     _font.gdi.pyx (GDI) in the Windows branch or _font.fcft.pyx
#                                     (fontconfig + HarfBuzz) in the Linux branch
#   src/aegisub_render/_crt.pyd         - C runtime behaviour (atoi, strtol, printf %g/%f, C casts) of the
#                                     platform's C library (the UCRT on Windows, glibc on Linux)
#
# The Lua runtime mirrors an Aegisub source tree (Aegisub v3.5.1 by default), taken by
# native/sync_upstream.py and recorded in native/upstream.json:
#   - LuaJIT from native/subprojects/luajit.wrap (copied verbatim, or with a branch revision
#     pinned), with the options that tree passes to it (given to meson setup by native/build.py)
#   - luabins sources in native/subprojects/luabins (copied verbatim)
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
            # the options of the luajit subproject are passed by native/build.py (native/upstream.json)
        ],
        version: '3.5.1')

py = import('python').find_installation(pure: false)
cc = meson.get_compiler('c')

# The platform branch: the host's, unless meson.options' `branch` forces one (native/build.py
# --msys2 builds the Linux branch on Windows with an MSYS2 toolchain).
branch = get_option('branch')
if branch == 'auto'
    branch = host_machine.system() == 'windows' ? 'windows' : 'linux'
endif

if branch == 'linux'
    # Aegisub's meson.build: LuaJIT unwinds through every C frame Lua can call into, which needs
    # unwind tables (the dispatcher in _luajit raises Lua errors through Cython frames)
    add_global_arguments(cc.get_supported_arguments(['-fexceptions']), language: ['c'])
endif

subproject('luajit')
luajit = dependency('luajit')
assert(luajit.type_name() == 'internal', 'System luajit used instead of built-in luajit')

luabins_dep = subproject('luabins').get_variable('luabins_dep')

py.extension_module('_luajit',
    'src/aegisub_render/lua/_luajit.pyx',
    'native/src/shim.c',
    'native/src/lpeg.c',
    include_directories: include_directories('native/src'),
    dependencies: [luajit, luabins_dep],
    # Cython-generated C trips many warnings that say nothing about this code
    override_options: ['warning_level=1'],
)

py.extension_module('_crt',
    'src/aegisub_render/_crt.pyx',
    override_options: ['warning_level=1'],
)

# One module, aegisub_render.host._font, from either source (the same functions in both)
if branch == 'windows'
    font_source = 'src/aegisub_render/host/_font.gdi.pyx'
    font_deps = [cc.find_library('gdi32')]
else
    # the non-WIN32 text_extents: fontconfig picks the font (as libass does), HarfBuzz measures it
    font_source = 'src/aegisub_render/host/_font.fcft.pyx'
    font_deps = [dependency('fontconfig'), dependency('harfbuzz')]
endif
py.extension_module('_font',
    font_source,
    dependencies: font_deps,
    cython_args: ['--module-name', 'aegisub_render.host._font'],
    override_options: ['warning_level=1'],
)
