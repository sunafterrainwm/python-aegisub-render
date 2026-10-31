"""Take the Lua runtime (LuaJIT, lpeg, luabins) and MoonScript of an Aegisub source tree.

Usage (from the repository root):

    uv run python native/sync_upstream.py SOURCE [--ref REF] [--dry-run]

SOURCE is a GitHub repository (`owner/repo`, `https://github.com/owner/repo`, or a
`.../releases/tag/T`, `.../tree/T` or `.../commit/C` URL, which also gives the ref) or the
path of a local git repository. REF is a tag, branch or commit (default HEAD), or an Aegisub
version string such as `9706-cibuilds-20caaabc0`, which names commit `20caaabc0`. GitHub
sources are read file by file (raw.githubusercontent.com and the REST API; set GITHUB_TOKEN
to raise the API rate limit), never cloned; local ones with `git show <commit>:<path>`.

Two layouts are understood:

* aegisub-3.5 (TypesettingTools/Aegisub 3.5.x): subprojects/luajit.wrap is a pinned
  [wrap-file] (wrapdb's LuaJIT port), luabins lives in subprojects/luabins and meson.build
  passes 'luajit:...' options to the subproject.
* arch1t3cht-feature (arch1t3cht/Aegisub feature releases): subprojects/luajit.wrap is a
  [wrap-git] whose build comes from subprojects/packagefiles/luajit, and luabins lives in
  vendor/luabins. A branch or tag revision (`v2.1`) is pinned to the commit it pointed to at
  the source commit's date, since a moving branch would not be reproducible.

Both take lpeg from libaegisub/lua/modules and MoonScript from automation/include/moonscript.lua.
Anything else is refused and nothing changes. The result replaces the vendored files
(native/subprojects, native/src/lpeg.*, aegisub_render/host/lua/moonscript.lua) and
native/upstream.json, which records where they came from, the sha256 native/build.py checks
and the meson options for the LuaJIT subproject. Rebuild with native/build.py afterwards.
"""

import argparse
import configparser
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parent.parent
LOCK = "native/upstream.json"

LUABINS_SOURCES = (
    "fwrite.c", "fwrite.h", "load.c", "luabins.c", "luabins.h", "luaheaders.h", "luainternals.c",
    "luainternals.h", "lualess.h", "save.c", "savebuffer.c", "savebuffer.h", "saveload.h", "write.c", "write.h",
)  # fmt: skip
COMMON_FILES = {  # local path -> upstream path, the same in both layouts
    "native/src/lpeg.c": "libaegisub/lua/modules/lpeg.c",
    "native/src/lpeg.h": "libaegisub/lua/modules/lpeg.h",
    "aegisub_render/host/lua/moonscript.lua": "automation/include/moonscript.lua",
}
PACKAGEFILES = "subprojects/packagefiles/luajit"
SHA_RE = re.compile(r"[0-9a-f]{40}")
# "<revision>-<branch>-<commit>", the version string of Aegisub builds made outside a release tag
VERSION_STRING_RE = re.compile(r"\d+-[\w.+-]*?-([0-9a-f]{7,40})")
MOONSCRIPT_VERSION_RE = re.compile(
    rb"package\.preload\['moonscript\.version'\] = function\(\)\s*local version = \"([^\"]+)\""
)


class SyncError(Exception):
    pass


class Source(Protocol):
    description: str

    def resolve(self, ref: str) -> tuple[str, str]:
        """(full commit sha, committer date in ISO 8601)"""
        ...

    def read(self, commit: str, path: str) -> bytes | None: ...

    def list_files(self, commit: str, directory: str) -> list[str]: ...


def _http(url: str, accept: str = "application/vnd.github+json") -> bytes | None:
    """GET url; None on 404."""
    headers = {"User-Agent": "aegisub-render-sync", "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise SyncError(f"{url}: HTTP {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise SyncError(f"{url}: {e.reason}") from None


def _json(url: str) -> object:
    data = _http(url)
    if data is None:
        raise SyncError(f"{url}: not found")
    result: object = json.loads(data)
    return result


def _obj(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SyncError(f"unexpected data: {what}")
    return cast(dict[str, object], value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise SyncError(f"unexpected data: {what}")
    return cast(list[object], value)


def _str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise SyncError(f"unexpected data: {what}")
    return value


@dataclass(slots=True)
class GitHubSource:
    owner: str
    repo: str
    description: str = ""
    _trees: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def __post_init__(self) -> None:
        self.description = f"https://github.com/{self.owner}/{self.repo}"

    def resolve(self, ref: str) -> tuple[str, str]:
        quoted = urllib.parse.quote(ref, safe="")
        what = f"commit {ref!r} of {self.description}"
        data = _obj(_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{quoted}"), what)
        committer = _obj(_obj(data.get("commit"), what).get("committer"), what)
        return _str(data.get("sha"), what), _str(committer.get("date"), what)

    def read(self, commit: str, path: str) -> bytes | None:
        return _http(f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{commit}/{path}", "*/*")

    def list_files(self, commit: str, directory: str) -> list[str]:
        if commit not in self._trees:
            what = f"the files of {commit}"
            data = _obj(
                _json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{commit}?recursive=1"), what
            )
            entries = [_obj(e, what) for e in _list(data.get("tree"), what)]
            self._trees[commit] = [_str(e.get("path"), what) for e in entries if e.get("type") == "blob"]
        prefix = directory.rstrip("/") + "/"
        return sorted(p for p in self._trees[commit] if p.startswith(prefix))


@dataclass(slots=True)
class LocalGitSource:
    path: Path
    description: str = ""

    def __post_init__(self) -> None:
        self.description = str(self.path)

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, check=False)

    def resolve(self, ref: str) -> tuple[str, str]:
        r = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode != 0:
            raise SyncError(f"{self.path}: no commit {ref!r}")
        sha = r.stdout.decode().strip()
        date = self._git("show", "-s", "--format=%cI", sha).stdout.decode().strip()
        return sha, date

    def read(self, commit: str, path: str) -> bytes | None:
        r = self._git("show", f"{commit}:{path}")
        return r.stdout if r.returncode == 0 else None

    def list_files(self, commit: str, directory: str) -> list[str]:
        r = self._git("ls-tree", "-r", "--name-only", commit, "--", directory.rstrip("/") + "/")
        return sorted(r.stdout.decode().split()) if r.returncode == 0 else []


def parse_source(spec: str, ref: str | None) -> tuple[Source, str]:
    """The source and the ref to take from it (a ref in the URL and --ref must agree)."""
    if Path(spec).exists():
        return LocalGitSource(Path(spec).resolve()), normalize_ref(ref or "HEAD")
    m = re.fullmatch(
        r"(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?"
        r"(?:/(?:releases/tag|tree|commit)/([^?#]+))?/?",
        spec,
    )
    if not m:
        raise SyncError(f"not a local directory or a GitHub repository: {spec}")
    url_ref = urllib.parse.unquote(m.group(3)) if m.group(3) else None
    if url_ref and ref and url_ref != ref:
        raise SyncError(f"the URL names {url_ref!r} but --ref is {ref!r}")
    return GitHubSource(m.group(1), m.group(2)), normalize_ref(url_ref or ref or "HEAD")


def normalize_ref(ref: str) -> str:
    """An Aegisub version string `<revision>-<branch>-<commit>` names its commit."""
    m = VERSION_STRING_RE.fullmatch(ref)
    return m.group(1) if m else ref


@dataclass(slots=True)
class Plan:
    source: str
    ref: str
    commit: str
    date: str
    layout: str
    luajit: dict[str, str]
    moonscript: str
    meson_options: list[str]
    files: dict[str, tuple[str, bytes]]  # local path -> (where it came from, content)

    def lock(self) -> dict[str, object]:
        return {
            "source": self.source,
            "ref": self.ref,
            "commit": self.commit,
            "date": self.date,
            "layout": self.layout,
            "luajit": self.luajit,
            "luajit_meson_options": self.meson_options,
            "moonscript": self.moonscript,
            "files": {
                path: {"from": origin, "sha256": hashlib.sha256(data).hexdigest()}
                for path, (origin, data) in sorted(self.files.items())
            },
        }


def _wrap(text: bytes) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text.decode("utf-8"))
    return parser


def _meson_options(meson_build: bytes) -> list[str]:
    """The 'luajit:...' entries of meson.build (default_options passed to the subproject)."""
    return [m.decode() for m in re.findall(rb"'luajit:([\w-]+=[^']*)'", meson_build)]


def pin_revision(url: str, revision: str, date: str) -> str:
    """A [wrap-git] revision as a commit: a branch or tag is taken as it was at `date`."""
    if SHA_RE.fullmatch(revision):
        return revision
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url)
    if not m:
        raise SyncError(f"cannot pin revision {revision!r} of {url}: only GitHub repositories are supported")
    query = urllib.parse.urlencode({"sha": revision, "until": date, "per_page": "1"})
    what = f"revision {revision!r} of {url}"
    data = _list(_json(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/commits?{query}"), what)
    if not data:
        raise SyncError(f"{url} has no commit on {revision!r} before {date}")
    return _str(_obj(data[0], what).get("sha"), what)


def git_luajit_version(url: str, commit: str) -> str:
    """jit.version of a LuaJIT built from a git checkout: genversion.lua puts the commit's
    committer timestamp (`git show -s --format=%ct`) in place of ROLLING in LUAJIT_VERSION.
    Empty when the repository is not on GitHub."""
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url)
    if not m:
        return ""
    repo = GitHubSource(m.group(1), m.group(2))
    _, date = repo.resolve(commit)
    header = repo.read(commit, "src/luajit_rolling.h") or b""
    v = re.search(rb'#define\s+LUAJIT_VERSION\s+"([^"]+)"', header)
    if not v:
        raise SyncError(f"{url}@{commit}: no LUAJIT_VERSION in src/luajit_rolling.h")
    timestamp = int(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())
    return v.group(1).decode().replace("ROLLING", str(timestamp))


def plan(source: Source, ref: str) -> Plan:
    commit, date = source.resolve(ref)
    problems: list[str] = []

    def need(path: str) -> bytes:
        data = source.read(commit, path)
        if data is None:
            problems.append(f"missing {path}")
            return b""
        return data

    wrap_text = need("subprojects/luajit.wrap")
    meson_build = need("meson.build")
    files: dict[str, tuple[str, bytes]] = {local: (up, need(up)) for local, up in COMMON_FILES.items()}
    if problems:
        raise SyncError("not an Aegisub source tree this tool understands:\n  " + "\n  ".join(problems))

    wrap = _wrap(wrap_text)
    luajit: dict[str, str]
    if wrap.has_section("wrap-file"):
        layout = "aegisub-3.5"
        section = wrap["wrap-file"]
        for key in ("directory", "source_url", "source_hash", "patch_url", "patch_hash"):
            if key not in section:
                problems.append(f"subprojects/luajit.wrap: [wrap-file] has no {key} (not pinned)")
        m = re.fullmatch(r"LuaJIT-([0-9a-f]{40})", section.get("directory", ""))
        if not m:
            problems.append(f"subprojects/luajit.wrap: directory {section.get('directory')!r} is not LuaJIT-<commit>")
        luajit = {
            "kind": "wrap-file",
            "revision": m.group(1) if m else "",
            "wrapdb_version": section.get("wrapdb_version", ""),
            # wrapdb's port names its version after the same commit timestamp: 2.1.1720049189-2
            "version": "LuaJIT " + section.get("wrapdb_version", "").rpartition("-")[0],
        }
        files["native/subprojects/luajit.wrap"] = ("subprojects/luajit.wrap", wrap_text)
        luabins_dir = "subprojects/luabins"
        files["native/subprojects/luabins/meson.build"] = (
            f"{luabins_dir}/meson.build",
            need(f"{luabins_dir}/meson.build"),
        )
        luabins_src = f"{luabins_dir}/src"
    elif wrap.has_section("wrap-git") and wrap["wrap-git"].get("patch_directory") == "luajit":
        layout = "arch1t3cht-feature"
        section = wrap["wrap-git"]
        url, revision = section.get("url", ""), section.get("revision", "")
        pinned = pin_revision(url, revision, date)
        note = f"# revision {revision} pinned to its commit as of {date} by native/sync_upstream.py\n"
        local_wrap = re.sub(rb"(?m)^revision\s*=.*$", f"{note}revision = {pinned}".encode(), wrap_text, count=1)
        luajit = {
            "kind": "wrap-git",
            "url": url,
            "revision": pinned,
            "upstream_revision": revision,
            "version": git_luajit_version(url, pinned),
        }
        files["native/subprojects/luajit.wrap"] = (
            f"subprojects/luajit.wrap (revision {revision} -> {pinned})",
            local_wrap,
        )
        listed = source.list_files(commit, PACKAGEFILES)
        if f"{PACKAGEFILES}/meson.build" not in listed:
            problems.append(f"missing {PACKAGEFILES}/meson.build")
        for up in listed:
            files["native/subprojects/packagefiles/luajit/" + up[len(PACKAGEFILES) + 1 :]] = (up, need(up))
        # the build of luabins is Aegisub's own there; the sources are what matters, ours builds them
        files["native/subprojects/luabins/meson.build"] = (
            "(local)",
            (ROOT / "native/subprojects/luabins/meson.build").read_bytes(),
        )
        luabins_src = "vendor/luabins/src"
    else:
        found = ", ".join(f"[{s}]" for s in wrap.sections()) or "nothing"
        raise SyncError(
            "subprojects/luajit.wrap is neither a [wrap-file] (aegisub-3.5) nor a [wrap-git] with "
            f"patch_directory = luajit (arch1t3cht-feature); found {found}"
        )
    for name in LUABINS_SOURCES:
        files[f"native/subprojects/luabins/src/{name}"] = (f"{luabins_src}/{name}", need(f"{luabins_src}/{name}"))

    m = MOONSCRIPT_VERSION_RE.search(files["aegisub_render/host/lua/moonscript.lua"][1])
    if not m:
        problems.append("automation/include/moonscript.lua: no moonscript.version module (not a bundled MoonScript)")
    if problems:
        raise SyncError(f"{layout} layout, but:\n  " + "\n  ".join(problems))
    return Plan(
        source=source.description,
        ref=ref,
        commit=commit,
        date=date,
        layout=layout,
        luajit=luajit,
        moonscript=m.group(1).decode() if m else "",
        meson_options=_meson_options(meson_build),
        files=files,
    )


def load_lock(root: Path = ROOT) -> dict[str, object]:
    path = root / LOCK
    if not path.exists():
        return {}
    return _obj(json.loads(path.read_text(encoding="utf-8")), str(path))


def changes(p: Plan, root: Path = ROOT) -> tuple[list[str], list[str]]:
    """(paths written with new content, previously synced paths no longer used)."""
    changed = [path for path, (_, data) in sorted(p.files.items()) if _read(root / path) != data]
    old = list(_obj(load_lock(root).get("files", {}), LOCK))
    removed = sorted(path for path in old if path not in p.files and (root / path).exists())
    return changed, removed


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def apply(p: Plan, root: Path = ROOT) -> tuple[list[str], list[str]]:
    changed, removed = changes(p, root)
    for path in changed:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.files[path][1])
    for path in removed:
        (root / path).unlink()
        parent = (root / path).parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    (root / LOCK).write_text(json.dumps(p.lock(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return changed, removed


def _summary(p: Plan, old: dict[str, object]) -> list[str]:
    old_rev = _obj(old.get("luajit", {}), LOCK).get("revision")
    lines = [
        f"source:     {p.source} {p.ref} -> {p.commit} ({p.date})",
        f"layout:     {p.layout}",
        f"LuaJIT:     {p.luajit['kind']} {p.luajit['revision']}"
        + (f" (was {old_rev})" if old_rev and old_rev != p.luajit["revision"] else ""),
    ]
    if "upstream_revision" in p.luajit:
        lines.append(f"            pinned from {p.luajit['upstream_revision']!r} as of {p.date}")
    if p.luajit.get("wrapdb_version"):
        lines.append(f"            wrapdb {p.luajit['wrapdb_version']}")
    if p.luajit.get("version"):
        lines.append(f"            jit.version {p.luajit['version']!r}")
    lines.append(f"options:    {', '.join(p.meson_options) or '(none)'}")
    old_moon = old.get("moonscript")
    lines.append(
        f"MoonScript: {p.moonscript}" + (f" (was {old_moon})" if old_moon and old_moon != p.moonscript else "")
    )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Take the Lua runtime and MoonScript of an Aegisub source tree.")
    parser.add_argument("source", help="owner/repo, a GitHub URL, or a local git repository")
    parser.add_argument("--ref", help="tag, branch, commit or Aegisub version string (default: HEAD)")
    parser.add_argument("--dry-run", action="store_true", help="only show what would change")
    args = parser.parse_args(argv)
    try:
        source, ref = parse_source(args.source, args.ref)
        p = plan(source, ref)
        old = load_lock()
        for line in _summary(p, old):
            print(line)
        if args.dry_run:
            changed, removed = changes(p)
        else:
            changed, removed = apply(p)
    except SyncError as e:
        print(f"sync_upstream: {e}", file=sys.stderr)
        return 1
    verb = "would change" if args.dry_run else "changed"
    for path in changed:
        print(f"{verb}: {path}")
    for path in removed:
        print(f"{'would remove' if args.dry_run else 'removed'}: {path}")
    native_changed = any(path.startswith("native/") for path in [*changed, *removed])
    if not changed and not removed:
        print("the vendored files are already these")
    elif native_changed and not args.dry_run:
        print("next: uv run --group build python native/build.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
