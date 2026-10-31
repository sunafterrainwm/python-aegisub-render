"""native/sync_upstream.py against local git repositories laid out like the Aegisub trees it reads."""

import subprocess
from pathlib import Path

import pytest

from native.sync_upstream import (
    LUABINS_SOURCES,
    LocalGitSource,
    SyncError,
    changes,
    normalize_ref,
    parse_source,
    plan,
)

ROOT = Path(__file__).resolve().parent.parent
LUAJIT_SHA = "97813fb924edf822455f91a5fbbdfdb349e5984f"


def ours(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def git_repo(path: Path, files: dict[str, bytes]) -> LocalGitSource:
    for name, data in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_bytes(data)

    def git(*args: str) -> None:
        # a throwaway repository: keep the developer's git config (signing, hooks) out of it
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", "-c", "core.autocrlf=false", *args],
            cwd=path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "tree")
    return LocalGitSource(path)


def common_files() -> dict[str, bytes]:
    return {
        "libaegisub/lua/modules/lpeg.c": ours("native/src/lpeg.c"),
        "libaegisub/lua/modules/lpeg.h": ours("native/src/lpeg.h"),
        "automation/include/moonscript.lua": ours("aegisub_render/host/lua/moonscript.lua"),
    }


def aegisub_35_tree() -> dict[str, bytes]:
    files = common_files()
    files["subprojects/luajit.wrap"] = ours("native/subprojects/luajit.wrap")
    files["meson.build"] = (
        b"project('Aegisub', default_options: ['luajit:default_library=static', 'luajit:lua52compat=true'])\n"
    )
    files["subprojects/luabins/meson.build"] = ours("native/subprojects/luabins/meson.build")
    for name in LUABINS_SOURCES:
        files[f"subprojects/luabins/src/{name}"] = ours(f"native/subprojects/luabins/src/{name}")
    return files


def feature_tree(revision: str = LUAJIT_SHA) -> dict[str, bytes]:
    files = common_files()
    files["subprojects/luajit.wrap"] = (
        f"[wrap-git]\ndirectory = luajit\nurl = file:///nowhere/LuaJIT.git\nrevision = {revision}\n"
        "patch_directory = luajit\n\n[provide]\nluajit = luajit_dep\n"
    ).encode()
    files["meson.build"] = b"project('Aegisub', version: '3.2.2')\n"
    files["subprojects/packagefiles/luajit/meson.build"] = b"project('luajit', 'c')\n"
    files["subprojects/packagefiles/luajit/src/meson.build"] = b"luajit_lib = static_library('luajit')\n"
    for name in LUABINS_SOURCES:
        files[f"vendor/luabins/src/{name}"] = ours(f"native/subprojects/luabins/src/{name}")
    return files


@pytest.mark.parametrize(
    ("ref", "expected"),
    [("9706-cibuilds-20caaabc0", "20caaabc0"), ("v3.5.1", "v3.5.1"), ("feature_12", "feature_12"), ("HEAD", "HEAD")],
)
def test_normalize_ref(ref: str, expected: str) -> None:
    assert normalize_ref(ref) == expected


def test_parse_source() -> None:
    source, ref = parse_source("https://github.com/arch1t3cht/Aegisub/releases/tag/feature_12", None)
    assert (source.description, ref) == ("https://github.com/arch1t3cht/Aegisub", "feature_12")
    source, ref = parse_source("TypesettingTools/Aegisub", "9706-cibuilds-20caaabc0")
    assert (source.description, ref) == ("https://github.com/TypesettingTools/Aegisub", "20caaabc0")
    _, ref = parse_source("https://github.com/arch1t3cht/Aegisub/commit/20caaabc0", None)
    assert ref == "20caaabc0"
    with pytest.raises(SyncError, match="--ref"):
        parse_source("https://github.com/arch1t3cht/Aegisub/tree/feature", "master")
    with pytest.raises(SyncError, match="not a local directory"):
        parse_source("not a repository", None)


def test_aegisub_35_layout_reproduces_the_vendored_files(tmp_path: Path) -> None:
    p = plan(git_repo(tmp_path, aegisub_35_tree()), "HEAD")
    assert p.layout == "aegisub-3.5"
    assert p.luajit["kind"] == "wrap-file" and p.luajit["revision"] == "04dca7911ea255f37be799c18d74c305b921c1a6"
    assert p.luajit["version"] == "LuaJIT 2.1.1720049189"
    assert p.meson_options == ["default_library=static", "lua52compat=true"]
    assert p.moonscript == "0.7.0"
    assert changes(p) == ([], [])  # the repository's vendored files are exactly these


def test_feature_layout(tmp_path: Path) -> None:
    p = plan(git_repo(tmp_path, feature_tree()), "HEAD")
    assert p.layout == "arch1t3cht-feature"
    assert p.luajit["kind"] == "wrap-git" and p.luajit["revision"] == LUAJIT_SHA
    assert p.meson_options == []
    wrap = p.files["native/subprojects/luajit.wrap"][1]
    assert f"revision = {LUAJIT_SHA}".encode() in wrap and b"patch_directory = luajit" in wrap
    assert {
        "native/subprojects/packagefiles/luajit/meson.build",
        "native/subprojects/packagefiles/luajit/src/meson.build",
    } <= p.files.keys()
    assert p.files["native/subprojects/luabins/src/luabins.c"][0] == "vendor/luabins/src/luabins.c"
    assert p.files["native/subprojects/luabins/meson.build"][0] == "(local)"


def test_branch_revision_needs_github(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match="only GitHub"):
        plan(git_repo(tmp_path, feature_tree(revision="v2.1")), "HEAD")


def test_other_layouts_are_refused(tmp_path: Path) -> None:
    files = feature_tree()
    files["subprojects/luajit.wrap"] = (
        b"[wrap-git]\ndirectory = luajit\nurl = https://example.invalid/l.git\nrevision = x\n"
    )
    with pytest.raises(SyncError, match=r"neither a \[wrap-file\]"):
        plan(git_repo(tmp_path / "a", files), "HEAD")

    files = aegisub_35_tree()
    del files["subprojects/luabins/src/save.c"]
    files["automation/include/moonscript.lua"] = b"return {}\n"
    with pytest.raises(SyncError) as e:
        plan(git_repo(tmp_path / "b", files), "HEAD")
    assert "missing subprojects/luabins/src/save.c" in str(e.value)
    assert "no moonscript.version module" in str(e.value)

    with pytest.raises(SyncError, match=r"missing subprojects/luajit\.wrap"):
        plan(git_repo(tmp_path / "c", common_files()), "HEAD")
