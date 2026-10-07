/* aegisub-render native shim (see shim.c). */
#ifndef AEGI_SHIM_H
#define AEGI_SHIM_H

#include <stddef.h>
#include "lua.h"

lua_State *aegi_newstate(size_t limit);
void aegi_close(lua_State *L);
void aegi_suspend_limit(lua_State *L, int suspend);
size_t aegi_memory_used(lua_State *L);
size_t aegi_memory_peak(lua_State *L);
int aegi_memory_limit_hit(lua_State *L);
int aegi_openlibs(lua_State *L);
int aegi_error(lua_State *L);
int aegi_argerror(lua_State *L, int narg, const char *extramsg);

#endif
