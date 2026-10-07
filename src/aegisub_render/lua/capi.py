"""ctypes declarations for aegisub_lua.dll (LuaJIT 2.1 C API + the aegisub-render shim)."""

import ctypes
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    c_char,
    c_char_p,
    c_double,
    c_int,
    c_size_t,
    c_ssize_t,
    c_void_p,
)
from pathlib import Path

DLL_PATH = Path(__file__).resolve().parent.parent / "_native" / "aegisub_lua.dll"

LUA_REGISTRYINDEX = -10000
LUA_ENVIRONINDEX = -10001
LUA_GLOBALSINDEX = -10002
LUA_MULTRET = -1

LUA_TNONE = -1
LUA_TNIL = 0
LUA_TBOOLEAN = 1
LUA_TLIGHTUSERDATA = 2
LUA_TNUMBER = 3
LUA_TSTRING = 4
LUA_TTABLE = 5
LUA_TFUNCTION = 6
LUA_TUSERDATA = 7
LUA_TTHREAD = 8
LUA_TCDATA = 10

LUA_OK = 0
LUA_ERRRUN = 2
LUA_ERRSYNTAX = 3
LUA_ERRMEM = 4
LUA_ERRERR = 5

LUA_GCCOLLECT = 2

LUA_IDSIZE = 60


def upvalueindex(i: int) -> int:
    return LUA_GLOBALSINDEX - i


# Declared as returning a 64-bit value on purpose: ctypes only writes sizeof(restype) bytes of
# the callback's return slot and libffi returns the whole register, while LuaJIT's VM uses the
# full RAX of a lua_CFunction's int result as an index. A c_int restype leaves the upper half
# undefined and LuaJIT then reads results from a garbage address.
lua_CFunction = CFUNCTYPE(c_ssize_t, c_void_p)


class lua_Debug(Structure):
    _fields_ = (
        ("event", c_int),
        ("name", c_char_p),
        ("namewhat", c_char_p),
        ("what", c_char_p),
        ("source", c_char_p),
        ("currentline", c_int),
        ("nups", c_int),
        ("linedefined", c_int),
        ("lastlinedefined", c_int),
        ("short_src", c_char * LUA_IDSIZE),
        ("i_ci", c_int),
    )


class LuaAPI:
    """Typed handle on the DLL's exports."""

    def __init__(self, path: Path = DLL_PATH) -> None:
        dll = ctypes.CDLL(str(path))
        self.dll = dll

        def fn(name: str, restype: object, *argtypes: object) -> ctypes._CFunctionType:
            f = getattr(dll, name)
            f.restype = restype
            f.argtypes = argtypes
            return f

        L = c_void_p
        self.aegi_newstate = fn("aegi_newstate", c_void_p, c_size_t)
        self.aegi_close = fn("aegi_close", None, L)
        self.aegi_suspend_limit = fn("aegi_suspend_limit", None, L, c_int)
        self.aegi_memory_used = fn("aegi_memory_used", c_size_t, L)
        self.aegi_memory_peak = fn("aegi_memory_peak", c_size_t, L)
        self.aegi_memory_limit_hit = fn("aegi_memory_limit_hit", c_int, L)
        self.aegi_malloc = fn("aegi_malloc", c_void_p, c_size_t)
        self.aegi_free = fn("aegi_free", None, c_void_p)
        self.aegi_openlibs_ptr = ctypes.cast(dll.aegi_openlibs, c_void_p).value

        self.lua_cpcall = fn("lua_cpcall", c_int, L, c_void_p, c_void_p)
        self.lua_gettop = fn("lua_gettop", c_int, L)
        self.lua_settop = fn("lua_settop", None, L, c_int)
        self.lua_pushvalue = fn("lua_pushvalue", None, L, c_int)
        self.lua_insert = fn("lua_insert", None, L, c_int)
        self.lua_remove = fn("lua_remove", None, L, c_int)
        self.lua_checkstack = fn("lua_checkstack", c_int, L, c_int)
        self.lua_type = fn("lua_type", c_int, L, c_int)
        self.lua_typename = fn("lua_typename", c_char_p, L, c_int)
        self.lua_tonumber = fn("lua_tonumber", c_double, L, c_int)
        self.lua_toboolean = fn("lua_toboolean", c_int, L, c_int)
        self.lua_tolstring = fn("lua_tolstring", c_void_p, L, c_int, POINTER(c_size_t))
        self.lua_touserdata = fn("lua_touserdata", c_void_p, L, c_int)
        self.lua_objlen = fn("lua_objlen", c_size_t, L, c_int)
        self.lua_pushnil = fn("lua_pushnil", None, L)
        self.lua_pushnumber = fn("lua_pushnumber", None, L, c_double)
        self.lua_pushinteger = fn("lua_pushinteger", None, L, c_ssize_t)
        self.lua_pushlstring = fn("lua_pushlstring", None, L, c_char_p, c_size_t)
        self.lua_pushboolean = fn("lua_pushboolean", None, L, c_int)
        self.lua_pushlightuserdata = fn("lua_pushlightuserdata", None, L, c_void_p)
        self.lua_pushcclosure = fn("lua_pushcclosure", None, L, c_void_p, c_int)
        self.lua_createtable = fn("lua_createtable", None, L, c_int, c_int)
        self.lua_rawget = fn("lua_rawget", None, L, c_int)
        self.lua_rawgeti = fn("lua_rawgeti", None, L, c_int, c_int)
        self.lua_rawset = fn("lua_rawset", None, L, c_int)
        self.lua_rawseti = fn("lua_rawseti", None, L, c_int, c_int)
        self.lua_next = fn("lua_next", c_int, L, c_int)
        self.lua_pcall = fn("lua_pcall", c_int, L, c_int, c_int, c_int)
        self.lua_gc = fn("lua_gc", c_int, L, c_int, c_int)
        self.luaL_loadbuffer = fn("luaL_loadbuffer", c_int, L, c_char_p, c_size_t, c_char_p)
        self.luaL_ref = fn("luaL_ref", c_int, L, c_int)
        self.luaL_unref = fn("luaL_unref", None, L, c_int, c_int)
        self.lua_getstack = fn("lua_getstack", c_int, L, c_int, POINTER(lua_Debug))
        self.lua_getinfo = fn("lua_getinfo", c_int, L, c_char_p, POINTER(lua_Debug))

        self.memmove = ctypes.memmove
