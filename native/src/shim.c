/*
 * aegisub-render native shim.
 *
 * Everything Aegisub itself does in C before handing control to Lua code lives in
 * libaegisub/lua/modules.cpp (preload_modules) and libaegisub/lua/utils.cpp (error helpers);
 * this file is the equivalent for the Cython host, plus a memory-limited allocator.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

#include "shim.h"

int luaopen_lpeg(lua_State *L);
int luaopen_luabins(lua_State *L);

typedef struct AegiAlloc {
	size_t used;      /* bytes currently allocated by the Lua state */
	size_t peak;      /* high-water mark of used */
	size_t limit;     /* 0 = unlimited */
	int suspended;    /* > 0 while Python code runs: never fail allocations there */
	int limit_hit;    /* set when an allocation was refused because of the limit */
} AegiAlloc;

static void *aegi_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
	AegiAlloc *a = (AegiAlloc *)ud;
	size_t old = ptr ? osize : 0;
	void *p;

	if (nsize == 0) {
		free(ptr);
		a->used -= old;
		return NULL;
	}

	if (nsize > old && a->limit != 0 && a->suspended == 0 && a->used - old + nsize > a->limit) {
		a->limit_hit = 1;
		return NULL;
	}

	p = realloc(ptr, nsize);
	if (p == NULL)
		return NULL;
	a->used = a->used - old + nsize;
	if (a->used > a->peak)
		a->peak = a->used;
	return p;
}

static int aegi_panic(lua_State *L)
{
	const char *msg = lua_tostring(L, -1);
	fprintf(stderr, "aegisub-render: PANIC: unprotected error in call to Lua API (%s)\n",
		msg ? msg : "error object is not a string");
	fflush(stderr);
	abort();
	return 0;
}

/* Create a Lua state whose heap is capped at `limit` bytes (0 = unlimited). */
lua_State *aegi_newstate(size_t limit)
{
	lua_State *L;
	AegiAlloc *a = (AegiAlloc *)calloc(1, sizeof(AegiAlloc));
	if (!a)
		return NULL;
	a->limit = limit;
	L = lua_newstate(aegi_alloc, a);
	if (!L) {
		free(a);
		return NULL;
	}
	lua_atpanic(L, aegi_panic);
	return L;
}

void aegi_close(lua_State *L)
{
	void *ud = NULL;
	lua_getallocf(L, &ud);
	lua_close(L);
	free(ud);
}

static AegiAlloc *get_alloc(lua_State *L)
{
	void *ud = NULL;
	lua_getallocf(L, &ud);
	return (AegiAlloc *)ud;
}

void aegi_suspend_limit(lua_State *L, int suspend)
{
	AegiAlloc *a = get_alloc(L);
	a->suspended += suspend ? 1 : -1;
	if (a->suspended < 0)
		a->suspended = 0;
}

size_t aegi_memory_used(lua_State *L) { return get_alloc(L)->used; }
size_t aegi_memory_peak(lua_State *L) { return get_alloc(L)->peak; }
int aegi_memory_limit_hit(lua_State *L) { return get_alloc(L)->limit_hit; }

/*
 * Equivalent of agi::lua::preload_modules(), minus the parts that need aegisub.exe
 * (aegisub.__re_impl etc. are supplied by the host through the ffi).
 * Must be called through lua_cpcall.
 */
int aegi_openlibs(lua_State *L)
{
	luaL_openlibs(L);

	lua_getglobal(L, "package");
	lua_getfield(L, -1, "preload");
	lua_pushcfunction(L, luaopen_lpeg);
	lua_setfield(L, -2, "lpeg");
	lua_pop(L, 2);

	/* The host wraps this with Aegisub's deprecation warning before exposing it as
	 * package.preload.luabins (see wrap_luaopen_luabins in modules.cpp). */
	lua_pushcfunction(L, luaopen_luabins);
	lua_setfield(L, LUA_REGISTRYINDEX, "aegisub-render:luaopen_luabins");
	return 0;
}

/*
 * agi::lua::error() for a message already on the stack: prefix it with the position of the
 * Lua code that called the running C function (luaL_where(L, 1)) and raise it.
 */
int aegi_error(lua_State *L)
{
	luaL_where(L, 1);
	lua_insert(L, -2);
	lua_concat(L, 2);
	return lua_error(L);
}

/* agi::lua::argerror(), which raises through agi::lua::error(). */
int aegi_argerror(lua_State *L, int narg, const char *extramsg)
{
	lua_Debug ar;
	if (!lua_getstack(L, 0, &ar)) {
		lua_pushfstring(L, "bad argument #%d (%s)", narg, extramsg);
		return aegi_error(L);
	}
	lua_getinfo(L, "n", &ar);
	if (ar.namewhat && strcmp(ar.namewhat, "method") == 0 && --narg == 0) {
		lua_pushfstring(L, "calling '%s' on bad self (%s)", ar.name ? ar.name : "?", extramsg);
		return aegi_error(L);
	}
	lua_pushfstring(L, "bad argument #%d to '%s' (%s)", narg, ar.name ? ar.name : "?", extramsg);
	return aegi_error(L);
}
