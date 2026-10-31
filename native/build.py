"""Build the extension modules described in ../meson.build, copy them into the package and
regenerate their .pyi stubs (native/stubgen.py).

Usage (from the repository root):

    uv run --group build python native/build.py
    uv run --group build python native/build.py --msys2 C:\\msys64\\ucrt64   # Windows: the Linux branch

--msys2 builds the Linux branch (meson option branch=linux: _font from _font.fcft.pyx, with
fontconfig and HarfBuzz) on Windows with the C compiler, pkg-config, fontconfig and HarfBuzz of an
MSYS2 environment (ucrt64, mingw64, clang64 or clangarm64: its prefix, or the MSYS2 root for its
ucrt64), still for this Python; a missing package is reported with that environment's package name. Its build
directory is native/builddir-msys2-<environment>. The modules it copies into the package replace
the committed MSVC ones (a plain build, or git checkout, puts those back), and need the
environment's DLLs: their directory is written to aegisub_render/_dll_dirs.txt (not committed),
which aegisub_render/__init__.py passes to os.add_dll_directory. A plain build removes that file.

Requirements: on Windows, Visual Studio 2022 (or Build Tools) with the C++ x64 toolchain;
elsewhere, a C compiler and the fontconfig and HarfBuzz development files (Debian:
libfontconfig-dev libharfbuzz-dev). Cython, meson and ninja come from the `build` dependency
group. Meson runs under this Python, so the modules are built for it (CPython 3.14). The
Windows modules (.pyd) are committed; the others (.so) are built where they are used, in a
build directory of their own (native/builddir-<platform>).

Every vendored input is checked against the sha256 recorded in native/upstream.json (written
by native/sync_upstream.py, which also records the Aegisub tree they came from) before
building. A [wrap-file] LuaJIT is verified by meson itself through the hashes in
subprojects/luajit.wrap; a [wrap-git] one is pinned to a commit. The meson options for the
LuaJIT subproject come from upstream.json too, and a change of upstream.json starts the build
directory afresh.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

NATIVE = Path(__file__).resolve().parent
ROOT = NATIVE.parent
WINDOWS = sys.platform == "win32"
BUILD_DIR = NATIVE / ("builddir" if WINDOWS else f"builddir-{sys.platform}")
SUFFIX = ".pyd" if WINDOWS else ".so"
DLL_DIRS = ROOT / "aegisub_render" / "_dll_dirs.txt"
# extension module name -> package directory it is imported from
MODULES: dict[str, Path] = {
    "_luajit": ROOT / "aegisub_render" / "lua",
    "_font": ROOT / "aegisub_render" / "host",
    "_crt": ROOT / "aegisub_render",
}

LOCK = NATIVE / "upstream.json"


def load_lock() -> dict[str, object]:
    data: object = json.loads(LOCK.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{LOCK} is not a JSON object")
    return cast(dict[str, object], data)


def vendored(lock: dict[str, object]) -> dict[str, str]:
    """native/-relative path -> sha256 of the vendored build inputs."""
    files = lock.get("files")
    if not isinstance(files, dict):
        raise SystemExit(f"{LOCK} has no files")
    out: dict[str, str] = {}
    for path, info in cast(dict[str, object], files).items():
        digest = cast(dict[str, object], info).get("sha256") if isinstance(info, dict) else None
        if path.startswith("native/") and isinstance(digest, str):
            out[path.removeprefix("native/")] = digest
    return out


def luajit_options(lock: dict[str, object]) -> list[str]:
    options = lock.get("luajit_meson_options")
    if not isinstance(options, list):
        return []
    return [f"-Dluajit:{o}" for o in cast(list[object], options) if isinstance(o, str)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_vendored(lock: dict[str, object]) -> None:
    bad = [
        name
        for name, digest in vendored(lock).items()
        if not (NATIVE / name).is_file() or sha256(NATIVE / name) != digest
    ]
    if bad:
        raise SystemExit(
            f"vendored files differ from {LOCK.name} (run native/sync_upstream.py):\n  " + "\n  ".join(bad)
        )


def msvc_environment() -> dict[str, str]:
    """Environment of a "x64 Native Tools" prompt (what Aegisub's CI gets from Enter-VsDevShell)."""
    if "VCINSTALLDIR" in os.environ:
        return dict(os.environ)
    vswhere = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    vswhere = vswhere / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    install = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not install:
        raise SystemExit("Visual Studio with the x64 C++ toolchain was not found")
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    dump = subprocess.run(
        f'"{vcvars}" >nul && set',
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    env: dict[str, str] = {}
    for line in dump.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            env[key] = value
    return env


def meson_command() -> list[str]:
    # Run meson with this interpreter: import('python').find_installation() then targets it.
    return [sys.executable, "-m", "mesonbuild.mesonmain"]


# MSYS2 environments whose packages can build the Linux branch: package prefix, C compiler
MSYS2_ENVIRONMENTS: dict[str, tuple[str, str]] = {
    "ucrt64": ("mingw-w64-ucrt-x86_64", "gcc"),
    "mingw64": ("mingw-w64-x86_64", "gcc"),
    "clang64": ("mingw-w64-clang-x86_64", "clang"),
    "clangarm64": ("mingw-w64-clang-aarch64", "clang"),
}
MSYS2_DEFAULT = "ucrt64"


@dataclass(slots=True)
class Msys2:
    prefix: Path  # e.g. C:\msys64\ucrt64
    name: str  # e.g. ucrt64
    compiler: Path
    pkg_config: Path


def msys2_toolchain(path: Path) -> Msys2:
    """The MSYS2 environment `path` names (its prefix, or the MSYS2 root for ucrt64), with every
    package the Linux branch needs; otherwise exit naming the environment and what to install."""
    path = path.resolve()
    if path.name.lower() in MSYS2_ENVIRONMENTS:
        prefix, what = path, f"{path} ({path.name.lower()} environment)"
    elif (path / "usr" / "bin").is_dir() or (path / "msys2.exe").is_file():
        prefix, what = path / MSYS2_DEFAULT, f"{path} ({MSYS2_DEFAULT} environment, the default for an MSYS2 root)"
    else:
        known = ", ".join(MSYS2_ENVIRONMENTS)
        raise SystemExit(f"{path}: not an MSYS2 root or environment ({known})")
    name = prefix.name.lower()
    package, compiler = MSYS2_ENVIRONMENTS[name]
    bin_dir, pc_dir = prefix / "bin", prefix / "lib" / "pkgconfig"
    pkg_config = next((bin_dir / n for n in ("pkg-config.exe", "pkgconf.exe") if (bin_dir / n).is_file()), None)
    needs = {
        f"{package}-{compiler}": (bin_dir / f"{compiler}.exe").is_file(),
        f"{package}-pkgconf": pkg_config is not None,
        f"{package}-fontconfig": (pc_dir / "fontconfig.pc").is_file(),
        f"{package}-harfbuzz": (pc_dir / "harfbuzz.pc").is_file(),
    }
    missing = [pkg for pkg, present in needs.items() if not present]
    if missing:
        raise SystemExit(
            f"{what} lacks {', '.join(missing)}\ninstall them from an MSYS2 shell: pacman -S {' '.join(missing)}"
        )
    assert pkg_config is not None
    return Msys2(prefix=prefix, name=name, compiler=bin_dir / f"{compiler}.exe", pkg_config=pkg_config)


def msys2_environment(toolchain: Msys2) -> dict[str, str]:
    """This environment with the MSYS2 toolchain first: its C compiler and pkg-config."""
    env = dict(os.environ)
    env["PATH"] = str(toolchain.prefix / "bin") + os.pathsep + env.get("PATH", "")
    env["CC"] = str(toolchain.compiler)
    env["PKG_CONFIG"] = str(toolchain.pkg_config)
    return env


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the extension modules and their stubs.")
    parser.add_argument(
        "--msys2",
        type=Path,
        metavar="ENV",
        help="Windows only: build the Linux branch with an MSYS2 environment, e.g. C:\\msys64\\ucrt64 "
        "(an MSYS2 root means its ucrt64)",
    )
    args = parser.parse_args(argv)
    lock = load_lock()
    verify_vendored(lock)
    options = ["--buildtype=release", "--backend=ninja", *luajit_options(lock)]
    if args.msys2 is not None:
        if not WINDOWS:
            raise SystemExit("--msys2 is for Windows; elsewhere the Linux branch is the default")
        toolchain = msys2_toolchain(args.msys2)
        prefix = toolchain.prefix
        env = msys2_environment(toolchain)
        build_dir = NATIVE / f"builddir-msys2-{toolchain.name}"
        options.append("-Dbranch=linux")
    else:
        prefix = None
        env = msvc_environment() if WINDOWS else dict(os.environ)
        build_dir = BUILD_DIR
    meson = meson_command()
    bin_dir = str(Path(sys.executable).parent)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")  # cython and ninja from this environment
    stamp_file = build_dir / "upstream.json.sha256"
    stamp = sha256(LOCK)
    if build_dir.exists() and (not stamp_file.is_file() or stamp_file.read_text(encoding="utf-8").strip() != stamp):
        print(f"{LOCK.name} changed: starting {build_dir.name} afresh")
        shutil.rmtree(build_dir)
    if not (build_dir / "build.ninja").exists():
        subprocess.run([*meson, "setup", str(build_dir), *options], cwd=ROOT, env=env, check=True)
        stamp_file.write_text(stamp + "\n", encoding="utf-8")
    subprocess.run([*meson, "compile", "-C", str(build_dir)], cwd=ROOT, env=env, check=True)
    for name, package_dir in MODULES.items():
        built = [p for p in build_dir.glob(f"{name}.*{SUFFIX}") if p.is_file()]
        if len(built) != 1:
            raise SystemExit(f"expected one {name}.*{SUFFIX} in {build_dir}, found {built}")
        target = package_dir / built[0].name
        shutil.copy2(built[0], target)
        print(f"{target.relative_to(ROOT)}  sha256={sha256(target)}")
    # the DLLs the MSYS2-built modules load (fontconfig, HarfBuzz, the gcc runtime)
    if prefix is not None:
        DLL_DIRS.write_text(str(prefix / "bin") + "\n", encoding="utf-8")
        print(f"{DLL_DIRS.relative_to(ROOT)}: {prefix / 'bin'}")
        print("the committed MSVC modules were replaced; a plain build (or git checkout) restores them")
    else:
        DLL_DIRS.unlink(missing_ok=True)
    # a fresh interpreter, so the modules just copied are the ones imported
    subprocess.run([sys.executable, str(NATIVE / "stubgen.py")], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
