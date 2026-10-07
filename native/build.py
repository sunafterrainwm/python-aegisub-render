"""Build src/aegisub_render/_native/aegisub_lua.dll the same way Aegisub 3.5.0 builds its Lua runtime.

Usage (from the repository root):

    uv run --group build python native/build.py

Requirements: Visual Studio 2022 (or Build Tools) with the C++ x64 toolchain.

Every vendored input is checked against the sha256 it has in the Aegisub v3.5.0 source
snapshot before building; the LuaJIT source tarball and its meson wrapdb patch are
verified by meson itself through the hashes in subprojects/luajit.wrap (copied verbatim
from Aegisub).
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
ROOT = NATIVE.parent
BUILD_DIR = NATIVE / "builddir"
OUTPUT = ROOT / "src" / "aegisub_render" / "_native" / "aegisub_lua.dll"

# sha256 of the files copied from the Aegisub v3.5.0 source snapshot
# (https://github.com/TypesettingTools/Aegisub/archive/refs/tags/v3.5.0.zip).
VENDORED: dict[str, str] = {
    "subprojects/luajit.wrap": "3bad67fc4af0625152c059c5f22d1b67677814e2757a5307b8debd0e54d4177f",
    "src/lpeg.c": "78b8469d543383a69bb3e5fcc8a65fb5ee61fd1672baaa11958917b16868e8ef",
    "src/lpeg.h": "7b4930365ba289d0d1a2d0c7d43fcf786fc286999654f86b5470ec1ebf99dc78",
    "subprojects/luabins/meson.build": "c74809d78b665c0c10d142f0a7435708c84c0b099631f197f8d48cf6f3829cd6",
    "subprojects/luabins/src/fwrite.c": "97305a6bdbd1354e81c34346cb49e7d9433f9a720dfcfbe3b729599669105097",
    "subprojects/luabins/src/fwrite.h": "917f49a59c328320adfa7a5f221befe05ae3c8295cb5f0f3d3d2eef015e02dc0",
    "subprojects/luabins/src/load.c": "f028861424fa3397a6ecbb72b9694ee75700d30bc7389c6d0487518d6ccfdff2",
    "subprojects/luabins/src/luabins.c": "690e1e465f0f8519c21f9621c43f29dccfb7bb2a5547c89068ff589081feae2e",
    "subprojects/luabins/src/luabins.h": "c04d9db5a4802555f6374e406d290483661c8b67bd84f4cf8e24d74b80840758",
    "subprojects/luabins/src/luaheaders.h": "f347d2af91a01b047089dea3cd49cebe44fd32630e00cc19f8f24acd57ca16b7",
    "subprojects/luabins/src/luainternals.c": "f68cd6679026237f1e6ae3f058792cb093353c778eec699b6a419f5add51bce5",
    "subprojects/luabins/src/luainternals.h": "a3fe1e82259ffc96ed47c4180b1c824b95e36dedc1ad2da07eddd4d168a2aa0a",
    "subprojects/luabins/src/lualess.h": "8e092149f07d3658127755b1952cfc328703b58856cb6b969741f74756a5f627",
    "subprojects/luabins/src/save.c": "48e9be479a514bff80c5c913cfc02630ab834a210071beeada4d159767cc4494",
    "subprojects/luabins/src/savebuffer.c": "38d1e3f05efd52da8f6b8cd1de731eb52b0eadd19c311e4c09322d32d1bc0fee",
    "subprojects/luabins/src/savebuffer.h": "6d2c7aff265032968951f4926827c812ba3f7b75ab1a3989383d386ef93178b4",
    "subprojects/luabins/src/saveload.h": "1155e446aa805aa82c41464f164cfdbde2091e6a101b050c979bda9ed498939c",
    "subprojects/luabins/src/write.c": "43bd4954724244ef9e403111cec2683f6fb7f40a4e6cb8d02b0dccd620fc64cc",
    "subprojects/luabins/src/write.h": "c9081af9a76ee8864d97818c2eeefa9f3d4cbc1c39e3f34a8c0fb9011297108d",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_vendored() -> None:
    bad = [name for name, digest in VENDORED.items() if sha256(NATIVE / name) != digest]
    if bad:
        raise SystemExit("vendored files differ from Aegisub v3.5.0:\n  " + "\n  ".join(bad))


def msvc_environment() -> dict[str, str]:
    """Environment of a "x64 Native Tools" prompt (what Aegisub's CI gets from Enter-VsDevShell)."""
    if "VCINSTALLDIR" in os.environ:
        return dict(os.environ)
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = vswhere / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    install = subprocess.run(
        [str(vswhere), "-latest", "-products", "*",
         "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not install:
        raise SystemExit("Visual Studio with the x64 C++ toolchain was not found")
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    dump = subprocess.run(
        f'"{vcvars}" >nul && set', shell=True, check=True, capture_output=True, text=True,
    ).stdout
    env: dict[str, str] = {}
    for line in dump.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            env[key] = value
    return env


def meson_command() -> list[str]:
    exe = shutil.which("meson")
    if exe:
        return [exe]
    return [sys.executable, "-m", "mesonbuild.mesonmain"]


def main() -> None:
    verify_vendored()
    env = msvc_environment()
    meson = meson_command()
    if not (BUILD_DIR / "build.ninja").exists():
        subprocess.run(
            [*meson, "setup", str(BUILD_DIR), "--buildtype=release", "--backend=ninja"],
            cwd=NATIVE, env=env, check=True,
        )
    subprocess.run([*meson, "compile", "-C", str(BUILD_DIR)], cwd=NATIVE, env=env, check=True)
    built = BUILD_DIR / "aegisub_lua.dll"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, OUTPUT)
    print(f"{OUTPUT.relative_to(ROOT)}  sha256={sha256(OUTPUT)}")


if __name__ == "__main__":
    main()
