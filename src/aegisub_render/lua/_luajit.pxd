# C declarations for _luajit.pyx: the LuaJIT 2.1 C API (lua.h / lauxlib.h) and the
# aegisub-render shim (native/src/shim.h). Macros are declared as functions.

cdef extern from "lua.h" nogil:
    ctypedef struct lua_State:
        pass
    ctypedef double lua_Number
    ctypedef Py_ssize_t lua_Integer
    ctypedef int (*lua_CFunction)(lua_State *L)

    ctypedef struct lua_Debug:
        int event
        const char *name
        const char *namewhat
        const char *what
        const char *source
        int currentline
        int nups
        int linedefined
        int lastlinedefined
        char short_src[60]

    enum:
        LUA_REGISTRYINDEX
        LUA_GLOBALSINDEX
        LUA_MULTRET
        LUA_TNONE
        LUA_TNIL
        LUA_TBOOLEAN
        LUA_TLIGHTUSERDATA
        LUA_TNUMBER
        LUA_TSTRING
        LUA_TTABLE
        LUA_TFUNCTION
        LUA_TUSERDATA
        LUA_TTHREAD

    int lua_upvalueindex(int i)

    int lua_gettop(lua_State *L)
    void lua_settop(lua_State *L, int idx)
    void lua_pop(lua_State *L, int n)
    void lua_pushvalue(lua_State *L, int idx)
    void lua_remove(lua_State *L, int idx)
    void lua_insert(lua_State *L, int idx)
    int lua_checkstack(lua_State *L, int sz)

    int lua_type(lua_State *L, int idx)
    const char *lua_typename(lua_State *L, int tp)
    lua_Number lua_tonumber(lua_State *L, int idx)
    lua_Integer lua_tointeger(lua_State *L, int idx)
    int lua_toboolean(lua_State *L, int idx)
    const char *lua_tolstring(lua_State *L, int idx, size_t *len)
    void *lua_touserdata(lua_State *L, int idx)

    void lua_pushnil(lua_State *L)
    void lua_pushnumber(lua_State *L, lua_Number n)
    void lua_pushinteger(lua_State *L, lua_Integer n)
    void lua_pushlstring(lua_State *L, const char *s, size_t l)
    void lua_pushstring(lua_State *L, const char *s)
    void lua_pushboolean(lua_State *L, int b)
    void lua_pushlightuserdata(lua_State *L, void *p)
    void lua_pushcclosure(lua_State *L, lua_CFunction fn, int n)
    void lua_concat(lua_State *L, int n)

    void lua_createtable(lua_State *L, int narr, int nrec)
    void lua_rawget(lua_State *L, int idx)
    void lua_rawgeti(lua_State *L, int idx, int n)
    void lua_rawset(lua_State *L, int idx)
    void lua_rawseti(lua_State *L, int idx, int n)
    int lua_next(lua_State *L, int idx)

    int lua_pcall(lua_State *L, int nargs, int nresults, int errfunc)
    int lua_cpcall(lua_State *L, lua_CFunction func, void *ud)
    int lua_error(lua_State *L)

    int lua_getstack(lua_State *L, int level, lua_Debug *ar)
    int lua_getinfo(lua_State *L, const char *what, lua_Debug *ar)


cdef extern from "lauxlib.h" nogil:
    int luaL_loadbuffer(lua_State *L, const char *buff, size_t sz, const char *name)
    int luaL_ref(lua_State *L, int t)
    void luaL_unref(lua_State *L, int t, int ref)
    void luaL_where(lua_State *L, int lvl)


cdef extern from "shim.h" nogil:
    lua_State *aegi_newstate(size_t limit)
    void aegi_close(lua_State *L)
    void aegi_suspend_limit(lua_State *L, int suspend)
    size_t aegi_memory_used(lua_State *L)
    size_t aegi_memory_peak(lua_State *L)
    int aegi_memory_limit_hit(lua_State *L)
    int aegi_openlibs(lua_State *L)
    int aegi_error(lua_State *L)
    int aegi_argerror(lua_State *L, int narg, const char *extramsg)


cdef class LuaState:
    cdef lua_State *L
    cdef list _funcs
    cdef object _pending
    cdef object _traceback_ref

    cdef object _to_python(self, lua_State *L, int idx, int depth)
    cdef int _push(self, lua_State *L, object value, int depth) except -1
    cdef int _dispatch(self, lua_State *L) noexcept
    cdef int _raise(self, lua_State *L, str message, int status) except? -999
    cdef tuple _call(self, object func, tuple args, bint handler, bint refs)
