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
  的第四段）；淺層 clone 建置時是 0，Aegisub 便不放這個欄位。數值集中在 `src/aegisub_render/version.py`。
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
  （與 Aegisub 把它們連進 exe 的方式相同）；`_font` 做 `text_extents` 的量測（見「非 Windows」）；`_crt` 用 MSVC UCRT 本身計算
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
- 套件在 `src/aegisub_render`（src layout）。根目錄的 `aegisub-render.py` 把 `src/` 加進 `sys.path` 後啟動；
  也可以被別的專案**以連結的方式**安裝（`uv add --editable <repo>`，build backend `uv_build`、`module-root = "src"`，
  console script `aegisub-render`），不發佈 wheel —— 擴充模組是 `native/build.py` 針對單一直譯器編好後 commit
  進 repo 的，不是安裝時才編譯。設定檔 `config.py` 留在 repo 根目錄，`settings.py` 以 `parents[2]`（`../../`）
  由套件位置推算它的絕對路徑。
  錯誤經 `char **err` 傳回，回傳字串以同一個 CRT 的 `malloc` 配置，`ffi.C.free` 可正確釋放。

## 非 Windows（Linux／WSL）

- `text_extents` 照 `CalculateTextExtents` 的非 WIN32 分支：Aegisub 用 `wxMemoryDC::GetTextExtent`（GTK 上是 Pango
  layout，字級 `(int)(fontsize*64)` pt），再把結果乘以 `fontsize / 行高`。所以 `height` 恆等於字級，寬度、descent
  依同一比例，external leading 是 0（wxGCDC 一律給 0）；有 spacing 時逐字量測，spacing 也乘上該比例
  （`fontsize / 以 96 dpi 換算的行高像素`）。空字串沒有 spacing 時仍有一行的高度（Pango 的空 layout），不像 GDI 是 0。
- 字型選擇與量測（`host/_font.fcft.pyx`）像 libass：fontconfig（family、weight、slant → `FcConfigSubstitute` +
  `FcDefaultSubstitute` + `FcFontMatch`），量測用 HarfBuzz 的 OpenType 函式（shaping 後的 advance、
  `hb_font_get_h_extents` 的行高，與 Pango 相同），不需要 FreeType。字型替換警告比對 fontconfig 給出的所有語言的
  family／fullname；`glyph_ink_boxes` 用 `hb_font_get_glyph_extents`。私人字型用 `FcConfigAppFontAddDir`。
- `_crt` 在 Linux 呼叫 glibc（Aegisub 在 Linux 也是）：`atoi`／`strtol` 不飽和、`long` 是 64 位元。
- LuaJIT 的錯誤會展開經過 C frame，非 Windows 時照 Aegisub 的 `meson.build` 以 `-fexceptions` 編譯。
- 路徑照 `std::filesystem` 跟著平台走（`make_preferred` 只有 Windows 會換成 `\`），模組搜尋器用 `LUA_DIRSEP`。
- WSL：`src/aegisub_render/wsl.py` 偵測 `/proc/sys/kernel/osrelease` 的 `microsoft`，把 config 與命令列上的
  `X:\...` 換成 `/mnt/x/...`；`?user`／`?local` 取自 Windows 的 `%APPDATA%`／`%LOCALAPPDATA%`（`cmd.exe`）。
  連結檔若是 Windows 磁碟機路徑，只在檢查檔案是否存在時翻譯，寫回時保持原樣。
- `.so` 不 commit（依各系統的 fontconfig／HarfBuzz 版本），builddir 是 `native/builddir-<platform>`；
  `.pyi` 兩邊產生的內容相同，照常 commit。
- 量測模組只有一個名稱 `aegisub_render.host._font`，依平台分支編譯 `_font.gdi.pyx` 或 `_font.fcft.pyx`
  （Cython `--module-name`；C 宣告放在同名 `.pxi` 以 `include` 引入）。兩份原始碼的公開函式、簽名與 docstring
  逐字相同，所以只有一份 `_font.pyi`，兩個平台的 `tests/test_stubs.py` 都檢查它；`_font.backend()` 回報
  `"gdi"` 或 `"fcft"`，測試以它（而不是 `sys.platform`）判斷 GDI 才有的行為。共用的純 Python 部分
  （字型檔副檔名、`name` 表解析）在 `host/fontnames.py`。分支由 `meson.options` 的 `branch` 決定（預設跟著主機）。
- Windows 上可用 MSYS2 強制編 Linux 分支：`native/build.py --msys2 <環境>` 以該環境的 gcc／pkg-config／fontconfig／
  HarfBuzz、`-Dbranch=linux` 建置（meson 仍在本專案的 Python 下執行，模組給它用；gcc 連結官方 MSVC 版 CPython
  沒有問題）。DLL 目錄寫進 `src/aegisub_render/_dll_dirs.txt` 由 `src/aegisub_render/__init__.py` 以 `os.add_dll_directory`
  載入（Python 3.8 起擴充模組的相依 DLL 不從 PATH 找）。UCRT64 的 C 函式庫是 UCRT，所以 `_crt` 與 MSVC 版相同；
  MSYS2 的 fontconfig 會看到 Windows 字型（例如 "Arial" 在 900 會選到 Arial Black）。以 `F:\msys64\ucrt64`
  （gcc 16、fontconfig 2.18、HarfBuzz 14）驗證：162 個測試通過、1 個 GDI 專用的略過。
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

worker 行程 + 牆鐘逾時（預設 120 秒，**每個請求**各自計時）、Lua heap 512,000,000 bytes、產出行數 50,000、輸入行數 100,000。

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

## 覆寫標籤：切 token 不是逐位元組安全的

`parse_override_tags`（`AssDialogueBlockOverride::ParseTags`）的 `depth` 只在 depth 0 看到 `(` 時加一，
所以參數列遇到**第一個** `)` 就結束，不管裡面還有幾層括號。kara-templater 常寫的
`\t(0,248,\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080)))` 因此會被切成兩個 `\t`，
而 `AssOverrideTag::operator std::string()` 開了括號一定補上右括號，所以把 `str(tag)` 接回去
得到的不是原字串：後半會變成 `\t(586,922,\clip(1230,0,1230,1080),))`。
兩邊都忠於上游，刻意不改（改成真正的括號計數就不是 Aegisub 的切法了）。

**壞掉的是位元組，不是畫面**：libass 把 `\t` 裡多出來的 `)` 當垃圾吃掉，下游拿 assrender（真的 libass）
把巢狀寫法、攤平寫法與這個 `,))` 版在 1920×1080 下各取 13 個時刻比對，遮罩逐像素相同（XOR 為 0）。
所以這件事的後果是「檔案不再是 Aegisub 會寫出的樣子」，不是渲染錯誤。

Aegisub 平常存檔寫的是 `Text` 原字串（只有真的編輯過 blocks 才 `UpdateText` 回寫），所以在 Aegisub 裡
踩不到；`AssFile.serialize()` 能逐位元組相同也是同一個原因。巢狀 `\t` 的參數也會錯位（前半多一個空的
accel，後半多一個 `)` 參數）。

所以提供第二種切法：`parse_override_tags(text, strict=True)`（`parse_dialogue_blocks` 也收）**真的計算
括號**，上面那一行會保持成一個 `\t`（巢狀動畫留在最後一個參數裡），而且 `"".join(str(t) for t in tags)`
等於原字串 —— 要重組、重排自己的行就用它。`set_strict_tag_parsing(True)` 可以把「沒傳 `strict=`」時的
預設整個行程切成這一種（回傳舊值；`strict_tag_parsing()` 讀目前值），給「決定全程都要正確切割」的程式用；
單次呼叫請直接傳 `strict=True`。

預設**永遠是** Aegisub 那一種（`strict=False`）：這個工具的保證是「輸出和 Aegisub 一致」，而這就是 Aegisub
看到的切法（libass 自己怎麼切沒有量過；上面的渲染比對只說明那三種寫法畫出來一樣）。切換會影響整個行程裡所有解標籤的地方（`parse_karaoke_syllables`、
`AssDialogue.get_stripped_text` 都算），逐位元組的輸出只在預設下測過，所以渲染流程不要動它；要動就在
啟動時動一次。另外 `strict=True` 只修正**切法**，不修正正規化：原字串本來就少一個 `)` 時，
`AssOverrideTag::operator std::string()` 照樣會補上。

下游的字幕工具集拿 1181 個真的 .ass、820573 行事件量過：10230 行會中這個形狀，全是 OP/ED 的
fx 與 template 行。`tests/test_ass.py::test_parse_override_tags_nested_transform` 把這個行為釘住。

## 自動化：巨集與匯出濾鏡（`automation/`）

`automation/` 對應上游的 `namespace Automation4`：一個腳本能提供的兩種東西 —— 巨集
（`register_macro`）與匯出濾鏡（`register_filter`）—— 的基底類別、登記表、內建的那幾個、
`.py` automation 檔案，以及「名字 → 誰提供的」解析。Lua 那一半在 `host/` 與 `worker.py`。
（本工具自己會做的事在 `tools/`，那是 `macro` 子命令，和這裡的 `Macro` 基底類別不是同一件事。）

Aegisub 只有兩個 C++ 匯出濾鏡（`main.cpp` 的兩次 `AssExportFilterChain::Register`）：
`Fix Styles`（優先度 −5000）與 `Transform Framerate`（1000）。本專案多一個自己的 `Shift Times`
（0，就是 `macro offset`：同一段位移邏輯 `tools/offset.py::shift_events`，一邊當子命令、一邊當濾鏡；
它的 `from_args` 特地把被 `:` 拆開的時間戳接回去，因為那是它唯一的參數）。其餘出現在 File → Export 清單裡的
都是 autoload 腳本用 `aegisub.register_filter` 註冊的 Lua 濾鏡，本工具本來就能跑。
`automation/base.py` 是可繼承的 `ExportFilter` 與註冊表（插入點照優先度，就是 `Register` 的做法），
`fixstyle.py`／`framerate.py` 是那兩個的移植，`spec.py` 是參數語法，`resolve.py` 是名字解析與快取。

- **順序是寫的順序，不是優先度。** 對話框的清單預設照優先度排，但有上移／下移按鈕，
  而 `AssExporter::AddFilter` 是照清單當下的順序加入的 —— 所以「照使用者寫的順序跑」才是忠於上游。
- **參數語法借 ffmpeg 的 filtergraph**（`name=k=v:k=v,name`）。和 ffmpeg 不同的是跳脫只有一層：
  `\:` 在哪裡都是冒號，不會因為巢狀深度而要寫成 `\\\\:`。濾鏡名稱含空白是常態
  （`Fix Styles`、`Clean Tags - 整理特效標籤`），所以空白不是分隔符；要跳脫的是 `, : = \ '`。
- **只有 Python 濾鏡吃參數。** Lua 濾鏡的設定在 Aegisub 來自它自己的設定對話框
  （`register_filter` 的第五個引數），headless 沒有東西可以驅動 —— 上游同樣拿不到，所以這裡不發明
  傳法（`host.run_filter` 照舊傳空 table），給了參數就報錯。需要參數的濾鏡寫成 Python：內建的兩個，
  或 autoload 目錄裡的 `.py`（`automation/plugin.py`，module 自己呼叫 `register_filter`）。`.py`
  只要 import、不必開 Lua state，所以它們**一律**會被載入，不受 `--scan-autoload` 管；
  Aegisub 只認 `.lua`／`.moon`，會忽略它們，兩邊可以放在同一個目錄。
- **兩種基底類別，照 Lua API 的兩種註冊**：`Macro`（`process_macro` ＋ 可選的 `validate_macro`）與
  `ExportFilter`（`process_filter`），`class MyCoolTool(Macro, ExportFilter)` 是合法的 ——
  一個 Lua 腳本本來就可以同時 `register_macro` 與 `register_filter`。`register()` 依類別是哪一種
  （或兩種）丟進對應的登記表。巨集那條路以無引數建構，所以兩用類別的選項都要有預設值。
  `selected_lines`／`active_line` 留在簽章裡但目前一律傳 `[]`／`0`：這個工具還沒有選取的概念。
  「內建還是來自某個檔案」是另一個軸，不是 Lua API 的分界，所以用 `is_builtin()` 看
  `__module__` 判斷就好（外掛會以套件外的模組名 import，見下）。
  `.py` 不是「被禁止的 automation」：`--script some.py` 會載入它、列出它註冊的東西，跑巨集時沒有
  才說「這個檔案沒有巨集」 —— 誠實的失敗，而不是預先禁止。
- **自動搜尋的副檔名集中在 `script_render.automation_files()`**：Lua 的 `.lua`／`.moon` 與外掛的
  `.py` 由同一個函式列出（濾鏡掃描的 stat 快取也用它）。既然 `.py` 現在會合法地出現在 autoload
  目錄裡，`resolve_script()` 就得知道自己要的是哪一種：它多了 `suffixes` 參數，
  `--script some-plugin.py` 不再被當成 Lua 腳本載入，而是回一句「那是 Python 濾鏡外掛，
  用 `--export-filters` 叫它的名字」。
- **巨集與濾鏡分給兩個子命令**：`render` 永遠先跑巨集（沒指定就是 config 的預設腳本與巨集，
  這是它一直以來的行為），濾鏡鏈接在後面；`convert` 不跑巨集，就是 File → Export。
  所以「只跑某個濾鏡」是 `convert --export-filters ...`，不需要任何讓 `render` 跳過巨集的開關，
  也不會出現 `--script kara-templater.lua --export-filters "Karaoke template"` 這種把同一件事
  做兩次的寫法。`convert` 只有在鏈裡有非內建濾鏡時才去找 Aegisub 安裝
  （`resolve_builtin_chain` 先試一次），所以純格式轉換與純內建濾鏡都不需要裝 Aegisub。
- **檔案自己記的那串濾鏡**：Aegisub 的匯出對話框會把勾選的名字以 `|` 串起來寫回
  `[Aegisub Project Garbage]` 的 `Export Filters:`（`dialog_export.cpp` 的解構子），下次開對話框時
  再勾回去。`--[no-]garbage-export-filters` 就是這個「再勾回去」：和 `--export-filter` 互斥
  （一個是跑我說的、一個是跑檔案記的），兩個都沒給時只有互動終端機會問，其他情況印一行提示然後不跑 ——
  非互動的預設一定要是「不做」。那串只有名字沒有設定，所以 `Transform Framerate` 這種非參數不可的
  從那裡跑會報錯，這是誠實的結果而不是要補的洞。另外 Windows 的 `isatty()` 連 NUL 都說是終端機，
  所以「是不是真的互動」看的是讀進來是不是立刻 EOF。
- **額外的 autoload 目錄**（`--autoload-dirs`／`--autoload-dirs-prepend`、
  `AEGISUB_AUTOLOAD_DIRS[_PREPEND]`、`config.py` 的 `AUTOLOAD_DIRS[_PREPEND]`）：把只有這個工具看得懂的
  `.py` 塞進 Aegisub 安裝裡並不合理，所以可以指自己的資料夾。做法是在 `settings.effective_autoload()`
  把它們併進 Aegisub 的 `Path/Automation/Autoload` 選項字串裡 —— 下游（`--script` 的名字搜尋、
  `.py` 外掛載入、濾鏡掃描與它的快取鍵）全都已經在用 `automation_dirs()` 讀那個字串，所以一處改完
  到處生效，連快取失效都是自動的（目錄清單變了＝鍵變了）。`automation_dirs()` 順手去重。
- **名字解析與快取**：內建 → `--script` 的腳本 → 額外目錄的 `.py` 外掛（不需要 Aegisub）→
  Aegisub autoload 的 `.py` → （`--scan-autoload` 時）掃 autoload 的 Lua。
  `FilterResolver` 因此收一個 `env_factory`：`convert` 只有在真的需要知道 Aegisub 在哪時才去建
  `RenderEnvironment`，純內建濾鏡與純外部 `.py` 外掛完全不碰 Aegisub 安裝。
  掃描很貴（一個腳本一個 Lua state，而且 DependencyControl 被封印的腳本會載入失敗，只記一行訊息），
  所以結果寫進 `%TEMP%\aegisub-render\export-filters.json`，鍵是 autoload 目錄清單加上每個腳本的
  mtime 與大小；取用時還會再確認那個腳本真的仍註冊著這個名字，否則整份快取丟掉重掃。
- **`Transform Framerate` 需要 `ass/override.py` 的寫回能力**：`AssOverrideParameter::Set`
  （`set_str`／`set_int`／`set_double`／`set_bool`）、BLOCK 參數的惰性 `get_block()`、
  `AssDialogueBlockOverride::ProcessParameters`（會遞迴進 `\t`）與 `AssDialogue::UpdateText`。
  `UpdateText` 是從 blocks 重建 `Text`，所以會踩到〈覆寫標籤：切 token 不是逐位元組安全的〉
  那個坑 —— 這是 Aegisub 自己的行為（它平常根本不重建 `Text`），`--strict`／
  `set_strict_tag_parsing(True)` 可以讓巢狀 `\t(...)` 原封不動地接回去。
- 移植時照抄的細節：`trunc_cs` 與卡拉OK分支用 C 的除法（往零取整，所以用 `_crt.cdiv`）、
  `RELATIVE_TIME_START` 的「非零不可變成零」、結束時間加 9 再截到 cs、`float_to_string` 的
  `%.3f` 去尾零。

## 其他字幕格式（`ass/formats/`）

`.srt`、`.ttxt`、`.txt`、`.transtation.txt` 照 `src/subtitle_format_*.cpp` 移植，`.vtt` 與 `.mkv`
是本專案補的。共用的那一半（`SubtitleFormat` 的 Sort／StripComments／RecombineOverlaps／
MergeIdentical／ConvertNewlines／StripTags／CanSave）在 `formats/base.py`，是一個可繼承的
abstract class：加格式或改格式都是寫一個子類別再 `register_format()`，和上游用
`LoadFormats` 註冊、`GetReader`／`GetWriter` 依副檔名找第一個接受者一樣。

幾個刻意的決定：

- **`TagMode`：匯出預設保留 `{...}`。** 上游的 exporter 要嘛剝掉全部標籤（ttxt／txt／transtation），
  要嘛只翻譯 `.srt` 認得的 `\b\i\u\s`（`ConvertTags`）。但這些檔案的實際消費者是播放器，而播放器的
  字幕解析器多半是 libass，看得懂覆寫標籤；剝掉等於無謂地丟掉定位、字型、卡拉OK。所以
  `.srt`／`.vtt`／`.ttxt` 預設 `keep`，`.txt`／TranStation（給人讀的）預設 `strip`，
  `html` 則是上游那條路。沒有一個 writer 寫樣式表 —— 這些格式都沒有能表達 ASS 樣式的欄位。
- **`RecombineOverlaps` 用索引而不是連結串列。** 上游走的是 boost intrusive list，兩個 iterator
  加上「解構即從串列移除」；移植時保留同一套語意：`nxt` 就是 `next` iterator 的索引，所有插入都落在
  它之後（所以不會動到它），最後兩行移除讓它退兩格。
- **`.ttxt` 的 XML 不保證逐位元組相同。** 上游用 wxWidgets 的 `wxXmlDocument::Save`，要逐位元組相同
  得連 wx 的 XML writer 一起移植。結構、屬性、順序（包含 `WriteHeader` 把 `TextSampleDescription`
  塞進 `TextStreamHeader` 裡那個巢狀）都照抄，縮排與換行照 wx 的樣子（兩格、LF、沒有 BOM、
  只有文字子節點的元素寫成一行）。另外 `Timing/Default Duration` 在上游其實也不會寫進檔案
  （結尾那個空 sample 只寫 `sampleTime`，也就是它的 start），`default_duration` 保留是為了忠於上游。
- **TranStation 的幀率是參數。** 上游用 `AskForFPS` 對話框（不給 VFR 選項）。這裡是 `fps=`／
  `--fps`／`--timecodes`，而且不拒絕 VFR 時間碼檔；`SmpteFormatter`、`Framerate.smpte_at_frame`／
  `frame_at_smpte` 都照 `libaegisub` 移植（含 NTSC drop-frame）。
- **Matroska 用 PyAV。** 上游自帶 `MatroskaParser.c`（3350 行）＋`mkv_wrap.cpp`，照移植不值得；
  ffmpeg 的 matroska demuxer 交出來的東西剛好就是 `MatroskaWrapper::GetSubtitles` 要的：
  CodecPrivate 當 extradata、每個 block 是原始的 `ReadOrder,Layer,Style,...` 行。所以移植的是外圈：
  extradata 逐行餵給 `AssParser`（軌道自己的樣式與 script info 就跟著過來）、`[Events]`、
  依 block 的時間戳與第二個逗號之後的內容組 `Dialogue:` 行、照 ReadOrder 排序；`S_TEXT/UTF8` 走
  `SrtTagParser` 並把換行換成 `\N`，64 MiB 上限也照上游。多軌時預設第一條（上游會問）。
- **WebVTT 的對應規則**（上游沒有這個格式）整理在 README 的「WebVTT 怎麼對應到 ASS」。兩個值得記的
  選擇：margin 是 script 單位，所以只有真的用到定位的檔案才補 `PlayResX`／`PlayResY`（預設 1280x720），
  沒定位的檔案保持和 `LoadDefault` 一樣不帶解析度；寫出時 `<` 與 `>` escape 成 `&lt;`／`&gt;`
  （順便擋掉文字裡的 `-->`），但 `keep` 模式下 `&` 不 escape —— `{\c&H00FF00&}` 必須原樣送到播放器，
  而 WebVTT 的解析器對落單的 `&` 本來就有錯誤回復。

讀取端一律只接受 UTF-8（BOM 可有可無）與帶 BOM 的 UTF-16，和 `.ass` 一樣不做 Aegisub 的編碼偵測；
寫出端一律 UTF-8 BOM + CRLF（`TextFileWriter` 在 Windows 的行為），`.ttxt` 例外（XML，LF、無 BOM）。

## 內建巨集（`macro`）

`aegisub-render macro` 是不需要 Aegisub 腳本、也不啟動 Lua worker 的 Python 操作（`src/aegisub_render/tools/`）。
每個子指令的參數定義與 CLI 入口都放在 `src/aegisub_render/tools/` 對應模組內，並透過 `CommandRegistry.register_command`
註冊（頂層 CLI 同樣以此註冊並經由 `args.handler` 直接派發）；`aegisub-render --help` 只在 `macro` 說明印出已註冊的巨集名稱，
執行 `aegisub-render macro`（或 `aegisub-render macro --help`）時才列出每個子指令的用途。
輸出沿用 `render` 的路徑（`runner._read_input_ass`／`_write_output`：就地覆寫、`-o`、`-o -`、`-y`/`-n`、`--dry-run`），
寫出時照 Save（`serialize_ass`）。

- **`macro offset`**：把所有 `[Events]` 行（含 Comment）的開始／結束時間平移。
  - 時間：`[+-][[[hh:]mm:]ss].ms`，必須有小數點以和幀數區分；小數取到毫秒（`.5` = 500 ms）。
  - 幀數：`[+-]frames`，需要影片、時間碼或 `--fps`（影片參數與連結檔的處理和 `render` 相同），否則報錯且不寫檔。
    新時間是目標幀的 `Framerate.time_at_frame(i, START)`（`i > 0` 時是前後兩幀時間的中點，`i <= 0` 為 0）。
  - 結果夾限在 `0 .. 9:59:59.99`。
- **`macro measure`**：輸出 JSON。
  - 樣式來自參數（`--font`/`--fontsize`…）或 `--ass` 檔裡的樣式（`--style`，同時取得 PlayRes），各欄位可再覆寫；
    粗體照 libass 讀 `\b` 的方式（`text_extents.font_weight`）：0 一般（400）、1 粗體（700）、大於 1 就是字重本身且
    不設上限——`\b9999` 會要求比最粗的字面還粗，GDI 會在 Black 字面上再加粗（量起來更寬），fontconfig 則取最粗的字面。
    `text_extents` 本身拿到的是 Aegisub 樣式的 bool（`!!next_int()`），所以只會是 400／700，結果與以前相同；
    `--video-size` 時照 karaskel 的公式加上水平修正係數。
  - 寬高與逐字位置用和 `text_extents` 相同的 GDI 量測（64 倍字號）；整行寬度以整行一次量測為準，逐字位置是逐字累加。
  - 筆畫留白用 `_font.glyph_ink_boxes`（Windows 分支：`GetGlyphOutlineW` + `GGO_NATIVE`，點陣字型退回 `GGO_METRICS`）；
    空白字元沒有筆畫；筆畫超出字元格時留白忠實地是負值（左＋筆畫＋右恆等於寬）。
  - 位置照 `karaskel.preproc_line_pos`：`--an` + `--margins`（含有效邊距）或 `--an` + `--pos`；有版面時逐字附上
    `sleft`/`scenter`/… 等畫面座標。
- CLI 把 `-` 後接數字或小數點的參數一律視為值（`_negative_number_matcher`），否則 `-0:01:00.00` 會被當成旗標。

## 測試

`scripts/gen_cases.py` 以固定 seed 產生案例。`tests/aegisub/` 是 Aegisub 3.5.1 以 `Karaoke template` 濾鏡匯出的結果（與 3.5.0 匯出的檔案只差 header 的版本號）：
`--filter` 的輸出必須與它逐位元組相同（只差 header 那行），巨集的輸出去掉 Project Garbage 段落後也必須相同；
snapshot 記錄巨集的完整輸出。
