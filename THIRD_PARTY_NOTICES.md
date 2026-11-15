# 第三方元件

`LICENSE`（BSD 3-Clause）只涵蓋本專案自己的程式碼與文件。下列檔案來自其他專案，各自依原本的授權散布；
它們是從 Aegisub v3.5.1 的原始碼樹原樣取出的（來源與雜湊記錄在 `native/upstream.json`）。

| 檔案 | 專案 | 授權 |
| --- | --- | --- |
| `native/src/lpeg.c`、`native/src/lpeg.h` | LPeg（Lua.org & PUC-Rio，Roberto Ierusalimschy） | MIT |
| `native/subprojects/luabins/` | luabins | MIT（見該資料夾的 `COPYRIGHT`） |
| `src/aegisub_render/host/lua/moonscript.lua` | MoonScript 0.7.0（Leaf Corcoran） | MIT |

建置時另外會下載並靜態連結 LuaJIT 2.1（Mike Pall，MIT；見 `native/subprojects/luajit.wrap`），
它的原始碼不在這個 repo 裡。

本專案重現 Aegisub 3.5.1 Automation 4 的執行環境；Aegisub 以 BSD 3-Clause 授權散布
（https://github.com/TypesettingTools/Aegisub）。執行時載入的 `kara-templater.lua`、`karaskel` 等腳本
來自使用者自己安裝的 Aegisub，不在這個 repo 裡。
