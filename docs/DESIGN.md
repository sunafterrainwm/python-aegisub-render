# 設計紀錄

這份文件記錄實作前討論定案的設計決策，以及實作時依 Aegisub 原始碼（目前是 3.5.1）釐清的細節。

## 基準

- 以 `C:\Program Files\Aegisub`（官方 3.5.1，`aegisub.exe` 的 FileVersion 是 3.5.1.9914）為準；原始碼參考
  `TypesettingTools/Aegisub` tag `v3.5.1` 的快照（zip，不 clone），放在被 gitignore 的 `reference/aegisub-3.5.1/`
  （`reference/aegisub-3.5.0/` 留著對照）。最初以 3.5.0 為基準，後來升到 3.5.1，見下方「3.5.1」。
- 3.5.x 實際內嵌的是 LuaJIT 2.1（`04dca79`，`lua52compat`），所以執行環境選擇 LuaJIT 而非 PUC Lua：
  5.2/5.4 都需要額外補 `setfenv`、`bit`、LuaJIT 的 PRNG，也沒有 ffi（只能用相容性不完整的 cffi-lua）。
  LuaJIT 下這些都是原生的，`.0` 的字串化也自然與 Aegisub 一致。

## 3.5.1

v3.5.0 → v3.5.1 的差異中，影響本工具的只有 Automation 的三處，已照做：

- 新增 `aegisub.version`（`push_version`）：`{string = "3.5.1", major = 3, minor = 5, patch = 1, build = 9914}`。
  `build` 是官方建置的 git revision（6962 + 自最後一個 SVN commit 以來的 commit 數，即 `aegisub.exe` FileVersion
  的第四段）；淺層 clone 建置時是 0，Aegisub 便不放這個欄位。數值集中在 `aegisub_render/version.py`。
- `include()` 改用 `lua_pcall` 執行被 include 的檔案：執行期錯誤變成
  `Error running Lua include "<name>":
<錯誤>`（前面接呼叫位置），錯誤物件不是字串／數字時是
  `bad argument #-1 to 'include' (string expected, got <type>)`。
- `include()` 與模組搜尋器讀取載入錯誤時改用正確的堆疊位置（3.5.0 讀錯位置，`.moon` 編譯失敗會被當成成功或印出檔名）。
  本工具原本就刻意回報真正的錯誤，現在與 3.5.1 一致，行為不變。

其餘差異（`#pragma once`、macOS 的修正、影片／音訊副檔名清單改由 provider 提供、檔案對話框記住的路徑、
`ffmpeg` 建置選項）與本工具無關。內附的 LuaJIT／lpeg／luabins 原始碼、MoonScript、`automation/` 的 Lua 檔、
ASS 讀寫與 kara-templater 都與 3.5.0 逐位元組相同，所以原生模組不必重編，輸出除了 header 的版本號以外不變。

## 原生元件

- 沒有可信來源提供符合需求（commit、`lua52compat`、GC64、lpeg 0.10）的預建 DLL，因此自行建置；
  建置腳本照抄 Aegisub 的 meson 設定，建置產物與腳本都 commit（根目錄 `meson.build`、`native/`、套件內的 `.pyd`）。
- 綁定用 Cython（`.pyx` 實作、`.pxd` C 宣告、`.pyi` 型別）：`_luajit` 擴充模組靜態連結 LuaJIT + lpeg 0.10 + luabins + shim
  （與 Aegisub 把它們連進 exe 的方式相同）；`_gdi` 做 `text_extents` 的 GDI 量測；`_crt` 用 MSVC UCRT 本身計算
  Aegisub 依賴的 C 行為（`atoi`、`strtol`、`%g`/`%f`、C 轉型），取代原本的 Python 模擬。
- Lua 執行環境的來源可以換：`native/sync_upstream.py` 從 GitHub（逐檔讀取，不 clone）或本地 git 的指定
  tag／commit／分支（或 `9706-cibuilds-20caaabc0` 這類版本字串，取最後一段為 commit）取出 `luajit.wrap`、
  LuaJIT 的 meson 選項、lpeg、luabins 與 MoonScript，寫進 `native/upstream.json`（來源、commit、sha256、選項、
  預期的 `jit.version`）。只認兩種寫法：
  - **aegisub-3.5**：固定雜湊的 `[wrap-file]`（wrapdb 的 LuaJIT port），`subprojects/luabins`，
    選項取自上游 `meson.build` 的 `'luajit:...'`。
  - **arch1t3cht-feature**：`[wrap-git]` + `patch_directory = luajit`（`subprojects/packagefiles/luajit` 自己的 meson
    建置，固定開 lua52compat、沒有選項），`vendor/luabins`。上游 `revision = v2.1` 是會移動的分支，
    為了可重現，固定成該分支在來源 commit 日期當時的 commit（這是推定：實際建置可能稍晚）；`jit.version` 照
    LuaJIT `genversion.lua` 的做法算：`luajit_rolling.h` 的 `LUAJIT_VERSION` 把 `ROLLING` 換成該 commit 的 committer 時間戳。
  feature_12 實測：LuaJIT `v2.1` → `97813fb`（`LuaJIT 2.1.1727870382`，與編出來的一致）、MoonScript 0.5.0，
  luabins 與 lpeg 與 3.5.1 逐位元組相同；用它編譯後全部測試通過。換來源只換執行環境，Lua API 仍是 3.5.1 的。
- LuaJIT 的選項不再寫死在根目錄 `meson.build`，由 `native/build.py` 依 `upstream.json` 傳給 `meson setup`；
  `upstream.json` 變了（以 sha256 判斷）就整個重建 builddir。
- `.pyi` 由 `native/stubgen.py` 從編好的模組產生（Cython 沒有 stub 產生器，改用 `embedsignature.format=python`
  嵌入的簽名），不手寫。
  最初的 ctypes 版本慢約 3 倍，且 ctypes callback 的 `int` 回傳值只寫入 4 個位元組，LuaJIT 卻把整個 RAX 當索引用。
- 原則：Lua 錯誤絕不穿越持有 Python 狀態的 frame。
  - 面向 Python 的方法只呼叫不會拋 Lua 錯誤的 API（stack 操作、push、raw 存取、受保護的
    `lua_pcall`/`lua_cpcall`/`luaL_loadbuffer`）；唯一的例外是配置失敗，所以 Python 程式碼執行期間暫停記憶體上限。
  - Python 函式以 `lua_CFunction` 提供給 Lua：`with gil` 區塊呼叫 Python 並一定正常返回，回到沒有 Python 狀態的
    C 程式碼後才依結果 `lua_error`（`agi::lua::error` 會加呼叫端位置，C++ 例外則不加，兩種都重現）。
- `aegisub.re`/`unicode`/`lfs` 保留 Aegisub 原本的 `.moon` 包裝，只把背後的 C 函式表換成 `_luajit.pyx` 裡同簽名的
  C 函式（經由 ffi 呼叫），邏輯在 `host/impls.py`。
- 專案不安裝成套件（`package = false`、沒有 `src/` 層），由根目錄的 `aegisub-render.py` 啟動。
  錯誤經 `char **err` 傳回，回傳字串以同一個 CRT 的 `malloc` 配置，`ffi.C.free` 可正確釋放。

## 非 Windows（Linux／WSL）

- `text_extents` 照 `CalculateTextExtents` 的非 WIN32 分支：Aegisub 用 `wxMemoryDC::GetTextExtent`（GTK 上是 Pango
  layout，字級 `(int)(fontsize*64)` pt），再把結果乘以 `fontsize / 行高`。所以 `height` 恆等於字級，寬度、descent
  依同一比例，external leading 是 0（wxGCDC 一律給 0）；有 spacing 時逐字量測，spacing 也乘上該比例
  （`fontsize / 以 96 dpi 換算的行高像素`）。空字串沒有 spacing 時仍有一行的高度（Pango 的空 layout），不像 GDI 是 0。
- 字型選擇與量測（`host/_fcft.pyx`）像 libass：fontconfig（family、weight、slant → `FcConfigSubstitute` +
  `FcDefaultSubstitute` + `FcFontMatch`），量測用 HarfBuzz 的 OpenType 函式（shaping 後的 advance、
  `hb_font_get_h_extents` 的行高，與 Pango 相同），不需要 FreeType。字型替換警告比對 fontconfig 給出的所有語言的
  family／fullname；`glyph_ink_boxes` 用 `hb_font_get_glyph_extents`。私人字型用 `FcConfigAppFontAddDir`。
- `_crt` 在 Linux 呼叫 glibc（Aegisub 在 Linux 也是）：`atoi`／`strtol` 不飽和、`long` 是 64 位元。
- LuaJIT 的錯誤會展開經過 C frame，非 Windows 時照 Aegisub 的 `meson.build` 以 `-fexceptions` 編譯。
- 路徑照 `std::filesystem` 跟著平台走（`make_preferred` 只有 Windows 會換成 `\`），模組搜尋器用 `LUA_DIRSEP`。
- WSL：`aegisub_render/wsl.py` 偵測 `/proc/sys/kernel/osrelease` 的 `microsoft`，把 config 與命令列上的
  `X:\...` 換成 `/mnt/x/...`；`?user`／`?local` 取自 Windows 的 `%APPDATA%`／`%LOCALAPPDATA%`（`cmd.exe`）。
  連結檔若是 Windows 磁碟機路徑，只在檢查檔案是否存在時翻譯，寫回時保持原樣。
- `.so` 不 commit（依各系統的 fontconfig／HarfBuzz 版本），builddir 是 `native/builddir-<platform>`；
  `.pyi`（含 `_fcft.pyi`）兩邊產生的內容相同，照常 commit。
- 測試：參考檔與 snapshot 都是 Windows 的輸出。非 Windows 時要求逐行相同、只有數字可以不同，且每個數字在 5% 或 ±1
  以內（實測最大 4.2%）；snapshot 只在 Windows 重新記錄。

## Lua host

- `host/lua/bootstrap.lua` 重現 `auto4_lua.cpp`、`auto4_lua_assfile.cpp`、`auto4_lua_progresssink.cpp`、
  `libaegisub/lua/{script_reader,utils,modules}.cpp` 的行為：模組搜尋器（`.moon` 優先）、`include()`、
  MoonScript 以固定路徑強制載入（失敗時改用 repo 內附的 0.7.0）、`add_stack_trace`（host 的 frame 顯示成 C 函式）、
  `aegisub` table、字幕物件（欄位讀寫順序、錯誤訊息皆照 C++）、progress sink。
- 字幕物件的資料（行列表、extradata、undo point）在 Python 端（`host/subs.py`），轉換與驗證在 Lua 端，
  以保留 metamethod 與錯誤順序。
- ffi 的放行以函式環境區分：檔案載入的 chunk 用 `file_env`（其 `require` 允許 ffi），執行期編譯的程式碼用全域 `require`。

## 沙箱（只為檢查奇怪用途）

封鎖 `io`、`package`、`dofile`、`loadfile`、`module`；`os` 只留 `clock/date/difftime/time`；`debug` 只留 `traceback`；
`getmetatable` 只對 table 有效；`load`/`loadstring` 只收文字；`string.dump` 封鎖；`collectgarbage` 只允許
`count`/`collect`；`print` 導到 trace level 4。封鎖物以代理物件呈現，存取時拋出描述性錯誤。

DependencyControl（`l0.DependencyControl` 及子模組）被「物理封印」：模組搜尋器在搜尋前就放棄，`require` 得到原生的
module not found；include 目錄中對應的檔案對 `require`、`include()` 與腳本載入都不可見（比對不分大小寫）。
以 `pcall(require, ...)` 載入它的腳本因此退回不用 DepCtrl 的模式。

## 限制

worker 行程 + 牆鐘逾時（預設 120 秒）、Lua heap 512,000,000 bytes、產出行數 50,000、輸入行數 100,000。

## 影片

PyAV 讀取、快取於 `%TEMP%\aegisub-render\<sha256(絕對路徑)>.json`（以大小與 mtime 驗證）；CLI 可覆蓋。
無影片時 `video_size`/`frame_from_ms`/`ms_from_frame` 回傳 nil、`keyframes` 回傳 `{}`（照原版）；
`get_audio_selection` 拋出未實現錯誤。專案屬性照 `Project::UpdateRelativePaths` 與 `VideoController`
在存檔時的結果改寫，但刻意不模仿一點：Aegisub 只寫出實際載入的檔案，所以找不到的影片／音訊等連結在存檔時會被刪掉；
這裡沒能載入的連結（檔案不存在、`--no-video`、`--fps`）照原樣保留，存到別的目錄時才改寫相對路徑。

## 輸出

巨集照 `SubsController::Save`：先 `CleanExtradata`，再以 `AssSubtitleFormat::WriteFile` 的格式寫出
（UTF-8 BOM、CRLF）。`[Script Info]` 開頭的註解不照 Aegisub（它一律寫自己的兩行）：解析時保留輸入檔開頭的 `;` 行（`AssFile.header_comments`），寫出時由 `writer.header_lines` 決定——`--keep-brand` 原樣輸出；預設時，輸入檔的第一行是 `; Script generated by Aegisub <ver>` 就保留該版本（也可能是 `9706-cibuilds-20caaabc0` 這類建置名稱）並附加 ` with aegisub-render <ver>`（取代先前附加的），其後的註解行原樣保留（舊版寫的是 `; http://www.aegisub.org/`，不改成 https）；否則改寫成 `; Script generated by Aegisub 3.5.1 with aegisub-render <ver>` 與 `; https://aegisub.org/` 兩行。匯出濾鏡在 Aegisub 裡只會由
File → Export 執行（`AssExporter::Export`），所以 `--filter` 照 `AssSubtitleFormat::ExportFile` 寫出：不寫
Project Garbage 與 Extradata，也不做 `CleanExtradata`（`{=id}` 標記仍照 `GetEntryData` 留在對白裡）。唯一的例外是輸入檔
本來就有這兩段時：結果可能覆寫輸入檔，所以照存檔的方式保留（Extradata 先 `CleanExtradata`）。失敗或取消時不寫檔。

沒有 `--output` 時覆寫輸入檔。連結檔案的相對路徑照 `Project::SetSubtitlesFilename` → `UpdateRelativePaths` 改成相對於
儲存位置；執行腳本時的 `?script` 仍是輸入檔所在目錄（Aegisub 也是存檔時才改）。`--output` 已存在時預設詢問
（像 ffmpeg 的 `-y`/`-n`），在執行腳本前就決定。`--output -` 把同樣的位元組寫到 stdout（不檢查覆寫）。`--dry-run`
什麼都不寫，改印 diff 到 stderr：以輸入檔為舊版、結果為新版，比較前先拿掉 `;` 開頭的行，hunk 標頭仍標示完整檔案中
的行號；沒有差異時印 `No differences found.`。

Aegisub 執行巨集或濾鏡時開的進度對話框，在這裡是 worker 行程裡的 tqdm 進度條（只在 stderr 是終端機時）：標題是巨集／
濾鏡名稱（同 `LuaThreadedCall` 的對話框標題），`aegisub.progress.title/task/set` 更新標題、尾端文字與百分比。腳本輸出
先清掉進度條再印、印完重畫；輸出停在行中間時暫不重畫，以免蓋掉那一行。執行結束後進度條留在畫面上，成功時補到 100%。

## 內建巨集（`macro`）

`aegisub-render macro` 是不需要 Aegisub 腳本、也不啟動 Lua worker 的 Python 操作（`aegisub_render/macro/`）。
輸出沿用 `render` 的路徑（`runner._read_input_ass`／`_write_output`：就地覆寫、`-o`、`-o -`、`-y`/`-n`、`--dry-run`），
寫出時照 Save（`serialize_ass`）。

- **`macro offset`**：把所有 `[Events]` 行（含 Comment）的開始／結束時間平移。
  - 時間：`[+-][[[hh:]mm:]ss].ms`，必須有小數點以和幀數區分；小數取到毫秒（`.5` = 500 ms）。
  - 幀數：`[+-]frames`，需要影片、時間碼或 `--fps`（影片參數與連結檔的處理和 `render` 相同），否則報錯且不寫檔。
    新時間是目標幀的 `Framerate.time_at_frame(i, START)`（`i > 0` 時是前後兩幀時間的中點，`i <= 0` 為 0）。
  - 結果夾限在 `0 .. 9:59:59.99`。
- **`macro measure`**：輸出 JSON。
  - 樣式來自參數（`--font`/`--fontsize`…）或 `--ass` 檔裡的樣式（`--style`，同時取得 PlayRes），各欄位可再覆寫；
    `--video-size` 時照 karaskel 的公式加上水平修正係數。
  - 寬高與逐字位置用和 `text_extents` 相同的 GDI 量測（64 倍字號）；整行寬度以整行一次量測為準，逐字位置是逐字累加。
  - 筆畫留白用 `_gdi.glyph_ink_boxes`（`GetGlyphOutlineW` + `GGO_NATIVE`，點陣字型退回 `GGO_METRICS`）；
    空白字元沒有筆畫；筆畫超出字元格時留白忠實地是負值（左＋筆畫＋右恆等於寬）。
  - 位置照 `karaskel.preproc_line_pos`：`--an` + `--margins`（含有效邊距）或 `--an` + `--pos`；有版面時逐字附上
    `sleft`/`scenter`/… 等畫面座標。
- CLI 把 `-` 後接數字或小數點的參數一律視為值（`_negative_number_matcher`），否則 `-0:01:00.00` 會被當成旗標。

## 測試

`scripts/gen_cases.py` 以固定 seed 產生案例。`tests/aegisub/` 是 Aegisub 3.5.1 以 `Karaoke template` 濾鏡匯出的結果（與 3.5.0 匯出的檔案只差 header 的版本號）：
`--filter` 的輸出必須與它逐位元組相同（只差 header 那行），巨集的輸出去掉 Project Garbage 段落後也必須相同；
snapshot 記錄巨集的完整輸出。
