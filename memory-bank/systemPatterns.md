# 架構與模式

## 資料流

`cli.py` → `runner.render()`（檔案層：讀 ASS、`update_relative_paths`、選 Save／Export、原子性寫檔／
stdout／dry-run diff、把例外轉成 ExitCode）→ `ScriptRender`（`script_render.py`，執行層：解析 Aegisub
安裝與路徑代號、建影片 context、找腳本、組 `Job`，然後開一個**常駐的 spawn worker 行程**）→
`worker.ScriptSession` 在 worker 裡建 `ScriptHost`、載入腳本，之後每個請求（`features`／`validate`／
`run`）回一個 `Result`。

- 分界線：**`ScriptRender` 只管「在 AssFile 上跑腳本」**，不讀檔、不寫檔、不序列化、不知道結果要存到哪。
  `run()` 把 worker 回傳的內容搬回呼叫端那一個 `AssFile`（就地更新，回傳同一個物件；裡面的 entry 是新物件）。
- 一個 session 一個 Lua state，所以 `macro_can_run()`（巨集的 validation 函式）和 `run()` 共用同一個
  state —— Aegisub 也是這樣。逾時是**每個請求**各自計時，逾時或行程崩潰就 kill 掉整個 worker。
  `isolate=False` 時 Lua state 在本行程（測試用，沒有逾時保護）。
- `with ScriptRender(...) as sr:` 是常見寫法；忘了 `stop_lua_worker()` 也有 `__del__` 兜底，不會留孤兒行程。

自動化（`automation/`，＝上游的 `namespace Automation4`：腳本能提供的巨集與匯出濾鏡）：`base.py` 的 `Macro`／`ExportFilter`（可繼承 + 依優先度插入的註冊表，照
`AssExportFilterChain`）、`fixstyle.py`／`framerate.py`（Aegisub 僅有的兩個 C++ 濾鏡）、
`spec.py`（ffmpeg filtergraph 語法，單層跳脫）、`plugin.py`（autoload 目錄裡的 `.py` 濾鏡外掛，
module 自己呼叫 `register_filter`；只要 import 所以一律載入，不受 `--scan-autoload` 管；
照 Lua API 分兩種基底類別：`Macro`（`process_macro`／`validate_macro`）與 `ExportFilter`
（`process_filter`），`class X(Macro, ExportFilter)` 合法，`register()` 會丟進對應的登記表，
巨集那條路無引數建構；`selected_lines`／`active_line` 目前一律 `[]`／`0`；
內建與否看 `is_builtin()`（`__module__`））、
`resolve.py`（名字解析：內建 → `--script` 的腳本 → `.py` 外掛 → `--scan-autoload` 掃 autoload 的 Lua；
掃描結果快取在 `%TEMP%\aegisub-render\export-filters.json`，以目錄清單＋各 Lua 腳本 mtime／大小為鍵，
取用時再驗一次名字還在）。只有 Python 濾鏡吃參數 —— Lua 濾鏡在上游也拿不到參數。
`--[no-]garbage-export-filters` 跑檔案 `[Aegisub Project Garbage]` 的 `Export Filters:` 那串
（和 `--export-filter` 互斥；都沒給時只有互動終端機會問，而 Windows 的 `isatty()` 連 NUL 都說是
終端機，所以真正的判斷是讀進來立刻 EOF）。
自動搜尋副檔名集中在 `script_render.automation_files()`（`AUTOMATION_SUFFIXES` ＝ .lua/.moon/.py），
`resolve_script()` 多了 `suffixes` 參數。`--script x.py` 是合法的：`ScriptRender.is_python` 時不開 Lua，
`features()` 給它註冊的濾鏡、沒有巨集，`run(filter=...)` 就地跑那個濾鏡。
額外 autoload 目錄（`--autoload-dirs[-prepend]`、`AEGISUB_AUTOLOAD_DIRS[_PREPEND]`、config 的
`AUTOLOAD_DIRS[_PREPEND]`）在 `settings.effective_autoload()` 併進 Aegisub 的選項字串，
所以 `--script` 搜尋、外掛載入、濾鏡掃描與快取鍵全都自動跟著走；`FilterResolver` 收 `env_factory`，
`convert` 只有真的需要時才去找 Aegisub 安裝。
兩個子命令分工：`render` **永遠先跑巨集**（沒指定就是 config 的預設腳本／巨集），濾鏡鏈接在後面；
`convert` 不跑巨集，就是 File → Export（而且可以順便換格式）。`convert` 只有在鏈裡有非內建濾鏡時
才建 `RenderEnvironment`（`resolve_builtin_chain` 先試），所以純轉檔與純內建濾鏡都不需要 Aegisub。
有濾鏡時 `.ass` 照 File → Export 寫出。Lua 濾鏡仍走 `ScriptRender(filter=...)`，而且不吃參數。
`script_render.build_environment()`／`RenderEnvironment` 把「不需要 Lua 的那半」抽出來，
讓沒有腳本時也能有 paths 與影片 context，整條鏈共用一份。

內建的 headless 操作（`tools/`，與 `automation.Macro` 無關 —— 那是「替腳本跑巨集」的基底類別）不走 worker：`tools/` 下的子模組（`offset.py`、`measure.py` 等）各自包含 CLI 參數定義與執行函式，
透過 `CommandRegistry.register_command` 註冊（頂層 `render`、`list`、`macro` 亦同，以 `args.handler` 派發，無 `if/elif` 分支）：
`macro offset` 讀檔 → 平移 → 共用 `runner.write_output`；
`macro measure` 直接用 `TextExtents`／`_font` 量測並輸出 JSON。

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
  模型類別由 `ass/__init__.py` 與套件根 `__init__.py` 一起匯出（`from aegisub_render import AssFile`）；
  `AssFile.from_text/from_bytes/from_path/serialize/export` 是門面，方法裡用執行期 import 叫
  `ass.parser` / `ass.writer`（它們 import `ass.file`，所以不能在模組頂層反向 import）。
  `parser.split_subtitle_text` 是 `decode_subtitle_bytes` 的 UTF-8 那半段，給 `from_text` 用。
- `AssParser` 只透過 `make_file`（classmethod）／`make_info`／`make_style`／`make_dialogue`／
  `make_attachment`／`make_extradata` 建物件，子類別覆寫就能把檔案解析成自己的類別；`parser_cls`
  一路傳到 `parse_ass_*`、`read_ass_file` 與 `AssFile.from_*`。`AssStyle.parse`／`AssDialogue.parse`
  是 classmethod（`cls()`），`AssInfo.copy` 用 `type(self)`，所以子類別不必重寫解析。
- 把 `ass/` 當函式庫整合進別的流程時，要知道的幾件事：
  - Aegisub 那幾個常用的字幕操作都有對應的方法：`AssDialogue.get_stripped_text`／`strip_tags`
    （只留 plain 區塊，`\N` 不動）、`AssDialogue.copy`／`AssStyle.copy`／`AssFile.copy`
    （deepcopy，events 拿新 id，照 Aegisub 的 copy ctor）、`entries.ssa_to_ass_alignment`
    （公開 `_SSA_TO_ASS`）。
  - 覆寫標籤可以改回去：`OverrideParam.set_str`／`set_int`／`set_double`／`set_bool`
    （＝`AssOverrideParameter::Set`）、BLOCK 參數的惰性 `get_block()`、`Block.get_text()`、
    `Block.process_parameters()`（會遞迴進 `\t`）、`AssDialogue.update_text(blocks)`。
    `update_text` 是從 blocks 重建文字，所以吃下面那個「切 token 不是逐位元組安全」的問題。
  - 覆寫標籤有兩種切法：`parse_override_tags`／`parse_dialogue_blocks` 的 `strict=` 真的算括號
    （巢狀 `\t` 不會被切開、`str(tag)` 可逐位元組接回），`set_strict_tag_parsing()`／
    `strict_tag_parsing()` 讀寫行程層級的預設。預設永遠是 Aegisub 那一種，渲染流程不要切換。
  - 寫出的編碼與換行固定是 UTF-8 BOM + CRLF：writer 沒有 BOM／換行選項，要別的在呼叫端轉。
  - 不提供 override 標籤排序（那屬於呼叫端自己的規約，不是 Aegisub 的行為）。
- 寫出：`serialize_ass`＝`SubsController::Save`（CleanExtradata → WriteFile），`serialize_export`＝`ExportFile`
  （但保留輸入本來有的那兩段）。
- `ass/formats/` 是 `.ass` 以外的格式，`subtitle_format*.cpp` 的移植：`base.py` 的
  `SubtitleFormat`（可繼承的 abstract class，帶 Sort／StripComments／RecombineOverlaps／
  MergeIdentical／ConvertNewlines／StripTags／CanSave／`prepare()`）＋ 一個模組一個格式
  （`srt`、`vtt`＋`vtt_style`、`ttxt`、`txt`、`transtation`、`mkv`、`ass_format`）。
  `formats/__init__.py` 是註冊表（`register_format`＝`LoadFormats`、`get_reader`／`get_writer`
  照註冊順序找）與門面（`read_subtitles`／`serialize_subtitles`／`write_subtitles`，
  `make_format` 只把該格式建構子收的選項傳下去）。類別與門面都從 `aegisub_render.ass` 與套件根再匯出。
  - 讀：`.ass`／`.ssa`、`.srt`、`.vtt`、`.mkv`／`.mka`／`.mks`（PyAV）。
    寫：`.ass`、`.srt`、`.vtt`、`.ttxt`、`.txt`、`.transtation.txt`（需要 `fps`）。
  - 本專案自己的決定（理由見 `docs/DESIGN.md`〈其他字幕格式〉）：`TagMode`，匯出一律不寫樣式表，
    `{...}` 在 `.srt`／`.vtt`／`.ttxt` 預設**保留**、`.txt`／TranStation 預設剝掉。
  - CLI：`convert` 子命令（不啟動 Lua worker），`render -o out.srt` 也依副檔名換格式；兩者都有 `--tags`。
- C 行為（`atoi`、`strtol`、`%g`/`%f`、C 轉型）用 `_crt` 擴充模組呼叫真的 MSVC UCRT，不用 Python 模擬。

## 影片

PyAV 讀取，模仿 FFMS2 provider；結果快取在 `%TEMP%\aegisub-render\<sha256>.json`；
「Load linked files: yes」的載入順序與 `Project::UpdateRelativePaths` 照做（但保留沒載入的連結）。

## 原生模組

`_luajit`（LuaJIT＋lpeg＋luabins＋`native/src/shim.c`）、`_font`（同一個模組名，Windows 分支從 `_font.gdi.pyx`：GDI；
Linux 分支從 `_font.fcft.pyx`：fontconfig 選字、HarfBuzz 量測；兩份介面與 docstring 相同，一份 `_font.pyi`，
`_font.backend()` 說是哪一份）、`_crt`（該平台的 C 函式庫）。分支由 `meson.options` 的 `branch` 決定。
WSL 的路徑翻譯在 `src/aegisub_render/wsl.py`。
`native/sync_upstream.py`：從某個 Aegisub 樹（GitHub 或本地 git；aegisub-3.5 或 arch1t3cht-feature 兩種寫法）取出
LuaJIT／lpeg／luabins／MoonScript，寫進 `native/upstream.json`（來源、sha256、LuaJIT 選項、預期的 `jit.version`）。
`native/build.py`：依 `upstream.json` 驗證 sha256 → MSVC 環境 → meson（`-Dluajit:...`，lock 變了就重建 builddir）
→ 複製 `.pyd` → `native/stubgen.py` 產生 `.pyi`。
