"""Values and exceptions exchanged with the Lua state (see _luajit.pyx)."""

from collections.abc import Callable
from dataclasses import dataclass

from aegisub_render.textenc import from_bytes


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
    """Raised by a Python function exposed to Lua: the message becomes the Lua error value.

    Like a C++ exception turned into a Lua error by Aegisub's exception_wrapper, the message is
    raised as is, without position information.
    """


class LocatedError(ScriptError):
    """Like agi::lua::error(): the message is prefixed with the position of the calling Lua code."""


class ArgError(ScriptError):
    """agi::lua::argerror(): "bad argument #n to 'name' (extramsg)", with the caller's position."""

    def __init__(self, narg: int, extramsg: str) -> None:
        super().__init__(extramsg)
        self.narg = narg
        self.extramsg = extramsg


class CancelScript(Exception):
    """Raise a Lua error whose value is nil (what aegisub.cancel() does)."""


class _Nil:
    _instance: _Nil | None = None

    def __new__(cls) -> _Nil:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NIL"


NIL = _Nil()
"""Return this from a Python function to give Lua one nil result (None gives no results)."""


@dataclass(frozen=True, slots=True)
class LuaRef:
    """A value pinned in the Lua registry."""

    ref: int


@dataclass(frozen=True, slots=True)
class LightUserdata:
    address: int


@dataclass(frozen=True, slots=True)
class LuaOpaque:
    """A Lua value Python does not convert (function, userdata, thread, cdata)."""

    type_name: str


type PyFunc = Callable[..., object]


@dataclass(frozen=True, slots=True)
class PyFunction:
    """A Python callable to push as a Lua C function."""

    func: PyFunc
