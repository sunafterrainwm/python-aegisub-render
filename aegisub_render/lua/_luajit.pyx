# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""A LuaJIT state driven from Python.

LuaJIT, lpeg, luabins and the shim are linked into this extension module (see native/).

Lua errors must never unwind through frames that hold Python state, so:

* Python-facing methods only use Lua API functions that cannot raise (stack manipulation,
  pushes, raw table access, and the protected lua_pcall / lua_cpcall / luaL_loadbuffer).
  Allocation failure is the one remaining source, so the memory limit is suspended while
  Python code runs and is enforced again once Lua code allocates.
* Python functions are exposed through `_dispatch_entry`, a C function without Python state:
  it calls the Python side (which always returns normally) and only then, if that reported
  an error, raises it with lua_error.
"""

from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_FromStringAndSize, PyBytes_GET_SIZE
from cpython.ref cimport Py_DECREF, Py_INCREF
from libc.stdlib cimport free, malloc
from libc.string cimport memcpy

import traceback

from aegisub_render.lua.values import (
    NIL,
    ArgError,
    CancelScript,
    LightUserdata,
    LocatedError,
    LuaError,
    LuaOpaque,
    LuaRef,
    PyFunction,
    ScriptError,
)

cdef enum DispatchStatus:
    RAISE_LOCATED = -1   # message on the stack, prefix it with luaL_where(L, 1)
    RAISE_PLAIN = -2     # message on the stack, raise it as is
    RAISE_NIL = -3       # aegisub.cancel(): raise nil
    RAISE_ARG = -4       # narg and extramsg on the stack: agi::lua::argerror


cdef int _dispatch_entry(lua_State *L) noexcept nogil:
    cdef int status
    cdef int narg
    cdef const char *extramsg
    with gil:
        status = (<LuaState>lua_touserdata(L, lua_upvalueindex(1)))._dispatch(L)
    if status >= 0:
        return status
    if status == RAISE_LOCATED:
        return aegi_error(L)
    if status == RAISE_PLAIN:
        return lua_error(L)
    if status == RAISE_NIL:
        lua_settop(L, 0)
        lua_pushnil(L)
        return lua_error(L)
    narg = <int>lua_tointeger(L, -2)
    extramsg = lua_tolstring(L, -1, NULL)
    return aegi_argerror(L, narg, extramsg)


cdef inline bytes _utf8(str text):
    return text.encode("utf-8", "surrogateescape")


cdef class LuaState:
    """One Lua state (aegi_newstate) with the standard libraries, lpeg and luabins loaded."""

    def __cinit__(self, size_t memory_limit=0):
        self.L = aegi_newstate(memory_limit)
        if self.L is NULL:
            raise MemoryError("could not create a Lua state")
        self._funcs = []
        self._pending = None
        self.traceback_ref = None
        cdef int status = lua_cpcall(self.L, aegi_openlibs, NULL)
        if status != 0:
            raise LuaError(status, self._to_python(self.L, -1, 0))

    def __dealloc__(self):
        if self.L is not NULL:
            aegi_close(self.L)
            self.L = NULL

    def close(self):
        if self.L is not NULL:
            aegi_close(self.L)
            self.L = NULL

    @property
    def memory_used(self):
        return aegi_memory_used(self.L)

    @property
    def memory_peak(self):
        return aegi_memory_peak(self.L)

    @property
    def memory_limit_hit(self):
        return aegi_memory_limit_hit(self.L) != 0

    # ------------------------------------------------------------------ conversion

    cdef object _to_python(self, lua_State *L, int idx, int depth):
        cdef int t = lua_type(L, idx)
        cdef size_t size
        cdef const char *s
        if t == LUA_TNIL or t == LUA_TNONE:
            return None
        if t == LUA_TBOOLEAN:
            return lua_toboolean(L, idx) != 0
        if t == LUA_TNUMBER:
            return lua_tonumber(L, idx)
        if t == LUA_TSTRING:
            s = lua_tolstring(L, idx, &size)
            return PyBytes_FromStringAndSize(s, size)
        if t == LUA_TLIGHTUSERDATA:
            return LightUserdata(<size_t>lua_touserdata(L, idx))
        if t == LUA_TTABLE and depth < 16:
            if idx < 0 and idx > LUA_REGISTRYINDEX:
                idx = lua_gettop(L) + idx + 1
            result = {}
            lua_checkstack(L, 4)
            lua_pushnil(L)
            while lua_next(L, idx) != 0:
                key = self._to_python(L, -2, depth + 1)
                result[key] = self._to_python(L, -1, depth + 1)
                lua_pop(L, 1)
            return result
        return LuaOpaque(lua_typename(L, t).decode())

    cdef int _push(self, lua_State *L, object value, int depth) except -1:
        cdef bytes data
        cdef Py_ssize_t i
        if depth > 64:
            raise RecursionError("value nested too deeply to convert to Lua")
        lua_checkstack(L, 3)
        if value is None or value is NIL:
            lua_pushnil(L)
        elif value is True or value is False:
            lua_pushboolean(L, value is True)
        elif isinstance(value, (int, float)):
            lua_pushnumber(L, <double>value)
        elif isinstance(value, str):
            data = _utf8(value)
            lua_pushlstring(L, PyBytes_AS_STRING(data), PyBytes_GET_SIZE(data))
        elif isinstance(value, bytes):
            lua_pushlstring(L, PyBytes_AS_STRING(value), PyBytes_GET_SIZE(value))
        elif isinstance(value, (list, tuple)):
            lua_createtable(L, len(value), 0)
            for i in range(len(value)):
                self._push(L, value[i], depth + 1)
                lua_rawseti(L, -2, <int>(i + 1))
        elif isinstance(value, dict):
            lua_createtable(L, 0, len(value))
            for k, v in value.items():
                self._push(L, k, depth + 1)
                self._push(L, v, depth + 1)
                lua_rawset(L, -3)
        elif isinstance(value, LuaRef):
            lua_rawgeti(L, LUA_REGISTRYINDEX, value.ref)
        elif isinstance(value, LightUserdata):
            lua_pushlightuserdata(L, <void *><size_t>value.address)
        elif isinstance(value, PyFunction):
            self._funcs.append(value.func)
            lua_pushlightuserdata(L, <void *>self)
            lua_pushinteger(L, len(self._funcs) - 1)
            lua_pushcclosure(L, _dispatch_entry, 2)
        else:
            raise TypeError(f"cannot convert {type(value).__name__} to a Lua value")
        return 0

    # ------------------------------------------------------------------ Lua -> Python calls

    cdef int _raise(self, lua_State *L, str message, int status) except? -999:
        lua_settop(L, 0)
        self._push(L, message, 0)
        return status

    cdef int _dispatch(self, lua_State *L) noexcept:
        cdef int n, i
        aegi_suspend_limit(L, 1)
        try:
            try:
                func = self._funcs[lua_tointeger(L, lua_upvalueindex(2))]
                n = lua_gettop(L)
                args = [self._to_python(L, i, 0) for i in range(1, n + 1)]
                result = func(*args)
            except ArgError as e:
                lua_settop(L, 0)
                lua_pushinteger(L, e.narg)
                self._push(L, e.extramsg, 0)
                return RAISE_ARG
            except LocatedError as e:
                return self._raise(L, str(e), RAISE_LOCATED)
            except ScriptError as e:
                return self._raise(L, str(e), RAISE_PLAIN)
            except CancelScript:
                return RAISE_NIL
            except Exception as e:  # a bug in the host: report it to the script as an error
                return self._raise(
                    L,
                    "aegisub-render internal error: " + "".join(traceback.format_exception_only(e)).strip()
                    + "\n" + traceback.format_exc(),
                    RAISE_PLAIN,
                )
            except BaseException as e:  # KeyboardInterrupt etc.: re-raised once back in Python
                self._pending = e
                return self._raise(L, "interrupted", RAISE_PLAIN)
            lua_settop(L, 0)
            if result is None:
                return 0
            if type(result) is tuple:
                lua_checkstack(L, len(result) + 4)
                for v in result:
                    self._push(L, v, 0)
                return len(result)
            self._push(L, result, 0)
            return 1
        except BaseException as e:  # a value could not be converted; never let anything escape into C
            if self._pending is None:
                self._pending = e
            lua_settop(L, 0)
            lua_pushstring(L, b"aegisub-render internal error while converting values")
            return RAISE_PLAIN
        finally:
            aegi_suspend_limit(L, 0)

    def raise_pending(self):
        if self._pending is not None:
            exc, self._pending = self._pending, None
            raise exc

    # ------------------------------------------------------------------ Python -> Lua calls

    def load(self, bytes code not None, bytes chunkname not None):
        """luaL_loadbuffer; returns a registry reference to the compiled chunk."""
        cdef lua_State *L = self.L
        cdef int status
        aegi_suspend_limit(L, 1)
        try:
            status = luaL_loadbuffer(L, PyBytes_AS_STRING(code), PyBytes_GET_SIZE(code), PyBytes_AS_STRING(chunkname))
            if status != 0:
                err = self._to_python(L, -1, 0)
                lua_pop(L, 1)
                raise LuaError(status, err)
            return LuaRef(luaL_ref(L, LUA_REGISTRYINDEX))
        finally:
            aegi_suspend_limit(L, 0)

    def ref(self, value):
        cdef lua_State *L = self.L
        aegi_suspend_limit(L, 1)
        try:
            self._push(L, value, 0)
            return LuaRef(luaL_ref(L, LUA_REGISTRYINDEX))
        finally:
            aegi_suspend_limit(L, 0)

    def call(self, func, *args, bint handler=True):
        """lua_pcall func(*args); results are converted to Python values."""
        return self._call(func, args, handler, False)

    def call_for_refs(self, func, *args, bint handler=True):
        """lua_pcall func(*args); every result is pinned in the registry instead of converted."""
        return self._call(func, args, handler, True)

    cdef tuple _call(self, object func, tuple args, bint handler, bint refs):
        cdef lua_State *L = self.L
        cdef int top = lua_gettop(L)
        cdef int errfunc = 0
        cdef int status, first, i
        aegi_suspend_limit(L, 1)
        try:
            lua_checkstack(L, len(args) + 4)
            if handler and self.traceback_ref is not None:
                lua_rawgeti(L, LUA_REGISTRYINDEX, self.traceback_ref)
                errfunc = top + 1
            self._push(L, func, 0)
            for a in args:
                self._push(L, a, 0)
        except BaseException:
            lua_settop(L, top)
            raise
        finally:
            aegi_suspend_limit(L, 0)

        status = lua_pcall(L, len(args), LUA_MULTRET, errfunc)

        aegi_suspend_limit(L, 1)
        try:
            if status != 0:
                err = self._to_python(L, -1, 0)
                lua_settop(L, top)
                self.raise_pending()
                raise LuaError(status, err)
            first = top + (2 if errfunc else 1)
            results = []
            if refs:
                while lua_gettop(L) >= first:
                    lua_pushvalue(L, first)
                    lua_remove(L, first)
                    results.append(LuaRef(luaL_ref(L, LUA_REGISTRYINDEX)))
            else:
                for i in range(first, lua_gettop(L) + 1):
                    results.append(self._to_python(L, i, 0))
            lua_settop(L, top)
        finally:
            aegi_suspend_limit(L, 0)
        self.raise_pending()
        return tuple(results)


# ---------------------------------------------------------------------------------------------
# C function tables for aegisub.__re_impl / __unicode_impl / __lfs_impl.
#
# Aegisub exports these from aegisub.exe (libaegisub/lua/modules/{re,unicode,lfs}.cpp) and the
# .moon wrappers in automation/include/aegisub call them through the ffi. Here each is a C
# function with the same signature that calls into a Python object (see host/impls.py).
# Errors are reported through the `char **err` out-parameter as in the C++ code, opaque handles
# are owned references to Python objects, and returned strings are malloc'ed by this module's
# CRT, which is what ffi.C.free resolves to.

ctypedef struct agi_re_flag:
    const char *name
    int value

cdef object _impl = None
cdef agi_re_flag *_flags = NULL
cdef list _flag_names = []
cdef dict _mode_names = {}


cdef class _Match:
    cdef object match
    cdef int range[2]


cdef char *_dup(bytes data) noexcept:
    cdef Py_ssize_t n = PyBytes_GET_SIZE(data)
    cdef char *out = <char *>malloc(n + 1)
    if out is not NULL:
        memcpy(out, PyBytes_AS_STRING(data), n)
        out[n] = 0
    return out


cdef void _set_err(char **err, object exc) noexcept:
    if err is not NULL:
        try:
            err[0] = _dup(_utf8(str(exc) or type(exc).__name__))
        except BaseException:
            err[0] = _dup(b"Unknown error")


cdef void *_own(object obj) noexcept:
    Py_INCREF(obj)
    return <void *>obj


cdef void _disown(void *ptr) noexcept:
    if ptr is not NULL:
        Py_DECREF(<object>ptr)


cdef int *c_re_search(void *re, const char *s, size_t length, size_t start, char **err) noexcept with gil:
    cdef int *out
    try:
        r = _impl.re_search(<object>re, PyBytes_FromStringAndSize(s, length), start)
        if r is None:
            return NULL
        out = <int *>malloc(2 * sizeof(int))
        if out is not NULL:
            out[0] = r[0]
            out[1] = r[1]
        return out
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef void *c_re_match(void *re, const char *s, size_t length, int start, char **err) noexcept with gil:
    cdef _Match state
    try:
        m = _impl.re_match(<object>re, PyBytes_FromStringAndSize(s, length), start)
        if m is None:
            return NULL
        state = _Match()
        state.match = m
        return _own(state)
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef int *c_re_get_match(void *m, size_t idx) noexcept with gil:
    cdef _Match state = <_Match>m
    try:
        r = _impl.re_get_match(state.match, idx)
    except BaseException:
        return NULL
    if r is None:
        return NULL
    state.range[0] = r[0]
    state.range[1] = r[1]
    return &state.range[0]


cdef char *c_re_replace(void *re, const char *replacement, const char *s, size_t length, int max_count,
                        char **err) noexcept with gil:
    try:
        return _dup(_impl.re_replace(<object>re, <bytes>replacement, PyBytes_FromStringAndSize(s, length), max_count))
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef void *c_re_compile(const char *pattern, int flags, char **err) noexcept with gil:
    try:
        return _own(_impl.re_compile(<bytes>pattern, flags))
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef const agi_re_flag *c_get_flags() noexcept nogil:
    return _flags


cdef void c_match_free(void *m) noexcept with gil:
    _disown(m)


cdef void c_regex_free(void *re) noexcept with gil:
    _disown(re)


cdef char *_convert(str method, const char *s, char **err) noexcept with gil:
    try:
        return _dup(getattr(_impl, method)(<bytes>s))
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef char *c_to_upper(const char *s, char **err) noexcept with gil:
    return _convert("unicode_upper", s, err)


cdef char *c_to_lower(const char *s, char **err) noexcept with gil:
    return _convert("unicode_lower", s, err)


cdef char *c_to_fold(const char *s, char **err) noexcept with gil:
    return _convert("unicode_fold", s, err)


cdef bint _lfs_setter(str method, const char *path, char **err) noexcept with gil:
    try:
        getattr(_impl, method)(<bytes>path)
        return True
    except BaseException as e:
        _set_err(err, e)
        return False


cdef bint c_lfs_chdir(const char *path, char **err) noexcept with gil:
    return _lfs_setter("lfs_chdir", path, err)


cdef bint c_lfs_mkdir(const char *path, char **err) noexcept with gil:
    return _lfs_setter("lfs_mkdir", path, err)


cdef bint c_lfs_rmdir(const char *path, char **err) noexcept with gil:
    return _lfs_setter("lfs_rmdir", path, err)


cdef bint c_lfs_touch(const char *path, char **err) noexcept with gil:
    return _lfs_setter("lfs_touch", path, err)


cdef char *c_lfs_currentdir(char **err) noexcept with gil:
    try:
        return _dup(_impl.lfs_currentdir())
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef long long c_lfs_get_mtime(const char *path, char **err) noexcept with gil:
    try:
        return _impl.lfs_get_mtime(<bytes>path)
    except BaseException as e:
        _set_err(err, e)
        return 0


cdef const char *c_lfs_get_mode(const char *path, char **err) noexcept with gil:
    cdef bytes mode
    try:
        result = _impl.lfs_get_mode(<bytes>path)
        if result is None:
            return NULL
        mode = _mode_names.setdefault(result, result)
        return PyBytes_AS_STRING(mode)
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef unsigned long long c_lfs_get_size(const char *path, char **err) noexcept with gil:
    try:
        return _impl.lfs_get_size(<bytes>path)
    except BaseException as e:
        _set_err(err, e)
        return 0


cdef void *c_lfs_dir_new(const char *path, char **err) noexcept with gil:
    try:
        return _own(_impl.lfs_dir_new(<bytes>path))
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef char *c_lfs_dir_next(void *it, char **err) noexcept with gil:
    try:
        name = _impl.lfs_dir_next(<object>it)
        if name is None:
            return NULL
        return _dup(name)
    except BaseException as e:
        _set_err(err, e)
        return NULL


cdef void c_lfs_dir_close(void *it, char **err) noexcept with gil:
    try:
        _impl.lfs_dir_close(<object>it)
    except BaseException as e:
        _set_err(err, e)


cdef void c_lfs_dir_free(void *it, char **err) noexcept with gil:
    _disown(it)


def set_ffi_impl(impl):
    """Install the Python object behind the C function tables and build the regex flag list."""
    global _impl, _flags, _flag_names
    cdef Py_ssize_t i
    cdef agi_re_flag *table
    flags = list(impl.re_flags())
    table = <agi_re_flag *>malloc((len(flags) + 1) * sizeof(agi_re_flag))
    if table is NULL:
        raise MemoryError()
    names = []
    for i in range(len(flags)):
        name = bytes(flags[i][0])
        names.append(name)
        table[i].name = PyBytes_AS_STRING(name)
        table[i].value = flags[i][1]
    table[len(flags)].name = NULL
    table[len(flags)].value = 0
    if _flags is not NULL:
        free(_flags)
    _flags = table
    _flag_names = names
    _impl = impl


def ffi_functions():
    """Addresses of the C functions, by library and name."""
    return {
        "re": {
            "search": <size_t><void *>c_re_search,
            "match": <size_t><void *>c_re_match,
            "get_match": <size_t><void *>c_re_get_match,
            "replace": <size_t><void *>c_re_replace,
            "compile": <size_t><void *>c_re_compile,
            "get_flags": <size_t><void *>c_get_flags,
            "match_free": <size_t><void *>c_match_free,
            "regex_free": <size_t><void *>c_regex_free,
        },
        "unicode": {
            "to_upper_case": <size_t><void *>c_to_upper,
            "to_lower_case": <size_t><void *>c_to_lower,
            "to_fold_case": <size_t><void *>c_to_fold,
        },
        "lfs": {
            "chdir": <size_t><void *>c_lfs_chdir,
            "currentdir": <size_t><void *>c_lfs_currentdir,
            "mkdir": <size_t><void *>c_lfs_mkdir,
            "rmdir": <size_t><void *>c_lfs_rmdir,
            "touch": <size_t><void *>c_lfs_touch,
            "get_mtime": <size_t><void *>c_lfs_get_mtime,
            "get_mode": <size_t><void *>c_lfs_get_mode,
            "get_size": <size_t><void *>c_lfs_get_size,
            "dir_new": <size_t><void *>c_lfs_dir_new,
            "dir_free": <size_t><void *>c_lfs_dir_free,
            "dir_next": <size_t><void *>c_lfs_dir_next,
            "dir_close": <size_t><void *>c_lfs_dir_close,
        },
    }
