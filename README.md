# aegisub-render

在 Python 裡執行 Aegisub 3.5.0 的 Automation 4 腳本（預設是 kara-templater），不開 GUI。

- Lua 執行環境就是 Aegisub 3.5.0 用的那一個：LuaJIT 2.1 @ `04dca79`，`lua52compat`、GC64、
  lpeg 0.10、luabins，連同 MoonScript 0.7.0。所以 `.0` 不會出現在數字字串裡、`string.format("%d", 1.5)`、
  `math.random` 的序列（含 `math.randomseed` 後的結果）都和 Aegisub 一致。
- 腳本、karaskel、`aegisub/*.moon` 等全部從 Aegisub 安裝目錄（與 %APPDATA%）原封不動地載入；
  Aegisub 在 C++ 裡做的部分（`aegisub.*`、字幕物件、`aegisub.re/unicode/lfs` 背後的 C 函式、
  ASS 讀寫、`text_extents`）以 v3.5.0 原始碼為準重新實作。
- 目標是輸出與「在 Aegisub 3.5.0 套用後存檔」逐位元組相同（UTF-8 BOM、CRLF、Aegisub 的欄位格式）。

## 需求

- Windows x64（`text_extents` 用 GDI，Lua 執行環境是 Windows DLL）
- Python 3.14、[uv](https://docs.astral.sh/uv/)
- Aegisub 3.5.0 安裝目錄（預設 `C:\Program Files\Aegisub`）

```powershell
uv sync
uv run aegisub-render render input.ass -o output.ass
```

## 使用方式

```powershell
# 用 kara-templater 套用模板（等同 Automation > Apply karaoke template，然後存檔）
uv run aegisub-render render song.ass -o song.fx.ass

# 其他腳本 / 巨集 / 匯出濾鏡
uv run aegisub-render render in.ass -o out.ass --script strip-tags.lua --macro "Strip tags" --select 1,3-5
uv run aegisub-render render in.ass -o out.ass --filter "Karaoke template"

# 列出腳本註冊的巨集與匯出濾鏡
uv run aegisub-render list in.ass --script kara-templater.lua
```

`--script` 不是路徑時，依序在 `?user`、`?data` 的 autoload 目錄找（與 Aegisub 的 `Path/Automation/Autoload` 設定相同）。

結束碼：`0` 成功、`1` 錯誤（Lua 執行錯誤、腳本載入失敗、逾時…）、`2` 腳本呼叫了 `aegisub.cancel()`。
失敗或取消時不會寫出任何檔案（輸出是原子性寫入）。

### 主要參數

| 參數 | 說明 |
|---|---|
| `--config PATH` | 設定檔，預設為環境變數 `AEGISUB_RENDER_CONFIG` 或本專案根目錄的 `config.py` |
| `--aegisub-root DIR`、`--load-appdata` / `--no-load-appdata` | 覆蓋 `config.py` 的 `AEGISUB_ROOT`、`LOAD_APPDATA` |
| `--macro NAME` / `--filter NAME` | 要執行的巨集（預設 `Apply karaoke template`）或匯出濾鏡 |
| `--select 1,3-5` | 選取的對白行（1 起算，只算 Dialogue/Comment 行）；預設全選 |
| `--force` | 巨集的 validation 函式回傳 false 時仍然執行 |
| `--trace-level N`、`-v`、`-q` | `aegisub.debug.out` 的門檻（預設 3，同 Aegisub）；`-q` 只留錯誤與腳本輸出 |
| `--timeout`、`--memory-limit`、`--max-output-lines`、`--max-input-lines` | 覆蓋資源限制 |
| `--font-dir DIR` | 額外字型目錄（以 `FR_PRIVATE` 暫時註冊給 `text_extents`），可重複 |
| `--video PATH` / `--no-video` | 覆蓋專案的 Video File（也接受 `?dummy:...`）/ 當作沒有影片 |
| `--video-size WxH`、`--fps F`、`--timecodes FILE`、`--keyframes FILE` | 手動指定影片資訊 |
| `--refresh-cache`、`--no-cache` | 影片資訊快取（`%TEMP%\aegisub-render\<sha256>.json`） |

### Python API

```python
from pathlib import Path
from aegisub_render import ExitCode, RenderOptions, load_settings, render

code = render(Path("song.ass"), Path("song.fx.ass"), load_settings(), RenderOptions(trace_level=4))
assert code is ExitCode.OK
```

## 設定（`config.py`）

| 常數 | 預設 | 說明 |
|---|---|---|
| `AEGISUB_ROOT` | `C:\Program Files\Aegisub` | Aegisub 安裝目錄（`?data`） |
| `LOAD_APPDATA` | `None` | `None` 照 Aegisub 自動判斷（安裝目錄有 `config.json` 即攜帶式）；`True` 用 `%APPDATA%\Aegisub` 當 `?user`；`False` 則 `?user` = `?data` |
| `DEFAULT_SCRIPT` / `DEFAULT_MACRO` | `kara-templater.lua` / `Apply karaoke template` | |
| `TRACE_LEVEL` | `3` | |
| `TIMEOUT_SECONDS` | `120` | Lua worker 的牆鐘時間上限 |
| `MEMORY_LIMIT_BYTES` | `512_000_000` | Lua heap 上限（十進位 MB） |
| `MAX_OUTPUT_LINES` | `50_000` | 腳本 append/insert 的行數上限 |
| `MAX_INPUT_LINES` | `100_000` | 輸入檔的行數上限 |
| `FONT_DIRS` | `[]` | 額外字型目錄 |

此外會讀 `?user/config.json`（Aegisub 自己的設定）中的 `Path/Automation/Include`、`Path/Automation/Autoload`、
`App/Save UI State`、`Video/Open Audio`，讓結果和你的 Aegisub 一致。

## 沙箱

封鎖的目的是**檢查有沒有奇怪用途**，不是安全邊界（ffi 允許使用，能做到任何事）。

- 全域環境：`io`、`package`、`dofile`、`loadfile`、`module` 換成「一碰就報錯」的代理物件；
  `os` 只留 `clock`/`date`/`difftime`/`time`；`debug` 只留 `traceback`；`string.dump` 封鎖；
  `getmetatable` 對非 table 一律回傳 nil；`load`/`loadstring` 只接受文字 chunk；
  `collectgarbage` 只允許 `"count"`、`"collect"`；`print` 輸出到 trace level 4。
- 封鎖的東西被存取時，錯誤訊息是 `aegisub-render: io.open is blocked (sandbox)` 這種形式，並附 traceback。
- `require "io"` / `require "package"` 回傳同一個代理（載入時引用它們的函式庫仍可載入，真正使用才報錯）；
  `require "os"` / `require "debug"` 回傳受限版本。
- ffi：從檔案載入的程式碼（腳本本身、include/autoload 目錄裡的模組，如 Yutils）可以 `require "ffi"`；
  執行期編譯的程式碼（`loadstring`，例如 kara-templater 的模板程式碼）不行。判斷依據是函式環境，所以尾呼叫繞不過去。
- `aegisub.lfs` 照原樣可用；`aegisub.clipboard` 呼叫時報「unreachable」。

## 資源限制

Lua 在獨立的 worker 行程中執行：

- 時間：主行程在 `TIMEOUT_SECONDS` 後直接結束 worker（對 JIT 編譯的迴圈、卡在 C 函式裡的情況同樣有效）。
- 記憶體：自訂 allocator 限制 Lua heap（含 `ffi.new` 的 cdata，不含 `ffi.C.malloc`）。注意尚未回收的垃圾也算在內。
- 行數：腳本 append/insert 的行數、輸入檔行數。

## 影片

照 Aegisub 開啟專案時「載入關聯檔案：是」的行為：專案的 Video File（相對於字幕檔）存在就載入，接著是
Timecodes File、Keyframes File；也支援 `?dummy:` 影片。影片資訊以 PyAV 讀取（依 PTS 排序封包，
與 FFMS2 provider 相同的換算方式），並快取在 `%TEMP%\aegisub-render\`。

沒有影片時，`aegisub.video_size()`、`frame_from_ms`、`ms_from_frame` 回傳 nil，`keyframes()` 回傳空表，
與 Aegisub 相同；`get_audio_selection()`、`aegisub.dialog.*` 會拋出「not implemented」錯誤。

## 測試

```powershell
uv run pytest                 # 全部
uv run basedpyright           # strict 型別檢查
uv run ruff check
```

`tests/cases/*.ass` 由 `scripts/gen_cases.py` 以固定 seed 產生（模板裡的 `math.random` 也都在 `code once` 設好 seed）。
`tests/snapshots/` 是本工具的輸出（**未經 Aegisub 驗證**），可用
`AEGISUB_RENDER_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cases.py` 重新記錄。

用 Aegisub 驗證：在 Aegisub 3.5.0 開啟 `tests/cases/` 裡的檔案 → Automation → Apply karaoke template → 另存到
`tests/aegisub/` 同名檔案。之後 `tests/test_cases.py` 會改為和它逐位元組比對（只忽略記錄 GUI 狀態的
`Scroll Position`、`Active Line`、`Video Position`、`Video Zoom Percent` 四行）。

## 重新建置原生 DLL

`src/aegisub_render/_native/aegisub_lua.dll` 已附在 repo 裡。要自己重建（需要 Visual Studio 2022 的 x64 C++ 工具）：

```powershell
uv run --group build python native/build.py
```

`native/` 的 meson 專案照抄 Aegisub v3.5.0：`subprojects/luajit.wrap`（同一個原始碼與 wrapdb patch 的 sha256）、
`subprojects/luabins/`、`libaegisub/lua/modules/lpeg.c`，選項與 Aegisub 相同（static、amalgam、lua52compat、
release、動態 CRT）；`build.py` 會先檢查這些檔案的 sha256 與 v3.5.0 一致。唯一新增的是 `src/shim.c`
（限制記憶體的 allocator、panic handler、preload lpeg/luabins），並把全部連結成一個 DLL 供 ctypes 載入。
LuaJIT 官方不提供二進位檔，其他來源（MSYS2、conda-forge）的版本與編譯選項都和 Aegisub 不同。

## 與 Aegisub 的已知差異

- 沒有 GUI：對話框（`aegisub.dialog.*`）、音訊無法使用。
- 需要 `package`/`io` 才能運作的函式庫（DependencyControl 與依賴它的腳本，如 Aegisub-Motion）在沙箱中無法載入。
- 開啟影片時 Aegisub 可能跳出詢問並修改 PlayRes / YCbCr Matrix / LayoutRes，這部分不重現。
- 字元編碼只接受 UTF-8（可有 BOM）與帶 BOM 的 UTF-16，不做 Aegisub 的編碼偵測。
- `aegisub.re` 以 Python `regex` 模擬 boost::u32regex（perl 語法、預設多行與 `.` 匹配換行）；罕見語法可能有差異。
- 影片旋轉中繼資料（FFMS2 會交換寬高）未處理。
- `Scroll Position` 等 GUI 狀態沿用輸入檔的值（在 Aegisub 裡存檔時會是當下的 GUI 狀態）。
- 刻意保留 3.5.0 的行為：`subs.delete(a, b, c)` 只刪除最後一個參數指定的行、`subs[i] = nil` 會報錯、
  `lfs.attributes(...).modification` 是自 1601 年起算的秒數。
- 刻意修正的 3.5.0 問題：`require` 的 MoonScript 模組編譯失敗時回報真正的錯誤（3.5.0 會變成「找不到模組」），
  模組載入錯誤訊息包含實際錯誤而非模組名稱。

設計決策的完整紀錄見 [docs/DESIGN.md](docs/DESIGN.md)。
