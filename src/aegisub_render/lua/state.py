"""A LuaJIT state driven through ctypes.

Rules that keep Lua errors from ever unwinding through Python frames:

* Python only calls Lua API functions that cannot raise Lua errors (stack manipulation,
  pushes, raw table access, lua_pcall/lua_cpcall/luaL_loadbuffer which are protected).
  Allocation failures are the one remaining source, so the memory limit is suspended while
  Python code runs and is enforced again once Lua code allocates.
* Python functions exposed to Lua go through a single C dispatcher. It never raises: it
  returns ``true, results...`` or ``false, message`` and a small Lua wrapper (see
  host/lua/bootstrap.lua) turns the latter into ``error(message, 0)``.
"""

import ctypes
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from aegisub_render.lua import capi
from aegisub_render.lua.capi import LuaAPI, lua_CFunction, lua_Debug
from aegisub_render.textenc import from_bytes, to_bytes


class LuaError(Exception):
    """An error raised by Lua code and caught by lua_pcall."""

    def __init__(self, status: int, value: object) -> None:
        self.status = status
        self.value = value
        if isinstance(value, bytes):
            text = from_bytes(value)
        elif value is None:
            text = "nil"
        else:
            text = repr(value)
        super().__init__(text)

    @property
    def message(self) -> str:
        return str(self)


class ScriptError(Exception):
    """Raised by a Python function exposed to Lua: becomes error(message, 0) in Lua."""


class ArgError(ScriptError):
    """luaL_argerror-style error; the message is completed with the calling function's name."""

    def __init__(self, narg: int, extramsg: str) -> None:
        super().__init__(extramsg)
        self.narg = narg
        self.extramsg = extramsg


class CancelScript(Exception):  # noqa: N818 - mirrors aegisub.cancel()
    """Raise a Lua error whose value is nil (what aegisub.cancel() does)."""


class _Nil:
    _instance: "_Nil | None" = None

    def __new__(cls) -> "_Nil":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NIL"


NIL = _Nil()


@dataclass(frozen=True, slots=True)
class LuaRef:
    """A value pinned in the Lua registry."""

    ref: int


@dataclass(frozen=True, slots=True)
class LightUserdata:
    address: int


@dataclass(frozen=True, slots=True)
class CFunction:
    """A raw lua_CFunction pointer to push as a C closure without upvalues."""

    address: int


@dataclass(frozen=True, slots=True)
class PyFunction:
    """A Python callable to push as the raw dispatcher closure (wrap it in Lua before exposing it)."""

    func: "PyFunc"


@dataclass(frozen=True, slots=True)
class LuaOpaque:
    """A Lua value Python does not convert (function, userdata, thread, cdata)."""

    type_name: str


type PyFunc = Callable[..., object]


def _type_name(api: LuaAPI, L: int, t: int) -> str:  # noqa: N803
    name = api.lua_typename(L, t)
    return name.decode() if name else "no value"


class LuaState:
    def __init__(self, api: LuaAPI, memory_limit: int) -> None:
        self.api = api
        handle = api.aegi_newstate(memory_limit)
        if not handle:
            raise MemoryError("could not create a Lua state")
        self.L: int = handle
        self._funcs: list[PyFunc] = []
        self._pending: BaseException | None = None
        self._dispatcher = lua_CFunction(self._dispatch)
        self._dispatcher_ptr: int = ctypes.cast(self._dispatcher, ctypes.c_void_p).value or 0
        self.traceback_ref: int | None = None
        status = api.lua_cpcall(self.L, api.aegi_openlibs_ptr, None)
        if status != capi.LUA_OK:
            raise LuaError(status, self.to_python(self.L, -1))

    def close(self) -> None:
        if self.L:
            self.api.aegi_close(self.L)
            self.L = 0

    @property
    def memory_used(self) -> int:
        return int(self.api.aegi_memory_used(self.L))

    @property
    def memory_peak(self) -> int:
        return int(self.api.aegi_memory_peak(self.L))

    @property
    def memory_limit_hit(self) -> bool:
        return bool(self.api.aegi_memory_limit_hit(self.L))

    # ----------------------------------------------------------------- conversion

    def to_python(self, L: int, idx: int, depth: int = 0) -> object:  # noqa: N803
        api = self.api
        t = api.lua_type(L, idx)
        if t in (capi.LUA_TNIL, capi.LUA_TNONE):
            return None
        if t == capi.LUA_TBOOLEAN:
            return bool(api.lua_toboolean(L, idx))
        if t == capi.LUA_TNUMBER:
            return float(api.lua_tonumber(L, idx))
        if t == capi.LUA_TSTRING:
            size = ctypes.c_size_t()
            ptr = api.lua_tolstring(L, idx, ctypes.byref(size))
            return ctypes.string_at(ptr, size.value) if ptr else b""
        if t == capi.LUA_TLIGHTUSERDATA:
            return LightUserdata(api.lua_touserdata(L, idx) or 0)
        if t == capi.LUA_TTABLE and depth < 16:
            if idx < 0 and idx > capi.LUA_REGISTRYINDEX:
                idx = api.lua_gettop(L) + idx + 1
            result: dict[object, object] = {}
            api.lua_checkstack(L, 4)
            api.lua_pushnil(L)
            while api.lua_next(L, idx):
                key = self.to_python(L, -2, depth + 1)
                result[key] = self.to_python(L, -1, depth + 1)
                api.lua_settop(L, -2)
            return result
        return LuaOpaque(_type_name(api, L, t))

    def push(self, L: int, value: object, depth: int = 0) -> None:  # noqa: N803
        api = self.api
        if depth > 64:
            raise RecursionError("value nested too deeply to convert to Lua")
        api.lua_checkstack(L, 3)
        match value:
            case None | _Nil():
                api.lua_pushnil(L)
            case bool():
                api.lua_pushboolean(L, int(value))
            case int() | float():
                api.lua_pushnumber(L, float(value))
            case str():
                data = to_bytes(value)
                api.lua_pushlstring(L, data, len(data))
            case bytes():
                api.lua_pushlstring(L, value, len(value))
            case LuaRef(ref=ref):
                api.lua_rawgeti(L, capi.LUA_REGISTRYINDEX, ref)
            case LightUserdata(address=address):
                api.lua_pushlightuserdata(L, address)
            case CFunction(address=address):
                api.lua_pushcclosure(L, address, 0)
            case PyFunction(func=func):
                self.push_function(L, func)
            case list() | tuple():
                items: list[object] | tuple[object, ...] = value  # pyright: ignore[reportUnknownVariableType]
                api.lua_createtable(L, len(items), 0)
                for i, item in enumerate(items, 1):
                    self.push(L, item, depth + 1)
                    api.lua_rawseti(L, -2, i)
            case dict():
                mapping: dict[object, object] = value  # pyright: ignore[reportUnknownVariableType]
                api.lua_createtable(L, 0, len(mapping))
                for k, v in mapping.items():
                    self.push(L, k, depth + 1)
                    self.push(L, v, depth + 1)
                    api.lua_rawset(L, -3)
            case _:
                raise TypeError(f"cannot convert {type(value).__name__} to a Lua value")

    # ----------------------------------------------------------------- Python -> Lua functions

    def push_function(self, L: int, func: PyFunc) -> None:  # noqa: N803
        """Push the raw dispatcher closure for func; wrap it on the Lua side before exposing it."""
        self._funcs.append(func)
        self.api.lua_pushnumber(L, float(len(self._funcs) - 1))
        self.api.lua_pushcclosure(L, self._dispatcher_ptr, 1)

    def _caller_name(self, L: int) -> tuple[str, str]:  # noqa: N803
        """Name and namewhat of the Lua wrapper that called the dispatcher (stack level 1)."""
        ar = lua_Debug()
        if not self.api.lua_getstack(L, 1, ctypes.byref(ar)):
            return "?", ""
        self.api.lua_getinfo(L, b"n", ctypes.byref(ar))
        name = from_bytes(ar.name) if ar.name else "?"
        namewhat = from_bytes(ar.namewhat) if ar.namewhat else ""
        return name, namewhat

    def _argerror_message(self, L: int, narg: int, extramsg: str) -> str:  # noqa: N803
        name, namewhat = self._caller_name(L)
        if namewhat == "method":
            narg -= 1
            if narg == 0:
                return f"calling '{name}' on bad self ({extramsg})"
        return f"bad argument #{narg} to '{name}' ({extramsg})"

    def _dispatch(self, L: int) -> int:  # noqa: N803
        api = self.api
        api.aegi_suspend_limit(L, 1)
        try:
            try:
                fid = int(api.lua_tonumber(L, capi.upvalueindex(1)))
                func = self._funcs[fid]
                args = [self.to_python(L, i) for i in range(1, api.lua_gettop(L) + 1)]
                result = func(*args)
            except ArgError as e:
                return self._fail(L, self._argerror_message(L, e.narg, e.extramsg))
            except ScriptError as e:
                return self._fail(L, str(e))
            except CancelScript:
                api.lua_settop(L, 0)
                api.lua_pushboolean(L, 0)
                return 1
            except Exception as e:  # a bug in the host: report it to the script as an error
                return self._fail(L, "aegisub-render internal error: " + "".join(
                    traceback.format_exception_only(e)).strip() + "\n" + traceback.format_exc())
            except BaseException as e:  # KeyboardInterrupt etc.: re-raised once back in Python
                self._pending = e
                return self._fail(L, "interrupted")
            api.lua_settop(L, 0)
            api.lua_pushboolean(L, 1)
            if result is None:
                return 1
            values: tuple[object, ...] = result if isinstance(result, tuple) else (result,)  # pyright: ignore[reportUnknownVariableType]
            api.lua_checkstack(L, len(values) + 4)
            for v in values:
                self.push(L, v)
            return 1 + len(values)
        except BaseException as e:  # conversion failure; never let anything escape into C
            self._pending = self._pending or e
            api.lua_settop(L, 0)
            api.lua_pushboolean(L, 0)
            return 1
        finally:
            api.aegi_suspend_limit(L, 0)

    def _fail(self, L: int, message: str) -> int:  # noqa: N803
        self.api.lua_settop(L, 0)
        self.api.lua_pushboolean(L, 0)
        self.push(L, message)
        return 2

    def raise_pending(self) -> None:
        if self._pending is not None:
            exc, self._pending = self._pending, None
            raise exc

    # ----------------------------------------------------------------- calling into Lua

    def load(self, code: bytes, chunkname: bytes) -> LuaRef:
        """luaL_loadbuffer; returns a registry ref to the compiled chunk."""
        api, L = self.api, self.L
        api.aegi_suspend_limit(L, 1)
        try:
            status = api.luaL_loadbuffer(L, code, len(code), chunkname)
            if status != capi.LUA_OK:
                err = self.to_python(L, -1)
                api.lua_settop(L, -2)
                raise LuaError(status, err)
            return LuaRef(api.luaL_ref(L, capi.LUA_REGISTRYINDEX))
        finally:
            api.aegi_suspend_limit(L, 0)

    def call(self, func: LuaRef, *args: object, handler: bool = True) -> tuple[object, ...]:
        """lua_pcall func(*args); results are converted to Python values."""
        return self._call(func, args, handler, refs=False)

    def call_for_refs(self, func: LuaRef, *args: object, handler: bool = True) -> tuple[LuaRef, ...]:
        """lua_pcall func(*args); every result is pinned in the registry instead of converted."""
        return tuple(r for r in self._call(func, args, handler, refs=True) if isinstance(r, LuaRef))

    def _call(self, func: LuaRef, args: tuple[object, ...], handler: bool, refs: bool) -> tuple[object, ...]:
        api, L = self.api, self.L
        top = api.lua_gettop(L)
        api.aegi_suspend_limit(L, 1)
        try:
            api.lua_checkstack(L, len(args) + 4)
            errfunc = 0
            if handler and self.traceback_ref is not None:
                api.lua_rawgeti(L, capi.LUA_REGISTRYINDEX, self.traceback_ref)
                errfunc = top + 1
            self.push(L, func)
            for a in args:
                self.push(L, a)
        finally:
            api.aegi_suspend_limit(L, 0)
        status = api.lua_pcall(L, len(args), capi.LUA_MULTRET, errfunc)
        api.aegi_suspend_limit(L, 1)
        try:
            if status != capi.LUA_OK:
                err = self.to_python(L, -1)
                api.lua_settop(L, top)
                self.raise_pending()
                raise LuaError(status, err)
            first = top + (2 if errfunc else 1)
            if refs:
                results_list: list[object] = []
                while api.lua_gettop(L) >= first:
                    api.lua_pushvalue(L, first)
                    api.lua_remove(L, first)
                    results_list.append(LuaRef(api.luaL_ref(L, capi.LUA_REGISTRYINDEX)))
                results = tuple(results_list)
            else:
                results = tuple(self.to_python(L, i) for i in range(first, api.lua_gettop(L) + 1))
            api.lua_settop(L, top)
        finally:
            api.aegi_suspend_limit(L, 0)
        self.raise_pending()
        return results

    def ref(self, value: object) -> LuaRef:
        api, L = self.api, self.L
        api.aegi_suspend_limit(L, 1)
        try:
            self.push(L, value)
            return LuaRef(api.luaL_ref(L, capi.LUA_REGISTRYINDEX))
        finally:
            api.aegi_suspend_limit(L, 0)
