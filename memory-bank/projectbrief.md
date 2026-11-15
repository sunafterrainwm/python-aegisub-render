# 專案簡介

**aegisub-render**：在 Python 裡、不開 GUI 地執行 Aegisub Automation 4 腳本，預設是 kara-templater
（Apply karaoke template）。輸出要與「在 Aegisub 裡套用後存檔／匯出」逐位元組相同。

## 基準

- 目前的基準是 **Aegisub 3.5.1**（官方建置，`C:\Program Files\Aegisub`，`aegisub.exe` FileVersion 3.5.1.9914）。
  最初是 3.5.0；升級紀錄見 `docs/DESIGN.md` 的「3.5.1」一節。
- 原始碼參考 `reference/aegisub-<ver>/`（gitignore），來源是 GitHub tag 的 **zip 快照**
  `https://github.com/TypesettingTools/Aegisub/archive/refs/tags/v<ver>.zip`。**絕對不要 clone** Aegisub。
- 版本號集中在 `src/aegisub_render/version.py`（`AEGISUB_VERSION*`、`AEGISUB_BUILD`）。

## 範圍

- 重現 Aegisub 匯出給 Lua 的 API（`aegisub.*`、字幕物件、`include`、模組搜尋器、MoonScript 0.7.0）。
- 執行環境與 Aegisub 相同：LuaJIT 2.1 @ `04dca79`（lua52compat、GC64）＋ lpeg 0.10 ＋ luabins，自行以 Aegisub 的 meson
  設定編譯成 Cython 擴充模組。
- 沙箱只為了找出奇怪用途（不是安全邊界）：封鎖 `io`/`package`/`dofile`/`loadfile`/`module`，`os` 只留
  `clock/date/difftime/time`，`debug` 只留 `traceback`，`getmetatable` 只對 table 有效；被封鎖的東西要拋出描述性錯誤。
  ffi 只開放給從檔案載入的程式碼。DependencyControl 被「物理封印」（require 得到原生的 module not found）。
- 限制：120 秒（worker 行程）、Lua heap 512,000,000 bytes（MB 是十進位）、腳本產生 50,000 行、輸入 100,000 行。
- 只支援 Windows x64、CPython 3.14。

## 不可違反的限制

- Python 3.14 + uv；型別完整標註，**禁止** `from __future__ import annotations`（ruff 規則擋）。
- basedpyright strict、ruff 都要零錯誤；測試中的隨機數一律指定 seed。
- 原生綁定用 Cython（`.pyx`/`.pxd`/`.pyi`），不用 ctypes；`.pyi` 由編譯後的模組產生。
- src layout：套件是 `src/aegisub_render`，設定檔 `config.py` 在 repo 根目錄（`settings.py` 以 `parents[2]` 找它）。
  可以被安裝，但只以連結（editable）的形式：`uv add --editable <repo>`，不發佈 wheel（擴充模組是 commit 進 repo 的既編譯檔）。
- 與上游行為一致優先；刻意偏離的地方都要寫進 `docs/DESIGN.md` 與 README 的「已知差異」。
