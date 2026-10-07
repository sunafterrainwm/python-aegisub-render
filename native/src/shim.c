/*
 * aegisub-render native shim.
 *
 * Everything Aegisub itself does in C before handing control to Lua code lives in
 * libaegisub/lua/modules.cpp (preload_modules); this file is the equivalent for a
 * host that drives LuaJIT through ctypes, plus a memory-limited allocator.
 *
 * Python must never let a Lua error unwind through its own frames, so every function
 * here either cannot raise a Lua error or is meant to be called through lua_cpcall.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

#define AEGI_API __declspec(dllexport)

int luaopen_lpeg(lua_State *L);
int luaopen_luabins(lua_State *L);

typedef struct AegiAlloc {
	size_t used;      /* bytes currently allocated by the Lua state */
	size_t peak;      /* high-water mark of used */
	size_t limit;     /* 0 = unlimited */
	int suspended;    /* > 0 while Python callbacks run: never fail allocations there */
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
AEGI_API lua_State *aegi_newstate(size_t limit)
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

AEGI_API void aegi_close(lua_State *L)
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

AEGI_API void aegi_suspend_limit(lua_State *L, int suspend)
{
	AegiAlloc *a = get_alloc(L);
	a->suspended += suspend ? 1 : -1;
	if (a->suspended < 0)
		a->suspended = 0;
}

AEGI_API size_t aegi_memory_used(lua_State *L) { return get_alloc(L)->used; }
AEGI_API size_t aegi_memory_peak(lua_State *L) { return get_alloc(L)->peak; }
AEGI_API size_t aegi_memory_limit(lua_State *L) { return get_alloc(L)->limit; }
AEGI_API int aegi_memory_limit_hit(lua_State *L) { return get_alloc(L)->limit_hit; }

/*
 * Equivalent of agi::lua::preload_modules(), minus the parts that need aegisub.exe
 * (aegisub.__re_impl etc. are supplied by the Python host through the ffi).
 * Must be called through lua_cpcall.
 */
AEGI_API int aegi_openlibs(lua_State *L)
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

/* Allocation helpers for strings handed to Lua through the ffi; the Lua side frees
 * them with ffi.C.free, which resolves to this module's CRT. */
AEGI_API void *aegi_malloc(size_t size) { return malloc(size); }
AEGI_API void aegi_free(void *ptr) { free(ptr); }
