# 架構與模式

## 資料流

`cli.py` → `runner.prepare()`（讀 ASS、設定、影片資訊、決定腳本）→ `runner.run_job()` 以 **spawn 的 worker 行程**
執行（父行程負責 120 秒逾時）→ `worker.execute()` 建 `ScriptHost`、載入腳本、跑巨集／濾鏡 →
`runner.output_data()` 決定寫法（巨集＝Save，濾鏡＝Export）→ 原子性寫檔／stdout／dry-run diff。

內建巨集（`macro/`）不走 worker：`macro offset` 讀檔 → 平移 → 共用 `runner._write_output`；
`macro measure` 直接用 `TextExtents`／`_gdi` 量測並輸出 JSON。

## Lua host

- `host/lua/bootstrap.lua` 是 host 的 Lua 半邊，重現 `auto4_lua*.cpp` 與 `libaegisub/lua/*.cpp`；Python 只存資料與碰作業系統。
- Python 函式以 Cython dispatcher 變成真正的 Lua C 函式（`lua/_luajit.pyx`）。Python 一律正常返回，
  `lua_error` 在之後由純 C 拋出（`RAISE_LOCATED/PLAIN/NIL/ARG` 狀態碼）。執行 Python 時暫停記憶體上限。
- 錯誤語意照抄：`agi::lua::error` 會加上呼叫端位置（`luaL_where(L, 1)`）→ `api_error(level, msg)`；
  由 C++ 例外轉來的錯誤沒有位置 → `error(msg, 0)`；`argerror`/`typerror` 照 `agi::lua` 版本。
- `add_stack_trace` 重現，host 的 frame 顯示成 `<C function>`；`run_protected` 刻意不是 tail call。
- 信任邊界用環境而非堆疊：從檔案載入的 chunk 用 `file_env`（可 `require "ffi"`），執行期編譯的程式碼用全域 require。
- 字幕物件：資料在 Python（`host/subs.py`），轉換與驗證在 Lua，保留 metamethod 與錯誤順序；
  刻意保留 Aegisub 的怪行為（`subs.delete(a,b,c)` 只刪最後一個、`subs[i] = nil` 報錯）。

## ASS 與輸出

- `ass/` 是 `src/ass_*.cpp` 的移植；解析器記錄輸入是否有 Project Garbage／Extradata 段落。
- 寫出：`serialize_ass`＝`SubsController::Save`（CleanExtradata → WriteFile），`serialize_export`＝`ExportFile`
  （但保留輸入本來有的那兩段）。
- C 行為（`atoi`、`strtol`、`%g`/`%f`、C 轉型）用 `_crt` 擴充模組呼叫真的 MSVC UCRT，不用 Python 模擬。

## 影片

PyAV 讀取，模仿 FFMS2 provider；結果快取在 `%TEMP%\aegisub-render\<sha256>.json`；
「Load linked files: yes」的載入順序與 `Project::UpdateRelativePaths` 照做（但保留沒載入的連結）。

## 原生模組

`_luajit`（LuaJIT＋lpeg＋luabins＋`native/src/shim.c`）、`_gdi`（text_extents、字型 name 表、`glyph_ink_boxes` 筆畫邊界）、`_crt`。
`native/build.py`：驗證 vendored 檔案的 sha256 → MSVC 環境 → meson/ninja → 複製 `.pyd` → `native/stubgen.py` 產生 `.pyi`。
