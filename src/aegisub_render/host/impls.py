"""The C function tables behind aegisub.re / aegisub.unicode / aegisub.lfs.

Aegisub exposes these from aegisub.exe (libaegisub/lua/modules/{re,unicode,lfs}.cpp) and the
.moon wrappers in automation/include/aegisub call them through LuaJIT's ffi. Here they are
Python functions behind ctypes callbacks with the same C signatures; errors are reported
through the `char **err` out-parameter exactly like the C++ versions, so nothing ever unwinds
through LuaJIT frames.
"""

import ctypes
import os
import stat
import time
from collections.abc import Callable
from ctypes import CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_int, c_longlong, c_size_t, c_ulonglong, c_void_p
from dataclasses import dataclass
from pathlib import Path

from aegisub_render.host import regex_impl
from aegisub_render.lua.capi import LuaAPI
from aegisub_render.lua.state import LightUserdata
from aegisub_render.textenc import from_bytes, to_bytes

# seconds between 1601-01-01 (MSVC std::filesystem::file_time_type epoch) and 1970-01-01
_FILETIME_EPOCH_OFFSET = 11644473600


class agi_re_flag(Structure):  # noqa: N801 - C name
    _fields_ = (("name", c_char_p), ("value", c_int))


@dataclass(slots=True)
class LibSpec:
    types: list[str]
    funcs: list[tuple[str, str, LightUserdata]]

    def to_lua(self) -> dict[str, object]:
        return {"types": self.types, "funcs": [list(f) for f in self.funcs]}


class FfiImpls:
    def __init__(self, api: LuaAPI) -> None:
        self.api = api
        self._keep: list[object] = []
        self._handles: dict[int, object] = {}

    # ----------------------------------------------------------------- memory helpers

    def _malloc_bytes(self, data: bytes) -> int:
        """strdup/strndup into memory that ffi.C.free can release."""
        ptr = self.api.aegi_malloc(len(data) + 1) or 0
        if ptr:
            ctypes.memmove(ptr, data, len(data))
            ctypes.memset(ptr + len(data), 0, 1)
        return ptr

    def _set_err(self, err: int, message: str) -> None:
        if err:
            ctypes.cast(err, POINTER(c_void_p))[0] = self._malloc_bytes(to_bytes(message))

    def _new_handle(self, obj: object) -> int:
        ptr = self.api.aegi_malloc(16) or 0
        self._handles[ptr] = obj
        return ptr

    def _free_handle(self, ptr: int | None) -> None:
        if ptr and self._handles.pop(ptr, None) is not None:
            self.api.aegi_free(ptr)

    def _callback(self, proto: type, fn: Callable[..., object]) -> LightUserdata:
        cb = proto(fn)
        self._keep.append(cb)
        return LightUserdata(ctypes.cast(cb, c_void_p).value or 0)

    # ----------------------------------------------------------------- aegisub.__re_impl

    def re_spec(self) -> LibSpec:
        api = self.api
        flags_array = (agi_re_flag * (len(regex_impl.FLAGS) + 1))()
        names: list[bytes] = []
        for i, (name, value) in enumerate(regex_impl.FLAGS.items()):
            names.append(name.encode())
            flags_array[i].name = names[-1]
            flags_array[i].value = value
        self._keep += [flags_array, names]
        flags_ptr = ctypes.addressof(flags_array)

        def compile_(pattern: int, flags: int, err: int) -> int:
            try:
                compiled = regex_impl.compile_regex(from_bytes(ctypes.string_at(pattern)), flags)
            except Exception as e:  # noqa: BLE001 - reported to Lua through err
                self._set_err(err, str(e))
                return 0
            return self._new_handle(compiled)

        def regex_free(re: int) -> None:
            self._free_handle(re)

        def search(re: int, s: int, length: int, start: int, err: int) -> int:
            try:
                compiled = self._handles[re]
                assert isinstance(compiled, regex_impl.CompiledRegex)
                subject = regex_impl.Subject.decode(ctypes.string_at(s, length))
                m = regex_impl.search(compiled, subject, start)
                if m is None:
                    return 0
                first = subject.byte_offsets[m.start()]
                last = subject.byte_offsets[m.end()]
                ptr = api.aegi_malloc(8) or 0
                if ptr:
                    arr = ctypes.cast(ptr, POINTER(c_int))
                    arr[0] = first + 1
                    arr[1] = last
                return ptr
            except Exception as e:  # noqa: BLE001
                self._set_err(err, str(e))
                return 0

        def match(re: int, s: int, length: int, start: int, err: int) -> int:
            try:
                compiled = self._handles[re]
                assert isinstance(compiled, regex_impl.CompiledRegex)
                subject = regex_impl.Subject.decode(ctypes.string_at(s, length))
                m = regex_impl.search(compiled, subject, start)
                if m is None:
                    return 0
                rng = (c_int * 2)()
                return self._new_handle((compiled, subject, m, start, rng))
            except Exception as e:  # noqa: BLE001
                self._set_err(err, str(e))
                return 0

        def get_match(handle: int, idx: int) -> int:
            entry = self._handles.get(handle)
            if not isinstance(entry, tuple):
                return 0
            compiled, subject, m, start, rng = entry  # pyright: ignore[reportUnknownVariableType]
            assert isinstance(compiled, regex_impl.CompiledRegex)
            assert isinstance(subject, regex_impl.Subject)
            assert isinstance(start, int)
            span = regex_impl.group_span_bytes(m, subject, idx, compiled.nosubs)  # pyright: ignore[reportUnknownArgumentType]
            if span is None:
                return 0
            arr = rng  # pyright: ignore[reportUnknownVariableType]
            arr[0] = span[0] - start + 1
            arr[1] = span[1] - start
            return ctypes.addressof(arr)  # pyright: ignore[reportUnknownArgumentType]

        def match_free(handle: int) -> None:
            self._free_handle(handle)

        def replace(re: int, replacement: int, s: int, length: int, max_count: int, err: int) -> int:
            try:
                compiled = self._handles[re]
                assert isinstance(compiled, regex_impl.CompiledRegex)
                subject = regex_impl.Subject.decode(ctypes.string_at(s, length))
                out = regex_impl.replace(compiled, from_bytes(ctypes.string_at(replacement)), subject, max_count)
                return self._malloc_bytes(to_bytes(out))
            except Exception as e:  # noqa: BLE001
                self._set_err(err, str(e))
                return 0

        def get_flags() -> int:
            return flags_ptr

        vp, sz = c_void_p, c_size_t
        return LibSpec(
            ["agi_re_match", "u32regex"],
            [
                ("search", "int* (*)(u32regex&, char const*, unsigned long long, unsigned long long, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp, sz, sz, vp), search)),
                ("match", "agi_re_match* (*)(u32regex&, char const*, unsigned long long, int, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp, sz, c_int, vp), match)),
                ("get_match", "int* (*)(agi_re_match&, unsigned long long)",
                 self._callback(CFUNCTYPE(vp, vp, sz), get_match)),
                ("replace", "char* (*)(u32regex&, char const*, char const*, unsigned long long, int, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp, vp, sz, c_int, vp), replace)),
                ("compile", "u32regex* (*)(char const*, int, char**)",
                 self._callback(CFUNCTYPE(vp, vp, c_int, vp), compile_)),
                ("get_flags", "agi_re_flag const* (*)()", self._callback(CFUNCTYPE(vp), get_flags)),
                ("match_free", "void (*)(agi_re_match*)", self._callback(CFUNCTYPE(None, vp), match_free)),
                ("regex_free", "void (*)(u32regex*)", self._callback(CFUNCTYPE(None, vp), regex_free)),
            ],
        )

    # ----------------------------------------------------------------- aegisub.__unicode_impl

    def unicode_spec(self) -> LibSpec:
        def conv(fn: Callable[[str], str]) -> Callable[[int, int], int]:
            def impl(s: int, err: int) -> int:
                try:
                    text = ctypes.string_at(s).decode("utf-8", "replace")
                    return self._malloc_bytes(fn(text).encode("utf-8", "surrogatepass"))
                except Exception as e:  # noqa: BLE001
                    self._set_err(err, str(e))
                    return 0

            return impl

        proto = CFUNCTYPE(c_void_p, c_void_p, c_void_p)
        sig = "char* (*)(char const*, char**)"
        return LibSpec(
            [],
            [
                ("to_upper_case", sig, self._callback(proto, conv(str.upper))),
                ("to_lower_case", sig, self._callback(proto, conv(str.lower))),
                ("to_fold_case", sig, self._callback(proto, conv(str.casefold))),
            ],
        )

    # ----------------------------------------------------------------- aegisub.__lfs_impl

    def lfs_spec(self) -> LibSpec:
        def path_of(p: int) -> str:
            return from_bytes(ctypes.string_at(p))

        def fs_error(e: OSError, path: str) -> str:
            return f"{e.strerror or e}: \"{path}\""

        def setter(action: Callable[[str], None]) -> Callable[[int, int], bool]:
            def impl(p: int, err: int) -> bool:
                path = path_of(p)
                try:
                    action(path)
                    return True
                except OSError as e:
                    self._set_err(err, fs_error(e, path))
                except Exception as e:  # noqa: BLE001
                    self._set_err(err, str(e))
                return False

            return impl

        def mkdir(path: str) -> None:
            Path(path).mkdir(parents=True, exist_ok=True)

        def rmdir(path: str) -> None:
            p = Path(path)
            if p.is_dir() and not p.is_symlink():
                p.rmdir()
            else:
                p.unlink()

        def touch(path: str) -> None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
            now = time.time()
            os.utime(p, (os.stat(p).st_atime, now))

        def currentdir(err: int) -> int:
            try:
                return self._malloc_bytes(to_bytes(os.getcwd()))
            except OSError as e:
                self._set_err(err, str(e))
                return 0

        mode_names = {
            stat.S_IFREG: b"file", stat.S_IFDIR: b"directory", stat.S_IFLNK: b"link",
            stat.S_IFBLK: b"block device", stat.S_IFCHR: b"char device",
            stat.S_IFIFO: b"fifo", stat.S_IFSOCK: b"socket",
        }
        mode_ptrs: dict[bytes, int] = {}
        for name in [*mode_names.values(), b"other"]:
            buf = ctypes.create_string_buffer(name)
            self._keep.append(buf)
            mode_ptrs[name] = ctypes.addressof(buf)

        def get_mode(p: int, err: int) -> int:
            path = path_of(p)
            try:
                st = os.stat(path)
            except FileNotFoundError:
                return 0
            except OSError as e:
                self._set_err(err, fs_error(e, path))
                return 0
            return mode_ptrs[mode_names.get(stat.S_IFMT(st.st_mode), b"other")]

        def get_mtime(p: int, err: int) -> int:
            path = path_of(p)
            try:
                # duration_cast<seconds>(last_write_time(p).time_since_epoch()); MSVC's file clock
                # counts from 1601-01-01
                return int(os.stat(path).st_mtime) + _FILETIME_EPOCH_OFFSET
            except OSError as e:
                self._set_err(err, fs_error(e, path))
                return 0

        def get_size(p: int, err: int) -> int:
            path = path_of(p)
            try:
                if os.path.isdir(path):
                    self._set_err(err, f"{path} is not a file")
                    return 0
                return os.stat(path).st_size
            except OSError as e:
                self._set_err(err, fs_error(e, path))
                return 0

        def dir_new(p: int, err: int) -> int:
            path = path_of(p)
            try:
                names = os.listdir(path)
            except OSError as e:
                self._set_err(err, f"cannot open {path}: {e.strerror or e}")
                return 0
            entries = [n for n in names if not (n == "." or n.startswith(".."))]
            return self._new_handle(entries)

        def dir_next(it: int, err: int) -> int:
            entries = self._handles.get(it)
            if not isinstance(entries, list) or not entries:
                return 0
            name: str = entries.pop(0)  # pyright: ignore[reportUnknownVariableType]
            return self._malloc_bytes(to_bytes(name))  # pyright: ignore[reportUnknownArgumentType]

        def dir_close(it: int, err: int) -> None:
            entries = self._handles.get(it)
            if isinstance(entries, list):
                entries.clear()

        def dir_free(it: int, err: int) -> None:
            self._free_handle(it)

        vp = c_void_p
        bool_sig = "bool (*)(char const*, char**)"
        return LibSpec(
            ["DirectoryIterator"],
            [
                ("chdir", bool_sig, self._callback(CFUNCTYPE(c_bool, vp, vp), setter(os.chdir))),
                ("currentdir", "char* (*)(char**)", self._callback(CFUNCTYPE(vp, vp), currentdir)),
                ("mkdir", bool_sig, self._callback(CFUNCTYPE(c_bool, vp, vp), setter(mkdir))),
                ("rmdir", bool_sig, self._callback(CFUNCTYPE(c_bool, vp, vp), setter(rmdir))),
                ("touch", bool_sig, self._callback(CFUNCTYPE(c_bool, vp, vp), setter(touch))),
                ("get_mtime", "long long (*)(char const*, char**)",
                 self._callback(CFUNCTYPE(c_longlong, vp, vp), get_mtime)),
                ("get_mode", "char const* (*)(char const*, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp), get_mode)),
                ("get_size", "unsigned long long (*)(char const*, char**)",
                 self._callback(CFUNCTYPE(c_ulonglong, vp, vp), get_size)),
                ("dir_new", "DirectoryIterator* (*)(char const*, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp), dir_new)),
                ("dir_free", "void (*)(DirectoryIterator*, char**)",
                 self._callback(CFUNCTYPE(None, vp, vp), dir_free)),
                ("dir_next", "char* (*)(DirectoryIterator&, char**)",
                 self._callback(CFUNCTYPE(vp, vp, vp), dir_next)),
                ("dir_close", "void (*)(DirectoryIterator&, char**)",
                 self._callback(CFUNCTYPE(None, vp, vp), dir_close)),
            ],
        )
