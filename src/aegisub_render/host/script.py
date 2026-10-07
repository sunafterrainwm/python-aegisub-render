"""Automation 4 Lua script host (src/auto4_lua.cpp LuaScript / LuaCommand / LuaExportFilter)."""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from pathlib import Path

from aegisub_render.ass.entries import AssStyle
from aegisub_render.ass.file import AssFile
from aegisub_render.host.impls import FfiImpls
from aegisub_render.host.subs import Flat, SubsSession, as_flat, entry_from_flat
from aegisub_render.host.text_extents import TextExtents, register_private_fonts
from aegisub_render.lua.capi import LuaAPI
from aegisub_render.lua.state import NIL, LuaError, LuaRef, LuaState, PyFunc, PyFunction, ScriptError
from aegisub_render.output import Output
from aegisub_render.paths import AegisubPaths, join
from aegisub_render.textenc import from_bytes
from aegisub_render.video.context import VideoContext
from aegisub_render.video.vfr import START

BOOTSTRAP_CHUNKNAME = b"=aegisub-render"


class RunStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCEL = "cancel"


class ScriptLoadError(Exception):
    pass


@dataclass(slots=True)
class HostConfig:
    script_path: Path
    include_dirs: list[str]
    paths: AegisubPaths
    trace_level: int = 3
    memory_limit: int = 512_000_000
    max_output_lines: int = 50_000
    font_dirs: list[Path] = field(default_factory=list[Path])
    subtitle_filename: str | None = None


@dataclass(slots=True)
class Macro:
    index: int
    name: str
    help: str
    has_validate: bool


@dataclass(slots=True)
class Filter:
    index: int
    name: str
    description: str
    priority: int


@dataclass(slots=True)
class ScriptInfo:
    name: str
    description: str
    author: str
    version: str
    warnings: list[str]
    macros: list[Macro]
    filters: list[Filter]


def _str(v: object) -> str:
    if isinstance(v, bytes):
        return from_bytes(v)
    raise ScriptError(f"aegisub-render internal error: expected a string, got {type(v).__name__}")


def _num(v: object) -> float:
    if isinstance(v, float):
        return v
    raise ScriptError(f"aegisub-render internal error: expected a number, got {type(v).__name__}")


def _dict(v: object) -> dict[object, object]:
    if isinstance(v, dict):
        return v  # pyright: ignore[reportUnknownVariableType]
    return {}


def _list(v: object) -> list[object]:
    d = _dict(v)
    return [d[float(i)] for i in range(1, len(d) + 1) if float(i) in d]


class ScriptHost:
    def __init__(self, cfg: HostConfig, ass: AssFile, video: VideoContext, out: Output) -> None:
        self.cfg = cfg
        self.ass = ass
        self.video = video
        self.out = out
        self.api = LuaAPI()
        self.state = LuaState(self.api, cfg.memory_limit)
        self.impls = FfiImpls(self.api)
        self.text_extents = TextExtents()
        self.sessions: dict[int, SubsSession] = {}
        self._next_sid = 1
        if cfg.font_dirs:
            register_private_fonts(cfg.font_dirs)

        script = cfg.script_path
        support = Path(cfg.paths.decode("?data/automation/include/")) / "moonscript.lua"
        lua_cfg: dict[str, object] = {
            "include_dirs": cfg.include_dirs,
            "script_dir": str(script.parent),
            "script_stem": script.stem,
            "pretty_name": script.name,
            "moonscript_path": str(support),
            "moonscript_fallback": str(Path(str(resources.files("aegisub_render.host") / "lua" / "moonscript.lua"))),
            "trace_level": cfg.trace_level,
            "impls": {
                "re": self.impls.re_spec().to_lua(),
                "unicode": self.impls.unicode_spec().to_lua(),
                "lfs": self.impls.lfs_spec().to_lua(),
            },
        }
        code = (resources.files("aegisub_render.host") / "lua" / "bootstrap.lua").read_bytes()
        chunk = self.state.load(code, BOOTSTRAP_CHUNKNAME)
        py_table = self._python_functions()
        try:
            refs = self.state.call_for_refs(
                chunk, {k: PyFunction(f) for k, f in py_table.items()}, lua_cfg, handler=False
            )
        except LuaError as e:
            raise ScriptLoadError(f"could not initialise the Lua environment: {e.message}") from None
        (traceback_ref, self._load_script, self._features, self._validate_macro, self._run_macro, self._run_filter) = (
            refs
        )
        self.state.traceback_ref = traceback_ref.ref

    def close(self) -> None:
        self.state.close()

    # ----------------------------------------------------------------- Python functions for the bootstrap

    def _python_functions(self) -> dict[str, PyFunc]:
        out = self.out

        def read_file(path: object) -> tuple[bytes, str]:
            p = _str(path)
            try:
                canonical = os.path.realpath(p)
            except OSError:
                canonical = p
            try:
                data = Path(canonical).read_bytes()
            except FileNotFoundError:
                raise ScriptError(f"File not found: {p}") from None
            except IsADirectoryError:
                raise ScriptError(f"{p} is not a file") from None
            except OSError as e:
                raise ScriptError(f"Could not read {p}: {e.strerror or e}") from None
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            return data, canonical

        def file_exists(path: object) -> bool:
            return Path(_str(path)).is_file()

        def path_join(base: object, name: object) -> str:
            return join(_str(base), _str(name))

        def log(text: object) -> None:
            out.write(_str(text))

        def log_host(text: object) -> None:
            out.message(_str(text))

        def status(text: object) -> None:
            out.status(_str(text))

        def progress_set(pct: object) -> None:
            out.progress(percent=_num(pct))

        def progress_task(text: object) -> None:
            out.progress(task=_str(text))

        def progress_title(text: object) -> None:
            out.progress(title=_str(text))

        def progress_done() -> None:
            out.progress_done()

        def text_extents(entry: object, text: object) -> tuple[float, float, float, float]:
            style = entry_from_flat(as_flat(entry), self.ass)
            if not isinstance(style, AssStyle):
                raise ScriptError("Not a style entry")
            result = self.text_extents.measure(style, _str(text))
            for w in self.text_extents.warnings:
                if self.cfg.trace_level >= 2:
                    out.message(w)
            self.text_extents.warnings.clear()
            if result is None:
                raise ScriptError("Some internal error occurred calculating text_extents")
            return result

        def frame_from_ms(ms: object) -> object:
            tc = self.video.timecodes
            if not tc.is_loaded():
                return NIL
            return tc.frame_at_time(int(_num(ms)), START)

        def ms_from_frame(frame: object) -> object:
            tc = self.video.timecodes
            if not tc.is_loaded():
                return NIL
            return tc.time_at_frame(int(_num(frame)), START)

        def video_size() -> object:
            v = self.video
            if not v.loaded:
                return NIL
            return (v.width, v.height, v.ar_value, v.ar_type)

        def keyframes() -> list[int]:
            return list(self.video.keyframes)

        def decode_path(text: object) -> str:
            return self.cfg.paths.decode(_str(text))

        def file_name() -> object:
            return self.cfg.subtitle_filename if self.cfg.subtitle_filename else NIL

        def project_properties() -> dict[str, object]:
            p = self.ass.properties
            paths = self.cfg.paths
            return {
                "automation_scripts": p.automation_scripts,
                "export_filters": p.export_filters,
                "export_encoding": p.export_encoding,
                "style_storage": p.style_storage,
                "video_zoom": p.video_zoom,
                "ar_value": p.ar_value,
                "scroll_position": p.scroll_position,
                "active_row": p.active_row,
                "ar_mode": p.ar_mode,
                "video_position": p.video_position,
                "audio_file": paths.make_absolute(p.audio_file, "?script"),
                "video_file": paths.make_absolute(p.video_file, "?script"),
                "timecodes_file": paths.make_absolute(p.timecodes_file, "?script"),
                "keyframes_file": paths.make_absolute(p.keyframes_file, "?script"),
            }

        def session(sid: object) -> SubsSession:
            return self.sessions[int(_num(sid))]

        def subs_count(sid: object) -> int:
            return session(sid).count()

        def subs_read(sid: object, idx: object) -> tuple[object, ...]:
            return session(sid).read(int(_num(idx)))

        def subs_assign(sid: object, idx: object, entry: object) -> None:
            session(sid).assign(int(_num(idx)), as_flat(entry))

        def subs_append(sid: object, entry: object) -> None:
            session(sid).append(as_flat(entry))

        def subs_insert(sid: object, before: object, entries: object) -> None:
            flats: list[Flat] = [as_flat(e) for e in _list(entries)]
            session(sid).insert(int(_num(before)), flats)

        def subs_delete(sid: object, ids: object) -> None:
            session(sid).delete([int(_num(i)) for i in _list(ids)])

        def subs_deleterange(sid: object, a: object, b: object) -> None:
            session(sid).deleterange(int(_num(a)), int(_num(b)))

        def subs_resolution(sid: object) -> tuple[int, int]:
            return session(sid).resolution()

        def subs_karaoke(sid: object, entry: object) -> list[list[object]]:
            return session(sid).karaoke(as_flat(entry))

        def subs_modified(sid: object) -> bool:
            return session(sid).modified

        def subs_undo_point(sid: object, message: object) -> None:
            session(sid).undo_point()

        return {
            "read_file": read_file,
            "file_exists": file_exists,
            "path_join": path_join,
            "log": log,
            "log_host": log_host,
            "status": status,
            "progress_set": progress_set,
            "progress_task": progress_task,
            "progress_title": progress_title,
            "progress_done": progress_done,
            "text_extents": text_extents,
            "frame_from_ms": frame_from_ms,
            "ms_from_frame": ms_from_frame,
            "video_size": video_size,
            "keyframes": keyframes,
            "decode_path": decode_path,
            "file_name": file_name,
            "project_properties": project_properties,
            "subs_count": subs_count,
            "subs_read": subs_read,
            "subs_assign": subs_assign,
            "subs_append": subs_append,
            "subs_insert": subs_insert,
            "subs_delete": subs_delete,
            "subs_deleterange": subs_deleterange,
            "subs_resolution": subs_resolution,
            "subs_karaoke": subs_karaoke,
            "subs_modified": subs_modified,
            "subs_undo_point": subs_undo_point,
        }

    # ----------------------------------------------------------------- script operations

    def _new_session(self) -> int:
        sid = self._next_sid
        self._next_sid += 1
        self.sessions[sid] = SubsSession(self.ass, self.cfg.max_output_lines)
        return sid

    def load_script(self) -> ScriptInfo:
        result = self.state.call(self._load_script, str(self.cfg.script_path))
        ok, payload = result[0], result[1] if len(result) > 1 else None
        if ok is not True:
            raise ScriptLoadError(_str(payload) if isinstance(payload, bytes) else "unknown error")
        info = _dict(payload)
        features = _dict(self.state.call(self._features)[0])
        macros = [
            Macro(
                i + 1, _str(_dict(m).get(b"name")), _str(_dict(m).get(b"help", b"")), _dict(m).get(b"validate") is True
            )
            for i, m in enumerate(_list(features.get(b"macros")))
        ]
        filters = [
            Filter(
                i + 1,
                _str(_dict(f).get(b"name")),
                _str(_dict(f).get(b"description", b"")),
                int(_num(_dict(f).get(b"priority", 0.0))),
            )
            for i, f in enumerate(_list(features.get(b"filters")))
        ]
        return ScriptInfo(
            name=_str(info.get(b"name", b"")),
            description=_str(info.get(b"description", b"")),
            author=_str(info.get(b"author", b"")),
            version=_str(info.get(b"version", b"")),
            warnings=[_str(w) for w in _list(info.get(b"warnings"))],
            macros=macros,
            filters=filters,
        )

    def validate_macro(self, macro: Macro, selected: list[int], active: int) -> bool:
        sid = self._new_session()
        try:
            result = self.state.call(self._validate_macro, macro.index, sid, selected, active if active else NIL)
        finally:
            del self.sessions[sid]
        return bool(result) and result[0] is True

    def run_macro(self, macro: Macro, selected: list[int], active: int) -> RunStatus:
        sid = self._new_session()
        try:
            result = self.state.call(self._run_macro, macro.index, sid, selected, active)
            status = RunStatus(_str(result[0]))
            if status is RunStatus.OK:
                self.sessions[sid].apply()
            return status
        finally:
            del self.sessions[sid]

    def run_filter(self, flt: Filter) -> RunStatus:
        sid = self._new_session()
        try:
            result = self.state.call(self._run_filter, flt.index, sid)
            status = RunStatus(_str(result[0]))
            if status is RunStatus.OK:
                self.sessions[sid].apply()
            return status
        finally:
            del self.sessions[sid]


__all__ = ["HostConfig", "LuaRef", "Macro", "RunStatus", "ScriptHost", "ScriptInfo", "ScriptLoadError"]
