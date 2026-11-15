"""The logic behind aegisub.__re_impl / __unicode_impl / __lfs_impl.

Aegisub implements these in aegisub.exe (libaegisub/lua/modules/{re,unicode,lfs}.cpp) and the
.moon wrappers in automation/include/aegisub call them through LuaJIT's ffi. The C functions
with those exact signatures live in lua/_luajit.pyx; they marshal arguments and call the
methods of `FfiImpl` below. Raising an exception here becomes the `char **err` message.
"""

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import regex

from aegisub_render.host import regex_impl
from aegisub_render.lua import _luajit
from aegisub_render.lua.values import LightUserdata
from aegisub_render.textenc import from_bytes, to_bytes

# seconds between 1601-01-01 (the MSVC std::filesystem file_clock epoch) and 1970-01-01
_FILETIME_EPOCH_OFFSET = 11644473600

_MODE_NAMES: dict[int, bytes] = {
    stat.S_IFREG: b"file",
    stat.S_IFDIR: b"directory",
    stat.S_IFLNK: b"link",
    stat.S_IFBLK: b"block device",
    stat.S_IFCHR: b"char device",
    stat.S_IFIFO: b"fifo",
    stat.S_IFSOCK: b"socket",
}

# C types of the functions, as agi::type_name renders them in libaegisub/lua/ffi.h
_SIGNATURES: dict[str, dict[str, str]] = {
    "re": {
        "search": "int* (*)(u32regex&, char const*, unsigned long long, unsigned long long, char**)",
        "match": "agi_re_match* (*)(u32regex&, char const*, unsigned long long, int, char**)",
        "get_match": "int* (*)(agi_re_match&, unsigned long long)",
        "replace": "char* (*)(u32regex&, char const*, char const*, unsigned long long, int, char**)",
        "compile": "u32regex* (*)(char const*, int, char**)",
        "get_flags": "agi_re_flag const* (*)()",
        "match_free": "void (*)(agi_re_match*)",
        "regex_free": "void (*)(u32regex*)",
    },
    "unicode": {
        "to_upper_case": "char* (*)(char const*, char**)",
        "to_lower_case": "char* (*)(char const*, char**)",
        "to_fold_case": "char* (*)(char const*, char**)",
    },
    "lfs": {
        "chdir": "bool (*)(char const*, char**)",
        "currentdir": "char* (*)(char**)",
        "mkdir": "bool (*)(char const*, char**)",
        "rmdir": "bool (*)(char const*, char**)",
        "touch": "bool (*)(char const*, char**)",
        "get_mtime": "long long (*)(char const*, char**)",
        "get_mode": "char const* (*)(char const*, char**)",
        "get_size": "unsigned long long (*)(char const*, char**)",
        "dir_new": "DirectoryIterator* (*)(char const*, char**)",
        "dir_free": "void (*)(DirectoryIterator*, char**)",
        "dir_next": "char* (*)(DirectoryIterator&, char**)",
        "dir_close": "void (*)(DirectoryIterator&, char**)",
    },
}

_TYPES: dict[str, list[str]] = {"re": ["agi_re_match", "u32regex"], "unicode": [], "lfs": ["DirectoryIterator"]}


@dataclass(slots=True)
class _Match:
    match: regex.Match[str]
    subject: regex_impl.Subject
    start: int
    nosubs: bool


def _fs_message(e: OSError, path: str) -> str:
    return f'{e.strerror or e}: "{path}"'


class FfiImpl:
    """Implements the _luajit.FfiImpl protocol."""

    # -- re

    def re_flags(self) -> list[tuple[bytes, int]]:
        return [(name.encode(), value) for name, value in regex_impl.FLAGS.items()]

    def re_compile(self, pattern: bytes, flags: int) -> object:
        return regex_impl.compile_regex(from_bytes(pattern), flags)

    def re_search(self, compiled: object, subject: bytes, start: int) -> tuple[int, int] | None:
        assert isinstance(compiled, regex_impl.CompiledRegex)
        subj = regex_impl.Subject.decode(subject)
        m = regex_impl.search(compiled, subj, start)
        if m is None:
            return None
        return subj.byte_offsets[m.start()] + 1, subj.byte_offsets[m.end()]

    def re_match(self, compiled: object, subject: bytes, start: int) -> object | None:
        assert isinstance(compiled, regex_impl.CompiledRegex)
        subj = regex_impl.Subject.decode(subject)
        m = regex_impl.search(compiled, subj, start)
        return None if m is None else _Match(m, subj, start, compiled.nosubs)

    def re_get_match(self, match: object, idx: int) -> tuple[int, int] | None:
        assert isinstance(match, _Match)
        span = regex_impl.group_span_bytes(match.match, match.subject, idx, match.nosubs)
        if span is None:
            return None
        return span[0] - match.start + 1, span[1] - match.start

    def re_replace(self, compiled: object, replacement: bytes, subject: bytes, max_count: int) -> bytes:
        assert isinstance(compiled, regex_impl.CompiledRegex)
        out = regex_impl.replace(compiled, from_bytes(replacement), regex_impl.Subject.decode(subject), max_count)
        return to_bytes(out)

    # -- unicode (icu::UnicodeString::fromUTF8 replaces invalid sequences)

    def unicode_upper(self, text: bytes) -> bytes:
        return text.decode("utf-8", "replace").upper().encode("utf-8")

    def unicode_lower(self, text: bytes) -> bytes:
        return text.decode("utf-8", "replace").lower().encode("utf-8")

    def unicode_fold(self, text: bytes) -> bytes:
        return text.decode("utf-8", "replace").casefold().encode("utf-8")

    # -- lfs

    def _setter(self, path: bytes, action: str) -> None:
        p = from_bytes(path)
        try:
            match action:
                case "chdir":
                    os.chdir(p)
                case "mkdir":
                    Path(p).mkdir(parents=True, exist_ok=True)
                case "rmdir":
                    target = Path(p)
                    if target.is_dir() and not target.is_symlink():
                        target.rmdir()
                    else:
                        target.unlink()
                case _:
                    target = Path(p)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
                    os.utime(target, (os.stat(target).st_atime, time.time()))
        except OSError as e:
            raise OSError(_fs_message(e, p)) from None

    def lfs_chdir(self, path: bytes) -> None:
        self._setter(path, "chdir")

    def lfs_mkdir(self, path: bytes) -> None:
        self._setter(path, "mkdir")

    def lfs_rmdir(self, path: bytes) -> None:
        self._setter(path, "rmdir")

    def lfs_touch(self, path: bytes) -> None:
        self._setter(path, "touch")

    def lfs_currentdir(self) -> bytes:
        return to_bytes(os.getcwd())

    def lfs_get_mtime(self, path: bytes) -> int:
        p = from_bytes(path)
        try:
            # duration_cast<seconds>(last_write_time(p).time_since_epoch()) with MSVC's file clock
            return int(os.stat(p).st_mtime) + _FILETIME_EPOCH_OFFSET
        except OSError as e:
            raise OSError(_fs_message(e, p)) from None

    def lfs_get_mode(self, path: bytes) -> bytes | None:
        p = from_bytes(path)
        try:
            st = os.stat(p)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise OSError(_fs_message(e, p)) from None
        return _MODE_NAMES.get(stat.S_IFMT(st.st_mode), b"other")

    def lfs_get_size(self, path: bytes) -> int:
        p = from_bytes(path)
        if os.path.isdir(p):
            raise OSError(f"{p} is not a file")
        try:
            return os.stat(p).st_size
        except OSError as e:
            raise OSError(_fs_message(e, p)) from None

    def lfs_dir_new(self, path: bytes) -> object:
        p = from_bytes(path)
        try:
            names = os.listdir(p)
        except OSError as e:
            raise OSError(f"cannot open {p}: {e.strerror or e}") from None
        # agi::fs::DirectoryIterator skips "." and names starting with ".."
        return [n for n in names if not (n == "." or n.startswith(".."))]

    def lfs_dir_next(self, iterator: object) -> bytes | None:
        assert isinstance(iterator, list)
        entries: list[str] = iterator  # pyright: ignore[reportUnknownVariableType]
        return to_bytes(entries.pop(0)) if entries else None

    def lfs_dir_close(self, iterator: object) -> None:
        assert isinstance(iterator, list)
        iterator.clear()


def install() -> dict[str, dict[str, object]]:
    """Install the implementation; return the specs the bootstrap casts with ffi.cast."""
    _luajit.set_ffi_impl(FfiImpl())
    addresses = _luajit.ffi_functions()
    specs: dict[str, dict[str, object]] = {}
    for lib, signatures in _SIGNATURES.items():
        funcs = [[name, sig, LightUserdata(addresses[lib][name])] for name, sig in signatures.items()]
        specs[lib] = {"types": _TYPES[lib], "funcs": funcs}
    return specs
