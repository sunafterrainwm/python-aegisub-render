# aegisub-render

在 Python 裡執行 Aegisub 3.5.1 的 Automation 4 腳本（預設是 kara-templater），不開 GUI。

- Lua 執行環境就是 Aegisub 3.5.1 用的那一個：LuaJIT 2.1 @ `04dca79`，`lua52compat`、GC64、
  lpeg 0.10、luabins，連同 MoonScript 0.7.0。所以 `.0` 不會出現在數字字串裡、`string.format("%d", 1.5)`、
  `math.random` 的序列（含 `math.randomseed` 後的結果）都和 Aegisub 一致。
- 腳本、karaskel、`aegisub/*.moon` 等全部從 Aegisub 安裝目錄（與 %APPDATA%）原封不動地載入；
  Aegisub 在 C++ 裡做的部分（`aegisub.*`、字幕物件、`aegisub.re/unicode/lfs` 背後的 C 函式、
  ASS 讀寫、`text_extents`）以 v3.5.1 原始碼為準重新實作。
- 目標是輸出與「在 Aegisub 3.5.1 套用後存檔」逐位元組相同（UTF-8 BOM、CRLF、Aegisub 的欄位格式）。

## 需求

- Windows x64（`text_extents` 用 GDI；repo 內附為 CPython 3.14 / win_amd64 編譯好的 Cython 擴充模組），
  或 Linux／WSL（自行編譯，見下方「Linux／WSL」）
- Python 3.14、[uv](https://docs.astral.sh/uv/)
- Aegisub 3.5.1 安裝目錄（預設 `C:\Program Files\Aegisub`）

```powershell
uv sync
uv run aegisub-render.py render input.ass -o output.ass
```

從 repo 執行：`aegisub-render.py` 會把 repo 的 `src/` 加進 `sys.path`，所以在任何工作目錄都能用；`uv sync` 之後
repo 本身也以連結的方式裝進 `.venv`，所以 `uv run aegisub-render ...`、`uv run python -m aegisub_render ...`
在任何工作目錄都可以用。

### 在別的專案裡使用

只支援「連結到這個 repo」的安裝方式（uv 的 editable install，會在對方環境放一個指向本 repo 的 `.pth`）：

```powershell
uv add --editable <repo>
```

之後 `import aegisub_render` 與 `aegisub-render` 指令都直接用 repo 裡的檔案，repo 更新即生效。
要探測真的影片（`render` 讀專案的 Video File、`--video`）還需要 PyAV，它是 `video` extra：
`uv add --editable "<repo>[video]"`。只借 `macro measure` / `macro offset`
這類純 Python 功能的話不必裝（缺了只會在要開影片時以警告說明）。
**不要**建置或發佈 wheel：`src/aegisub_render/**/*.pyd`、`*.so` 是 `native/build.py` 針對單一直譯器編好後
commit 進 repo 的，不是安裝時才編譯，所以 wheel 的平台／ABI 標記會是錯的。純 `pip install` 沒有測試過。

套件放在 `src/aegisub_render`（src layout），所以這個連結只讓 `aegisub_render` 這一個名稱進入對方專案，repo 根目錄的 `config.py` 之類的檔案不會變成可 import 的頂層模組。

要注意的是：預設設定檔仍然是 **repo 根目錄的 `config.py`**（`src/aegisub_render/settings.py` 以套件位置
往上兩層推算），要換成自己的就用 `--config PATH` 或環境變數 `AEGISUB_RENDER_CONFIG`。

另外，VS Code 的 pyright／Pylance／basedpyright 擴充吃不到 uv editable 安裝放的 `.pth`（命令列的
pyright 其實認得），所以要在 VS Code 開的 workspace 根目錄自己把本 repo 的 `src` 加進搜尋路徑，
二選一（路徑都相對於那個根目錄）：

```jsonc
// .vscode/settings.json
{
  "python.analysis.extraPaths": ["../git/python-aegisub-render/src"],
  "python.defaultInterpreterPath": ".venv/Scripts/python.exe"
}
```

```json
// pyrightconfig.json（命令列的 pyright 也讀得到，但它存在時 Pylance 會完全忽略 python.analysis.*）
{
  "venvPath": ".",
  "venv": ".venv",
  "extraPaths": ["../git/python-aegisub-render/src"]
}
```

兩個都放只會讓 `python.analysis.*` 變成死設定，挑一個就好。寫在 `pyproject.toml` 的 `[tool.pyright]`
不行 —— 實測 basedpyright 不讀那一節。

### aegisub-render.exe

```powershell
uv run python scripts/build_exe.py            # 產生 repo 根目錄的 aegisub-render.exe（已 gitignore）
uv run python scripts/build_exe.py -o C:\tools\aegisub-render.exe
```

這個 `.exe` 等同 `.venv\Scripts\python.exe aegisub-render.py ...`：用的是 pip 放在 console script 前面的同一種
啟動器（distlib 的 `t64.exe`），後面接上 `#!<python.exe>` 與一個執行 `aegisub-render.py` 的 `__main__.py`。
結束碼與 Ctrl+C 會原樣傳遞。路徑都是絕對路徑，所以可以複製到 PATH 裡的目錄使用；repo 或 `.venv` 搬家後要重新產生。
`--python PATH` 可指定別的直譯器。

## 使用方式

```powershell
# 用 kara-templater 套用模板（等同 Automation > Apply karaoke template，然後存檔），直接覆寫 song.ass
uv run aegisub-render.py render song.ass

# 寫到另一個檔案；檔案已存在時會詢問，-y 直接覆寫、-n 不覆寫並以錯誤結束
uv run aegisub-render.py render song.ass -o song.fx.ass -y

# 不寫檔，只把結果與原檔的差異印到 stderr
uv run aegisub-render.py render song.ass --dry-run

# 結果寫到 stdout
uv run aegisub-render.py render song.ass -o - > preview.ass

# 其他腳本 / 巨集
uv run aegisub-render.py render in.ass -o out.ass --script strip-tags.lua --macro "Strip tags" --select 1,3-5

# 只跑匯出濾鏡（＝File → Export）：用 convert，不會跑任何巨集。語法照 ffmpeg 的 filtergraph
uv run aegisub-render.py convert in.ass -o out.ass \
    --export-filters "Transform Framerate=input=23.976:output=25,Fix Styles"
# Lua 腳本註冊的濾鏡（卡拉OK範本、Clean Tags…）要先讓它找到是誰註冊的：掃一次 autoload（會快取）
uv run aegisub-render.py convert in.ass -o out.ass --scan-autoload --export-filters "Karaoke template"
# 跑完巨集「之後」再跑濾鏡：用 render
uv run aegisub-render.py render in.ass -o out.ass --export-filters "Fix Styles"

# 列出腳本註冊的巨集與匯出濾鏡
uv run aegisub-render.py list in.ass --script kara-templater.lua

# 轉格式（不啟動 Aegisub／Lua）：格式看 --output 的副檔名
uv run aegisub-render.py convert in.ass -o out.srt
uv run aegisub-render.py convert in.srt -o out.ass
uv run aegisub-render.py convert in.vtt -o out.ass            # 帶樣式的 VTT 會轉成 Style 與覆寫標籤
uv run aegisub-render.py convert in.mkv -o out.ass --track 3   # 讀 Matroska 裡的字幕軌（需要 video extra）
uv run aegisub-render.py convert in.ass -o out.transtation.txt --fps 23.976
uv run aegisub-render.py convert in.ass -o out.srt --tags html # {\b1} → <b>，其餘標籤丟掉（Aegisub 的做法）
uv run aegisub-render.py convert in.ass -o - --to vtt > out.vtt

# render 的 -o 也認副檔名：跑完腳本直接吐 .srt
uv run aegisub-render.py render song.ass -o song.srt

# 不啟動 Aegisub / Lua worker，直接以時間戳（[+-][[[hh:]mm:]ss].ms）或幀數（[+-]frames）偏移字幕時間
uv run aegisub-render.py macro offset in.ass +0:00:01.50 -o out.ass
uv run aegisub-render.py macro offset in.ass -24 --fps 24000/1001 -o out.ass

# 量測文字長寬、筆畫上下左右留白、逐字位置，以及 karaskel.lua 相容的對齊座標（輸出 JSON）
uv run aegisub-render.py macro measure "測試文字" --font "Microsoft JhengHei" --fontsize 48 --an 2 --margins 10,10,20
uv run aegisub-render.py macro measure "測試文字" --ass in.ass --style Default --an 5 --pos 640,360
```

`--script` 不是路徑時，依序在 `?user`、`?data` 的 autoload 目錄找（與 Aegisub 的 `Path/Automation/Autoload` 設定相同）。

結束碼：`0` 成功、`1` 錯誤（Lua 執行錯誤、腳本載入失敗、逾時…）、`2` 腳本呼叫了 `aegisub.cancel()`。
失敗或取消時不會寫出任何檔案（輸出是原子性寫入）。

### 主要參數

| 參數 | 說明 |
|---|---|
| `--config PATH` | 設定檔，預設為環境變數 `AEGISUB_RENDER_CONFIG` 或本專案根目錄的 `config.py` |
| `--aegisub-root DIR`、`--load-appdata` / `--no-load-appdata` | 覆蓋 `config.py` 的 `AEGISUB_ROOT`、`LOAD_APPDATA` |
| `-o`/`--output PATH` | 輸出檔；省略時覆寫輸入檔；`-` 表示寫到 stdout（與寫檔時逐位元組相同；檔名真的是 `-` 時寫 `./-`）。連結檔案（影片、時間碼、關鍵影格、音訊）的相對路徑會改成相對於輸出檔所在的目錄，與 Aegisub 另存新檔相同；寫到 stdout 時相對於輸入檔。沒能載入的連結（檔案不存在、`--no-video`、`--fps`）照原樣保留，不像 Aegisub 存檔時會刪掉 |
| `-y`/`--override`、`-n`/`--no-override` | `--output` 檔案已存在時直接覆寫 / 不覆寫並以錯誤結束（不會執行腳本）；預設詢問，stdin 不是終端機時視為不覆寫。沒有 `--output`、`--output -` 或 `--dry-run` 時不起作用 |
| `--dry-run` | 不寫任何東西（包括 `--output -` 的 stdout），改把輸入檔到結果的 unified diff 印到 stderr；`;` 開頭的行（註解與 header）不列入比較，沒有差異時印 `No differences found.`；即使 `-q` 也會印 |
| `--keep-brand` / `--no-keep-brand` | `[Script Info]` 開頭的註解（`; Script generated by ...`）怎麼寫。`--keep-brand`：輸入檔有什麼就寫什麼（沒有就不寫）。`--no-keep-brand`（預設）：輸入檔原本是 `; Script generated by Aegisub <ver>` 時保留它的版本，在後面加上 ` with aegisub-render <ver>`（已經有的話換成目前的版本），其後的註解行原樣保留（例如舊版的 `; http://www.aegisub.org/`）；否則整個改寫成 `; Script generated by Aegisub 3.5.1 with aegisub-render <ver>` 與 `; https://aegisub.org/` 兩行。`render` 與 `macro offset` 都適用 |
| `--macro NAME` | 要執行的巨集（預設 `Apply karaoke template`），之後像存檔一樣寫出 |
| `--export-filter CHAIN` / `--export-filters CHAIN` | 要跑的匯出濾鏡鏈，語法見「匯出濾鏡」。`render` 是巨集跑完之後跑，`convert` 則只跑濾鏡。有這個參數時 `.ass` 會像 File → Export 一樣寫出（輸入檔沒有的 `[Aegisub Project Garbage]`、`[Aegisub Extradata]` 不會寫出，有的則照存檔的方式保留） |
| `--autoload-dir DIR` / `--autoload-dirs DIR` | 額外的 automation autoload 目錄，排在 Aegisub 自己的之後（`--autoload-dir-prepend` 則排在前面）。`--script` 會在裡面依名字找 `.lua`／`.moon`／`.py`，裡面的 `.py` 濾鏡外掛也會被載入。可重複；環境變數 `AEGISUB_AUTOLOAD_DIRS`／`AEGISUB_AUTOLOAD_DIRS_PREPEND`（以 `;`／`:` 分隔）與 `config.py` 的 `AUTOLOAD_DIRS`／`AUTOLOAD_DIRS_PREPEND` 同義 |
| `--garbage-export-filters` / `--no-garbage-export-filters` | 跑檔案自己記在 `[Aegisub Project Garbage]` `Export Filters:` 裡的那串濾鏡（Aegisub 上次在匯出對話框勾的）。**不能和 `--export-filter` 併用**；都不給時，互動終端機會問，其他情況不跑 |
| `--scan-autoload` / `--no-scan-autoload` | `--export-filters` 的名字不是內建濾鏡時，是否載入所有 autoload 腳本去找是誰註冊的（慢，結果會快取）。預設 `config.py` 的 `SCAN_AUTOLOAD`（False） |
| `--strict` / `--no-strict` | 解析 `{...}` 覆寫標籤時真的算括號（`set_strict_tag_parsing`）。預設 `config.py` 的 `STRICT_TAG_PARSING`（False＝照 Aegisub） |
| `--select 1,3-5` | 選取的對白行（1 起算，只算 Dialogue/Comment 行）；預設全選 |
| `--force` | 巨集的 validation 函式回傳 false 時仍然執行（否則只印一行說明並以結束碼 1 結束；kara-templater 在 `[Events]` 前 51 行沒有 Effect 以 `template` 開頭的行時會回傳 false） |
| `--trace-level N`、`-v`、`-q` | `aegisub.debug.out` 的門檻（預設 3，同 Aegisub）；`-q` 只留錯誤與腳本輸出 |
| `--no-progress` | 不顯示進度條。stderr 是終端機時，巨集或濾鏡執行期間會在 stderr 顯示 tqdm 進度條（標題是巨集名稱，後面接 `aegisub.progress.task` 的文字），結束後留在畫面上：成功時填滿到 100%，失敗時停在當時的進度；腳本的輸出印在進度條上方 |
| `--timeout`、`--memory-limit`、`--max-output-lines`、`--max-input-lines` | 覆蓋資源限制 |
| `--font-dir DIR` | 額外字型目錄（以 `FR_PRIVATE` 暫時註冊給 `text_extents`），可重複 |
| `--video PATH` / `--no-video` | 覆蓋專案的 Video File（也接受 `?dummy:...`）/ 當作沒有影片 |
| `--video-size WxH`、`--fps F`、`--timecodes FILE`、`--keyframes FILE` | 手動指定影片資訊 |
| `--refresh-cache`、`--no-cache` | 影片資訊快取（`%TEMP%\aegisub-render\<sha256>.json`） |

### Python API

用上面的 `uv add --editable` 安裝，或讓 repo 的 `src/` 在 `sys.path` 上（例如 `aegisub-render.py` 的做法）：

```python
from pathlib import Path
from aegisub_render import (
    ExitCode,
    MarginLayout,
    MeasureStyle,
    PosLayout,
    RenderOptions,
    load_settings,
    measure_text,
    offset,
    render,
)

code = render(Path("song.ass"), Path("song.fx.ass"), load_settings(), RenderOptions(trace_level=4))
assert code is ExitCode.OK

code = offset(Path("song.ass"), Path("song.shifted.ass"), "+0:00:01.50")
assert code is ExitCode.OK

metrics = measure_text(
    MeasureStyle(font="Microsoft JhengHei", fontsize=48, bold=True, spacing=1.0),
    "測試",
    MarginLayout(align=2, margin_l=10, margin_r=10, margin_v=20, res_x=1920, res_y=1080),
)
print(metrics.to_json())
```

要自己控制「跑腳本」這一步（而不是用 `render()` 連檔案一起處理）就用 `ScriptRender`：它吃一個
記憶體裡的 `AssFile`，跑完**就地更新**它，序列化與寫檔都留給呼叫端。

```python
from pathlib import Path
from aegisub_render import AssFile, ScriptRender, load_settings
from aegisub_render.ass.writer import serialize_ass

ass = AssFile.from_path(Path("song.ass"))
with ScriptRender(ass, "kara-templater.lua", load_settings(), subtitle_path=Path("song.ass")) as sr:
    if not sr.macro_can_run():  # 巨集的 validation 函式（Aegisub 會把選單變灰的那個）
        raise RuntimeError(sr.refusal_reason())
    sr.run()  # ass 被就地更新
Path("song.fx.ass").write_bytes(serialize_ass(ass, sr.save_ui_state))
```

`macro_can_run()` 與 `run()` 共用同一個 Lua state（和 Aegisub 一樣）；`features()` 列出腳本註冊的巨集與
濾鏡；`ScriptRefused`／`ScriptCancelled`／`ScriptFailed` 分別對應「沒跑」「腳本呼叫 `aegisub.cancel()`」
「腳本失敗或超限」，`RenderError` 是連 Lua 都還沒開始就出錯（找不到腳本、Aegisub 安裝不對）。
再跑一次 `run()` 等於在 Aegisub 裡再按一次巨集。

`ass/` 的模型類別也都從套件根匯出（`AssFile`、`AssInfo`、`AssStyle`、`AssDialogue`、`AssAttachment`、
`ExtradataEntry`、`ProjectProperties`、`Group`、`Color`、`KaraokeSyllable`、`OverrideTag`、`OverrideParam`、
`Block`、`BlockType`、`VarType`、`ParamClass`、`SubtitleFormatParseError`、`UnsupportedEncodingError`），
讀寫則掛在 `AssFile` 上（內部以執行期 import 呼叫 `ass.parser` / `ass.writer`，避免循環匯入）：

```python
from pathlib import Path
from aegisub_render import AssFile

ass = AssFile.from_path(Path("song.ass"))  # .ssa 會以 SSA（version 0）解析
ass = AssFile.from_bytes(Path("song.ass").read_bytes())  # UTF-8（BOM 可有可無）或帶 BOM 的 UTF-16
ass = AssFile.from_text(text, version=1)  # 已經解好碼的文字

for line in ass.events:
    line.text = line.text.replace("foo", "bar")

Path("out.ass").write_bytes(ass.serialize())  # 像 Aegisub 存檔（UTF-8 BOM + CRLF）
Path("out.ass").write_bytes(ass.export())  # 像 File → Export
```

`serialize()` / `export()` 的參數與 `ass.writer.serialize_ass` / `serialize_export` 相同
（`save_ui_state`、`keep_brand`）；更低階的 `parse_ass_lines`、`decode_subtitle_bytes`、
`split_subtitle_text`、`write_ass_file` 等仍在 `aegisub_render.ass` 裡。

#### 解析成自己的類別

`AssParser` 建立的每一個物件都經過一個 `make_*` 掛鉤，所以**不用改解析邏輯**就能換掉結果的類別：

```python
from typing import override
from aegisub_render import AssAttachment, AssDialogue, AssFile, AssParser, Group


class MyAssAttachment(AssAttachment): ...


class MyAssDialogue(AssDialogue): ...


class MyAssFile(AssFile): ...


class MyAssParser(AssParser):
    @classmethod
    @override
    def make_file(cls) -> MyAssFile:  # 整份檔案
        return MyAssFile()

    @override
    def make_attachment(self, data: str, group: Group) -> MyAssAttachment:
        return MyAssAttachment(data, group)

    @override
    def make_dialogue(self, data: str) -> MyAssDialogue:
        return MyAssDialogue.parse(data)  # .parse 是 classmethod，會回傳 cls()


ass = AssFile.from_path(Path("song.ass"), parser_cls=MyAssParser)
assert type(ass) is MyAssFile and type(ass.events[0]) is MyAssDialogue
```

掛鉤共六個：`make_file`（classmethod）、`make_info`、`make_style(data, version)`、`make_dialogue`、
`make_attachment(data, group)`、`make_extradata(id, key, value)`；只覆寫要換的那幾個就好。
`parser_cls` 可以傳給 `AssFile.from_text` / `from_bytes` / `from_path` 與
`parse_ass_text` / `parse_ass_bytes` / `parse_ass_lines` / `read_ass_file`，不傳就是原本的類別。
`AssStyle.parse` 與 `AssDialogue.parse` 是 classmethod（用 `cls(...)` 建物件），所以子類別直接呼叫
`.parse()` 就會得到自己的型別；`AssInfo.copy()` 同理。這些模型類別都是 dataclass，其中 `AssStyle`
與 `AssDialogue` 是 `kw_only=True`（欄位全都有預設值，`AssStyle()`／`AssDialogue()` 仍可直接建），
所以子類別新增的欄位也要有預設值，`.parse()` 才建得起來。

#### 其他 Aegisub 有的操作

```python
line.get_stripped_text()  # AssDialogue::GetStrippedText：只留 plain 區塊
line.strip_tags()  # AssDialogue::StripTags：把 Text 換成上面的結果
line.copy()  # 複製一行（像 Aegisub 的 copy ctor，id 是新的）
style.copy()  # 複製一個樣式
ass.copy()  # 複製整份檔案（events 會拿到新的 id）
ssa_to_ass_alignment(5)  # SSA 的對齊編號 → \an 的 1-9（舊式 \a 也是這套編號）
```

解析覆寫標籤有兩種切法：預設照 Aegisub（`ParseTags` 的 `depth` 只在 depth 0 加一，所以 kara-templater
寫的 `\t(0,248,\clip(...)\t(...))` 會被切成兩個 `\t`，接回去不是原字串 —— 細節與理由見
[docs/DESIGN.md](docs/DESIGN.md)），`parse_override_tags(text, strict=True)` 則真的計算括號，
一個 `\t` 就是一個 tag、`str(tag)` 接回去等於原字串（重排、重組自己的行就用這個）。
`set_strict_tag_parsing(True)` 把整個行程的預設換成後者（回傳舊值），`strict_tag_parsing()` 讀目前值；
渲染流程請保持預設。

`get_stripped_text()` 剝掉 `{}`（override 與 comment 區塊）與繪圖區塊，但 `\N`、`\n`、`\h`
**照原樣留著**（它們在 Aegisub 眼裡就是 plain 區塊裡的兩個字元，GetStrippedText 不動它們）——
要換成換行是呼叫端自己的規則，不是 Aegisub 的行為。

寫出的位元組固定是 UTF-8 BOM + CRLF（這是本工具的目標：和 Aegisub 存檔逐位元組相同）。
需要別的慣例就在呼叫端轉：

```python
Path("out.ass").write_bytes(ass.serialize().decode("utf-8-sig").replace("\r\n", "\n").encode("utf-8"))
```

## 匯出濾鏡

Aegisub 的 File → Export 會依序跑一串濾鏡，其中只有兩個是 Aegisub 自己用 C++ 寫的，其餘都是 Lua 腳本
以 `aegisub.register_filter` 註冊的。兩種本工具都跑：

| 濾鏡 | 優先度 | 來源 |
|---|---|---|
| `Transform Framerate` | 1000 | 內建（`export_framerate.cpp` 的移植） |
| `Shift Times` | 0 | 內建（本專案自己的，就是 `macro offset`） |
| `Fix Styles` | −5000 | 內建（`export_fixstyle.cpp` 的移植） |
| `Karaoke template`、`Clean Tags`… | 腳本自己定 | autoload 目錄裡的 Lua 腳本 |

`aegisub-render list in.ass --script <某腳本>` 會把兩者一起列出來（內建的標著 `built in`）。

### 語法

`--export-filters` 吃的是 **ffmpeg 的 filtergraph 寫法**：

```
名稱[=值[:值…][:鍵=值…]][,名稱…]
```

- 逗號串接多個濾鏡，**照寫的順序執行**（Aegisub 的對話框也是這樣：清單預設照優先度排，但有上移／下移可以改）；
- `=` 之後是參數，`:` 分隔，`鍵=值` 或依位置給；
- `\` 跳脫下一個字元，`'…'` 整段不解析（只有結尾的 `'` 特別）。名稱裡的**空白不用跳脫**，
  但 `, : = \ '` 要。**Windows 路徑一定要用 `'…'` 包起來**（`C:` 的冒號與反斜線都會被吃掉）；
- 這個參數可以重複給，等同用逗號接起來。

只有 **Python 濾鏡**（內建的兩個，或自己放的 `.py`）吃參數。Lua 濾鏡的設定在 Aegisub 是它自己的
對話框，headless 沒有東西可以驅動 —— 上游也拿不到，所以這裡不發明傳法，給 Lua 濾鏡參數會直接報錯；
需要參數的濾鏡請寫成 Python 的（見下面「自己寫一個」）。

```powershell
# 內建：把 23.976 的時間軸換算成 25
--export-filters "Transform Framerate=input=23.976:output=25"
--export-filters "Transform Framerate=23.976:25"              # 依位置：input, output, reverse
--export-filters "Transform Framerate=input=25:reverse=1"     # 少給的那邊＝專案的幀率
--export-filters "Transform Framerate=input='C:\tc\v2.txt':output=25"   # 時間碼檔要引號
# 多個濾鏡，照寫的順序
--export-filters "Transform Framerate=input=23.976:output=25,Fix Styles"
```

`Shift Times` 的參數：`by`（唯一一個，可依位置給），就是 `macro offset` 那個時間戳
（`[+-][[[hh:]mm:]ss].ms`）或幀數（`[+-]frames`）。時間戳裡的冒號**不用跳脫**（這個濾鏡只吃一個
參數，拆開的部分會自己接回去），要引號也行：

```powershell
--export-filters "Shift Times=+0:00:01.50"      # 等同 --export-filters "Shift Times='+0:00:01.50'"
--export-filters "Shift Times=-24" --fps 23.976  # 幀數位移要有幀率
```

`Transform Framerate` 的參數：`input`、`output`（幀率如 `23.976`、`24000/1001`，或 v1／v2 時間碼檔；
少給的那個就是專案自己的幀率 —— 影片、`--fps` 或 `--timecodes`），`reverse`（把兩者對調，
就是對話框的「逆轉換」）。兩個都不給等於什麼都不做，所以會報錯而不是默默跳過。
它會連覆寫標籤裡的時間（`\k`、`\fad`、`\move`、`\t`…）一起換算。

### `render` 與 `convert`

兩個子命令都吃 `--export-filters`，差別只在前面做了什麼：

- **`convert`** 不跑任何巨集，就是 **File → Export**：讀檔 → 濾鏡鏈 → 寫出（還可以順便換格式）。
  全部都是內建濾鏡的話連 Aegisub 都不用裝；有 Lua 濾鏡才需要（要找到註冊它的腳本）。
- **`render`** 一定會先跑巨集（預設就是 `config.py` 的 `kara-templater.lua` ＋
  `Apply karaoke template`），濾鏡鏈接在後面。

### 檔案自己記的那串

Aegisub 的匯出對話框會把這次勾選的濾鏡寫回檔案的 `[Aegisub Project Garbage]`：

```
Export Filters: 卡拉OK範本|修正樣式
```

`--garbage-export-filters` 就是跑這一串（`--no-` 則明確不跑）。兩個都不給、而且也沒有
`--export-filter` 時：互動終端機會問一句要不要跑，其他情況（導向、CI）印一行提示然後不跑。
`--export-filter` 與 `--garbage-export-filters` **不能併用** —— 一個是「跑我指定的」、
一個是「跑檔案記的」，同時給沒有意義。

注意這串只有名字、沒有設定（Aegisub 的設定在對話框裡，不會寫進檔案），所以像
`Transform Framerate` 這種一定要參數的濾鏡從這串跑會直接報錯，要自己用 `--export-filters` 指定。

### 名字是誰註冊的

依序找：**內建濾鏡** → **`--script` 指定的那個腳本註冊的濾鏡**（只有 `render` 有這個參數）→
**`--autoload-dirs` 等額外目錄裡的 `.py` 濾鏡外掛**（只要 import，而且不必知道 Aegisub 裝在哪）→
**Aegisub 自己的 autoload 目錄裡的 `.py`** → （只有在允許掃描時）**載入所有 autoload 的 Lua 腳本去問**。
所以 `convert` 只有在遇到前三步都找不到的名字時，才會真的去找 Aegisub 安裝。掃描預設關閉，因為每個 Lua 腳本都要開
一個 Lua state；`--scan-autoload` 打開，結果會寫進 `%TEMP%\aegisub-render\export-filters.json`，
以 autoload 目錄清單與每個腳本的大小／修改時間為鍵，所以改了腳本就自動失效；另外從快取取用時
還會再確認那個腳本真的還註冊著這個名字，不是的話就丟掉整份快取重掃。

### 自己寫一個

把這樣一個 `.py` 放進任何一個 autoload 目錄，`--export-filters "Drop Signs=OP"` 就會找到它 ——
不需要 `--scan-autoload`，因為 import 一個 `.py` 很便宜。建議**用 `--autoload-dirs`（或
`AEGISUB_AUTOLOAD_DIRS`、`config.py` 的 `AUTOLOAD_DIRS`）指一個自己的資料夾**，不必把只有這個工具
看得懂的檔案塞進 Aegisub 安裝裡；真的放進 Aegisub 的 autoload 也可以，Aegisub 只認 `.lua`／`.moon`，
會忽略它們。檔名以 `_` 開頭的會跳過，方便放私有的輔助模組。同一段程式當然也可以直接寫在自己的
Python 程式裡（import 之後呼叫 `register_filter`）。

`.py` 檔案可以註冊**巨集**與**匯出濾鏡**兩種，就像一個 Lua 腳本可以同時呼叫 `aegisub.register_macro`
與 `aegisub.register_filter`：對應的基底類別是 `Macro`（`process_macro` ＋ 可選的 `validate_macro`）
與 `ExportFilter`（`process_filter`）。一個類別可以同時是兩者。

`--script` 也吃 `.py`：`aegisub-render list in.ass --script my-tools.py` 會把它註冊的巨集與濾鏡
一起列出來，`render --script my-tools.py` 跑它的巨集（`--macro` 指定是哪一個，只有一個就不用指定），
`--export-filters` 則叫它的濾鏡。沒有巨集的檔案被要求跑巨集時會說「registers no macros」。

```python
# D:\my\filters\my-tools.py
from typing import ClassVar, override

from aegisub_render import AssFile
from aegisub_render.automation import ExportFilter, Macro, AutomationContext, register, register_filter


class DropSigns(ExportFilter):
    name: ClassVar[str] = "Drop Signs"
    description: ClassVar[str] = "Remove every line whose style starts with a prefix"
    priority: ClassVar[int] = 0
    positional: ClassVar[tuple[str, ...]] = ("prefix",)  # 可以依位置給的參數，依序

    def __init__(self, prefix: str = "Sign") -> None:  # 參數就是建構子的關鍵字引數
        self.prefix = prefix

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        file.events = [e for e in file.events if not e.style.startswith(self.prefix)]


register_filter(DropSigns)


class MyCoolTool(Macro, ExportFilter):  # 同一個類別兩種身分是合法的
    name: ClassVar[str] = "My Cool Tool"

    def __init__(self, suffix: str = "!") -> None:  # 巨集那條路無引數建構，所以都要有預設值
        self.suffix = suffix

    @override
    def validate_macro(self, file, selected_lines, active_line, ctx) -> bool:
        return bool(file.events)  # 不覆寫就是永遠可以跑

    @override
    def process_macro(self, file, selected_lines, active_line, ctx) -> None:
        self.process_filter(file, ctx)

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        for line in file.events:
            line.text += self.suffix


register(MyCoolTool)  # 兩邊都註冊（也可以分別呼叫 register_macro / register_filter）
```

`selected_lines` 與 `active_line` 是 Aegisub 交給巨集的「選取了哪幾行、作用中的是哪一行」。
本工具目前還沒有自己的選取概念，所以**一律傳 `[]` 與 `0`** —— 參數留在簽章裡是因為那是契約，
不是因為它現在帶著東西。

## 其他字幕格式

除了 `.ass`，`aegisub_render.ass.formats` 還有這些格式。每一個都是 `SubtitleFormat`
（`src/subtitle_format.cpp` 的移植）的子類別：

| 副檔名 | 讀 | 寫 | 來源 |
|---|---|---|---|
| `.ass` / `.ssa` | ✓ | `.ass` | `ass.parser` / `ass.writer`（`.ssa` 只讀不寫） |
| `.srt` | ✓ | ✓ | 照 `subtitle_format_srt.cpp` 移植（含 `SrtTagParser`） |
| `.vtt` | ✓ | ✓ | Aegisub 沒有這個格式，照 W3C WebVTT 規格實作 |
| `.ttxt` | — | ✓ | 照 `subtitle_format_ttxt.cpp`（它的讀取端沒有移植） |
| `.txt` | — | ✓ | 照 `subtitle_format_txt.cpp`（它的讀取端要 GUI 對話框） |
| `.transtation.txt` | — | ✓ | 照 `subtitle_format_transtation.cpp`，需要幀率 |
| `.mkv` / `.mka` / `.mks` | ✓ | — | 以 PyAV 取代 Aegisub 自帶的 demuxer（需要 `video` extra） |

```python
from pathlib import Path
from aegisub_render import SRTSubtitleFormat, TagMode, read_subtitles, write_subtitles

ass = read_subtitles(Path("in.srt"))  # 格式看檔名
write_subtitles(ass, Path("out.vtt"))  # 同理

data = SRTSubtitleFormat(tags=TagMode.HTML).write(ass)  # 也可以直接用類別
ass = SRTSubtitleFormat().read(Path("in.srt").read_bytes())
```

**沒有一個 writer 會寫樣式表。** 輸出帶的是時間與文字；`{...}` 覆寫標籤預設**原封不動留著**
（`.srt`、`.vtt`、`.ttxt`），因為多數播放器的字幕解析器是 libass、看得懂這些標籤。純文字格式
（`.txt`、TranStation）則照 Aegisub 把標籤剝掉 —— 它們是給人讀的。要改就傳 `tags=`／`--tags`：

| `TagMode` | 行為 |
|---|---|
| `keep` | 原樣（`.srt`／`.vtt`／`.ttxt` 的預設） |
| `strip` | `AssDialogue.get_stripped_text()`，也就是 `SubtitleFormat::StripTags`（`.txt`／TranStation 的預設，也是 Aegisub 在每個格式的做法） |
| `html` | `SRTSubtitleFormat::ConvertTags`：`\b\i\u\s` → `<b><i><u><s>`，其餘標籤丟掉（只有 `.srt`／`.vtt` 接受） |

各格式的建構子選項：`tags`、`parser_cls`（解析成自己的類別，和 `AssFile.from_path` 一樣）、
`fps`（TranStation 的 SMPTE 時間碼）、`play_res`（讀 WebVTT 時定位用的畫布，預設 1280x720）、
`track`（Matroska 字幕軌）、`keep_brand`、`save_ui_state`。`read_subtitles` / `serialize_subtitles` /
`write_subtitles` 只會把該格式建構子收的那幾個選項傳下去，其餘忽略。

### 加自己的格式

```python
from typing import ClassVar, override
from aegisub_render import AssFile, SRTSubtitleFormat, SubtitleFormat, register_format


class Upper(SubtitleFormat):
    name: ClassVar[str] = "Upper"
    write_wildcards: ClassVar[tuple[str, ...]] = ("upper",)

    @override
    def write(self, file: AssFile) -> bytes:
        # prepare() 做 Sort → StripComments → RecombineOverlaps → MergeIdentical → (StripTags)
        return "\n".join(e.text.upper() for e in self.prepare(file).events).encode()


register_format(Upper)  # 現在 out.upper 有人寫了


class LoudSrt(SRTSubtitleFormat): ...  # 或者改掉現有的一個


register_format(LoudSrt, first=True)  # first=True 讓它搶在 .srt 前面
```

`register_format`／`unregister_format`／`formats()` 就是 Aegisub 的 `LoadFormats`，
`get_reader`／`get_writer` 照註冊順序找第一個接受這個檔名的格式。

### WebVTT 怎麼對應到 ASS

Aegisub 沒有 WebVTT，所以這份對應是本專案定的：

- `<b>`／`<i>`／`<u>` → `\b`／`\i`／`\u`；`<c.class>` 查 `STYLE` 區塊的 CSS（沒有 CSS 就用 WebVTT
  內建的 8 色與 `bg_*`）→ `\c`、`\4c`、`\fn`、`\fs`、`\b` 等 inline 標籤，關閉時還原外層的值；
- `::cue { ... }` → 改 `Default` 樣式；`::cue(v[voice="X"])` → 多一個叫 `X` 的樣式；
- `<v Name>` → Name（Actor）欄位，若 CSS 有對應的 voice 規則就同時換成那個樣式；
- cue 的 `align`／`line` → `\an`，`line`／`position`／`size` → MarginL／MarginR／MarginV。margin 是
  script 單位，所以**有定位的檔案**才會補上 `PlayResX`／`PlayResY`（`play_res`，預設 1280x720）；
- `<00:00:01.000>` 時間戳標籤 → `\k` 卡拉OK分段；換行 → `\N`，`&nbsp;` → `\h`，其餘字元參照解碼;
- CSS 的 `color`／`background-color`／`font-family`／`font-size`(px)／`font-weight`／`font-style`／
  `text-decoration` 之外的屬性忽略。

丟掉的東西（ASS 沒地方放）：cue identifier、`NOTE` 與 `REGION` 區塊、`vertical:`、`::cue(#id)` 與
元素選擇器、`<rt>` 的注音內容（底字保留）。寫出時不寫 `STYLE` 區塊。

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
- 3.5.1 新增的 `aegisub.version` 與官方建置相同：`{string = "3.5.1", major = 3, minor = 5, patch = 1, build = 9914}`；
  `include()` 也照 3.5.1 以 `pcall` 執行，執行期錯誤回報為 `Error running Lua include "..."`。
- DependencyControl（`l0.DependencyControl` 與其子模組）被封印，對腳本而言它不存在：`require` 不做任何搜尋就回報
  LuaJIT 原本的「module not found」錯誤（與真的不存在的模組逐字相同，大小寫不同也一樣）；它在 include 目錄裡的檔案
  對 `require`（包括 `l0/DependencyControl` 這種斜線寫法）、`include()` 與腳本載入都不可見。以
  `pcall(require, "l0.DependencyControl")` 載入的腳本（Aegisub-Motion、ILL、clipper2…）因此會退回不用 DepCtrl 的模式；
  直接 `require` 的腳本則在載入時失敗。這份清單寫死在 `host/script.py` 的 `SEALED_MODULES`，不提供設定。

## 資源限制

Lua 在獨立的 worker 行程中執行（一個 `ScriptRender` 開一個，載入腳本後常駐，直到它被關閉）：

- 時間：主行程在 `TIMEOUT_SECONDS` 後直接結束 worker（對 JIT 編譯的迴圈、卡在 C 函式裡的情況同樣有效）。
  這個上限是每個請求各自計算的（驗證、執行各算一次）。
- 記憶體：自訂 allocator 限制 Lua heap（含 `ffi.new` 的 cdata，不含 `ffi.C.malloc`）。注意尚未回收的垃圾也算在內。
- 行數：腳本 append/insert 的行數、輸入檔行數。

## 影片

照 Aegisub 開啟專案時「載入關聯檔案：是」的行為：專案的 Video File（相對於字幕檔）存在就載入，接著是
Timecodes File、Keyframes File；也支援 `?dummy:` 影片。影片資訊以 PyAV 讀取（依 PTS 排序封包，
與 FFMS2 provider 相同的換算方式），並快取在 `%TEMP%\aegisub-render\`。

沒有影片時，`aegisub.video_size()`、`frame_from_ms`、`ms_from_frame` 回傳 nil，`keyframes()` 回傳空表，
與 Aegisub 相同；`get_audio_selection()`、`aegisub.dialog.*` 會拋出「not implemented」錯誤。

## Linux／WSL

非 Windows 時 `aegisub.text_extents` 照 Aegisub 在 Linux 的做法（wxWidgets／Pango）：像 libass 一樣用 fontconfig
選字型、用 HarfBuzz 量測，行高縮放成字級（`height` 就是字級，寬度與 descent 依同一比例，external leading 為 0）。
C 執行期行為用 glibc（Aegisub 在 Linux 也是），模組搜尋器的分隔字元是 `/`。

```bash
sudo apt install libfontconfig-dev libharfbuzz-dev build-essential   # Debian/Ubuntu
export UV_PROJECT_ENVIRONMENT=.venv-linux    # 和 Windows 的 .venv 分開（.venv* 都已 gitignore）
uv sync --group build
uv run --group build python native/build.py # 編出 .so（不 commit），builddir 是 native/builddir-linux
uv run pytest
```

在 WSL 裡，`config.py` 與命令列上的 Windows 路徑（`C:\Program Files\Aegisub`、`D:\sub\x.ass`）會自動換成
`/mnt/c/...`，`?user` 取自 Windows 的 `%APPDATA%`，所以同一份 `config.py` 兩邊都能用。fontconfig 看不到 Windows
的字型；需要的字型可用 `FONT_DIRS`／`--font-dir` 指定（以 `FcConfigAppFontAddDir` 載入），不必把整個
`C:\Windows\Fonts` 加進去。測試在 WSL 下會把用到的幾個 Windows 字型複製到 `tests/.fonts/`（gitignore）再載入。

量測方式不同，所以用到字寬／位置的模板在 Linux 的輸出數值會和 Windows 略有差異（12 個測試案例中 6 個逐位元組
相同，其餘 6 個只差數字，最多約 4%）；`tests/test_cases.py` 在非 Windows 上以此容許範圍比對。

### 在 Windows 上編譯 Linux 分支（MSYS2）

```powershell
# 需要該 MSYS2 環境的編譯器、pkgconf、fontconfig、harfbuzz（ucrt64 是 mingw-w64-ucrt-x86_64-gcc 等；缺的會列出套件名）
uv run --group build python native/build.py --msys2 C:\msys64\ucrt64   # 或 MSYS2 根目錄（取其 ucrt64）
uv run pytest
uv run --group build python native/build.py                            # 換回 MSVC 編的模組
```

`--msys2` 以該環境的 gcc、pkg-config、fontconfig 與 HarfBuzz 編譯 Linux 分支（meson 選項 `branch=linux`：`_font`
由 `_font.fcft.pyx` 編出），模組仍是給本專案的 Python 用，builddir 是 `native/builddir-msys2-<環境>`。編出的 `.pyd`
與 commit 的 MSVC 版同名，會取代它們（一般建置或 `git checkout` 即可換回）；它們要載入該環境的 DLL，所以
`build.py` 把該環境的 `bin` 寫進 `src/aegisub_render/_dll_dirs.txt`（不 commit），`src/aegisub_render/__init__.py` 以
`os.add_dll_directory` 載入，一般建置會刪掉這個檔案。MSYS2 的 fontconfig 會看到 `C:\Windows\Fonts`。
C 函式庫仍是 UCRT（`long` 32 位元），所以 `_crt` 的結果與 MSVC 版相同。

## 測試

```powershell
uv run pytest                 # 全部
uv run basedpyright           # strict 型別檢查
uv run ruff check
```

`tests/cases/*.ass` 由 `scripts/gen_cases.py` 以固定 seed 產生（模板裡的 `math.random` 也都在 `code once` 設好 seed）。
`tests/aegisub/` 是同一批案例在 Aegisub 3.5.1 的實際輸出：開啟 `tests/cases/` 裡的檔案 → File → Export Subtitles →
勾選 `Karaoke template` 濾鏡 → 匯出到 `tests/aegisub/` 同名檔案。`tests/test_cases.py` 對每個案例：

- 以 `--filter "Karaoke template"` 執行，結果必須和 Aegisub 的檔案逐位元組相同（只有 `; Script generated by` 那行多了
  `with aegisub-render <ver>`）；
- 以巨集執行（Apply karaoke template 後存檔），結果去掉存檔才會寫的 `[Aegisub Project Garbage]` 段落後也必須相同；
- 巨集的完整輸出另外與 `tests/snapshots/` 比對（含 Project Garbage），可用
  `AEGISUB_RENDER_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cases.py` 重新記錄。

缺少 Aegisub 輸出的案例會直接失敗，所以新增或重新產生案例後要重新匯出。

## 原生模組

三個 Cython 擴充模組，原始碼、編譯好的 `.pyd` 與 `.pyi` 都在 repo 裡：

| 模組 | 內容 |
|---|---|
| `src/aegisub_render/lua/_luajit`（`.pyx` / `.pxd` / `.pyi`） | 靜態連結的 LuaJIT + lpeg + luabins + shim，以及 Python 綁定：`LuaState`、把 Python 函式變成 Lua C 函式的 dispatcher、`aegisub.re/unicode/lfs` 背後的 C 函式表 |
| `src/aegisub_render/host/_font`（`_font.gdi.pyx` / `_font.fcft.pyx`，`.pxi`，一份 `_font.pyi`） | `text_extents` 的量測，同一個模組名稱、依平台分支從兩份原始碼之一編譯：Windows 分支用 GDI（以選中字型的 `name` 表判斷是否被替換，因為 `GetTextFace` 只給使用者語言的名稱），Linux 分支用 fontconfig + HarfBuzz。兩份的函式、簽名與 docstring 相同，`_font.backend()` 回報是哪一份 |
| `src/aegisub_render/_crt`（`.pyx` / `.pyi`） | Aegisub 依賴的 C 行為：`atoi`、`strtol`、`printf("%g"/"%f")`、double→整數的 C 轉型、int64 除法。直接用 MSVC UCRT 計算，而不是用 Python 模擬（例如 `atoi` 溢位時夾到 `INT_MAX`、`strtol` 接受 `0x`、`inf`/`nan` 的寫法） |

`.pxd` 是 C 宣告（LuaJIT API、shim、windows.h）。`.pyi` 是編譯時產生的：Cython 本身不會產生 stub，
但 `.pyx` 開了 `embedsignature.format=python`，會把原始碼裡帶型別註解的簽名嵌進 docstring；
`native/stubgen.py` 匯入編好的模組、把這些簽名還原成 `.pyi`（自動補上 import、用 ruff 排版），
`native/build.py` 編譯後會自動執行，`tests/test_stubs.py` 則檢查 commit 的 stub 是否與 `.pyd` 一致。
為此 `.pyx` 遵守幾個慣例：公開的 `def` 都有完整型別註解、property 一律唯讀、cdef class 的 `__init__` 要有 docstring。Python 函式以真正的 `lua_CFunction`
提供給 Lua：呼叫 Python 的部分一定正常返回，若要報錯，回到沒有 Python 狀態的 C 函式後才 `lua_error`，
因此 Lua 錯誤不會穿越持有 Python 狀態的 frame。比 ctypes 版快約 3 倍（`template syl`+`template char`、
300 行 × 10 音節：2.83 s → 0.98 s，輸出逐位元組相同）。

要自己重建（需要 Visual Studio 2022 的 x64 C++ 工具）：

```powershell
uv run --group build python native/build.py
```

根目錄的 `meson.build`（subproject 放在 `native/subprojects/`）照抄 Aegisub 的建置：`luajit.wrap`、`luabins/`、
`libaegisub/lua/modules/lpeg.c`，以及 Aegisub 傳給 LuaJIT subproject 的選項（3.5.1 是 static、amalgam、lua52compat；
另外是 release、動態 CRT）。這些檔案來自哪個 Aegisub 原始碼、sha256 與 LuaJIT 選項都記在 `native/upstream.json`；
`build.py` 先檢查 sha256，把選項交給 `meson setup`（`upstream.json` 變了就重建 builddir），
編好後把 `.pyd` 複製進套件目錄並重新產生 `.pyi`。

### 換成別的 Aegisub 的 Lua 執行環境

```powershell
uv run python native/sync_upstream.py TypesettingTools/Aegisub --ref v3.5.1          # 預設的狀態
uv run python native/sync_upstream.py https://github.com/arch1t3cht/Aegisub/releases/tag/feature_12
uv run python native/sync_upstream.py arch1t3cht/Aegisub --ref 9706-cibuilds-20caaabc0   # 版本字串 → commit 20caaabc0
uv run python native/sync_upstream.py D:\src\Aegisub --ref HEAD --dry-run               # 本地 git
uv run --group build python native/build.py
```

從 GitHub 時逐檔讀取（不 clone），本地則用 `git show`。取出 LuaJIT（`luajit.wrap` 與其選項）、lpeg、luabins 與
MoonScript（`automation/include/moonscript.lua`，只當內附的備用版本），取代本地的對應檔案並更新 `native/upstream.json`。
只接受兩種寫法：TypesettingTools 3.5.x（固定雜湊的 `[wrap-file]`、`subprojects/luabins`）與 arch1t3cht 的 feature 版
（`[wrap-git]` + `subprojects/packagefiles/luajit`、`vendor/luabins`）；其他一律拒絕且不改任何檔案。`[wrap-git]` 的
`revision` 若是分支（如 `v2.1`），固定成該分支在來源 commit 日期當時的 commit，並據此算出 `jit.version`
（`tests/test_host.py` 以它檢查編好的 LuaJIT）。Lua API 仍是本工具重現的 3.5.1 行為，不隨來源改變。新增的只有 `native/src/shim.c`（限制記憶體的 allocator、
panic handler、preload lpeg/luabins、照抄 `agi::lua::error`/`argerror` 的報錯函式）與 Cython 模組。
LuaJIT 官方不提供二進位檔，其他來源（MSYS2、conda-forge）的版本與編譯選項都和 Aegisub 不同。

## 與 Aegisub 的已知差異

- 沒有 GUI：對話框（`aegisub.dialog.*`）、音訊無法使用。
- 需要 `package`/`io`/`debug.getinfo` 才能運作的函式庫在沙箱中無法使用；DependencyControl 被刻意封印（見「沙箱」）。
- 開啟影片時 Aegisub 可能跳出詢問並修改 PlayRes / YCbCr Matrix / LayoutRes，這部分不重現。
- 字元編碼只接受 UTF-8（可有 BOM）與帶 BOM 的 UTF-16，不做 Aegisub 的編碼偵測。
- `aegisub.re` 以 Python `regex` 模擬 boost::u32regex（perl 語法、預設多行與 `.` 匹配換行）；罕見語法可能有差異。
- 影片旋轉中繼資料（FFMS2 會交換寬高）未處理。
- `Scroll Position` 等 GUI 狀態沿用輸入檔的值（在 Aegisub 裡存檔時會是當下的 GUI 狀態）。
- `AssFile.copy()` 連 `properties` 與 `header_comments` 一起複製；Aegisub 的 copy constructor
  沒有複製 `Properties`（它會是預設值），那對「我要這份檔案的副本」的呼叫端沒有用。
- 刻意保留 3.5.x 的行為：`subs.delete(a, b, c)` 只刪除最後一個參數指定的行、`subs[i] = nil` 會報錯、
  `lfs.attributes(...).modification` 是自 1601 年起算的秒數。
- 錯誤訊息的位置：Aegisub 以 `agi::lua::error()` 丟出的錯誤會加上呼叫端位置（`luaL_where(L, 1)`），
  由 C++ 例外轉成的錯誤（例如欄位缺漏的 `BadField`）則沒有；兩者都照樣重現。
- `require` 的 MoonScript 模組編譯失敗時回報真正的錯誤，模組載入錯誤訊息包含實際錯誤而非模組名稱：
  3.5.0 在這裡讀錯堆疊位置（會變成「找不到模組」或印出模組名稱），本工具一開始就刻意修正，3.5.1 也修好了。

- 匯出濾鏡：Aegisub 用對話框勾選、排序與設定，這裡是 `--export-filters` 的 ffmpeg 語法
  （`convert` ＝只跑濾鏡，`render` ＝巨集之後再跑）；
  Lua 濾鏡拿不到參數（它的設定對話框沒有 headless 的對應物，上游也一樣；需要參數就寫成 Python 濾鏡）；
  autoload 目錄除了 Aegisub 自己的那兩個，還可以用 `--autoload-dirs` 等加上自己的（Aegisub 沒有這種設定）；
  檔案自己記的那串（`Export Filters:`）要 `--garbage-export-filters` 才會跑，互動終端機則會問；
  要找出某個名字是哪個腳本註冊的，
  Aegisub 是啟動時全部載入，這裡預設不掃描（`--scan-autoload`，結果會快取）。
  `Transform Framerate` 會從 blocks 重建 `Text`，所以巢狀 `\t(...)` 不是逐位元組不變
  （Aegisub 也一樣；`--strict` 可以避免，但那本身就是刻意偏離）。
- 其他字幕格式（見「其他字幕格式」）：
  - 匯出時 `{...}` 覆寫標籤預設保留，Aegisub 一律剝掉或只翻譯 `.srt` 認得的那四個（`--tags` 可切換）；
  - `.ttxt` 的 XML 由本工具自己產生（Aegisub 用 wxXmlDocument），結構與屬性相同但不保證逐位元組一致；
  - TranStation 的幀率來自 `fps=`／`--fps`／`--timecodes`，而不是 Aegisub 的詢問對話框，而且不拒絕 VFR；
  - Matroska 用 PyAV（ffmpeg）取代 Aegisub 自帶的 `MatroskaParser.c`，多軌時預設讀第一條（Aegisub 會問），
    `track=`／`--track` 可指定；
  - `.txt` 的讀取端沒有移植（Aegisub 要先用對話框問分隔符號與註解開頭），`.ttxt` 的讀取端也沒有；
  - WebVTT 不是 Aegisub 的格式，對應規則是本專案定的。

設計決策的完整紀錄見 [docs/DESIGN.md](docs/DESIGN.md)。

## 附帶的 Claude Code 技能

`skills/` 底下是兩個用這個工具做卡拉OK特效字幕的 Claude Code 技能：

| 技能 | 做什麼 |
| --- | --- |
| `skills/ass-oped-karaoke/` | 從頭做一首 OP／ED 的卡拉OK特效字幕：逐詞 `\k`、Name 欄位與 inline fx、Lua ↔ code 行同步、套模板、驗證、取幀、NC／lite 衍生版 |
| `skills/kara-templater-decouple/` | 讀懂或重構既有的特效檔：把寫死在 `code` 區塊裡的字寬表、度量、每句設定表、拍點表拆回欄位，並量化證明渲染結果沒變 |

- 安裝：把要用的那個資料夾整個複製到 `~/.claude/skills/<技能名>`（或某個專案的 `.claude/skills/`），
  然後在 Claude Code 裡用 `/<技能名>`。
- 用法與各腳本的說明都在技能自己的 `SKILL.md` 與 `references/` 裡。
- `ass-oped-karaoke` 的執行環境（Python 3.14、`aegisub_render`、Aegisub 3.5.1、ffmpeg、numpy、Pillow…）用
  `python -X utf8 skills/ass-oped-karaoke/scripts/verify.py env` 逐項檢查。
- 設計依據與測試紀錄：[memory-bank/skillDesign.md](memory-bank/skillDesign.md)。

`ass-oped-karaoke` 執行時另外會用到兩個開源模型，只有使用強制對位（`falign.py`）時才需要，第一次使用時下載到本機：
demucs `htdemucs`（人聲分離，約 80 MB）與 torchaudio `MMS_FA`（強制對位，約 1.2 GB）。
