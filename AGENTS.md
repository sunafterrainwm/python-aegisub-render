# AGENTS.md

給在這個 repo 工作的 AI coding agent 的指引（人也歡迎讀）。這個檔案和程式碼一樣進版本控制，
發現有錯就直接修。

## Memory bank

Agent 每次開新 session 都對這個專案一無所知，`memory-bank/` 就是替代品：**動手之前先依下列順序讀完**。

| 檔案 | 內容 | 是否 commit |
| --- | --- | --- |
| `memory-bank/projectbrief.md` | 這個專案是什麼、範圍、不可違反的限制 | commit |
| `memory-bank/productContext.md` | 為誰而做、要解決的問題、使用方式與行為決定 | commit |
| `memory-bank/systemPatterns.md` | 架構、關鍵決定、載重的模組 | commit |
| `memory-bank/techContext.md` | 工具鏈、建置、測試、常用指令、開發慣例 | commit |
| `memory-bank/activeContext.md` | 現在在做什麼、尚未定案的事 | **只在本機，git 忽略** |
| `memory-bank/progress.md` | 已完成、待辦、已知問題 | **只在本機，git 忽略** |

前四個描述專案現在的樣子，只有專案本身改變時才改。後兩個是目前推動這個 repo 的人的工作狀態，
刻意不 commit，所以全新 clone 本來就沒有這兩個檔案 —— 記下這件事繼續做，不要慌。

`memory-bank/README.md` 也說明了同一套分工與閱讀順序。更細的設計理由在 `README.md` 與 `docs/DESIGN.md`。

### 保持最新

會漂移的 memory bank 比沒有更糟，因為下一個 session 會相信它。

- 工作狀態一變就更新 `activeContext.md` 與 `progress.md`：任務完成、方向放棄、發現 bug、做了決定。
  日期寫絕對日期（`2026-11-11`），不要寫「昨天」。
- 專案本身改變時更新那四個共用檔案：新依賴或服務、入口點搬家、架構決定、建置或測試指令改變。
  把過時的敘述當成 bug，在讓它過時的那次變更裡一起修掉。
- 發現錯的就改掉，不要在後面附一段相反的說法。兩個自信但矛盾的敘述會讓下一個 agent 無從選擇。
- 不再重要的就刪。這些檔案每次 session 開頭都會讀，長度有持續成本，各自維持一兩個畫面。
- 不確定就說不確定。`TODO: confirm` 有用；把猜測寫成事實沒有。

## 資訊清理（sanitization）

**這個 repo 除了 `memory-bank/activeContext.md` 與 `memory-bank/progress.md` 之外的每個檔案都是共用的
—— 會 commit、push、被審閱，也可能公開。寫的時候就要是乾淨的，包含這份 AGENTS.md 與四個共用的
memory bank 檔案。**

共用檔案裡絕對不要放：

- 帳密、token、API key、密碼、私鑰、連線字串；
- 內部主機名稱、私有 IP、staging 或管理後台 URL、bucket／queue／專案 id；
- 客戶、雇主、同事的名字，以及任何個資；
- 本機絕對路徑與使用者名稱 —— 用 repo 相對路徑或 `<project-root>`；
- 尚未公開的計畫、價格，或任何受 NDA 保護的內容。

描述形狀，不要寫出秘密本身。

那兩個本機檔案可以放只是不方便公開的細節（廠商名稱、半成品的做法、還沒定型的意見），但它們**不是**
秘密保管處：一次 `git add -f`、一張截圖、一份備份就外流了。真正的秘密放密碼管理工具或未追蹤的 `.env`。

如果發現 repo 裡已經 commit 了敏感內容，直接說出來並停手 —— 要改寫歷史與輪替憑證，那是維護者的決定。

## 在這個 repo 工作

細節在 `memory-bank/techContext.md`，這裡只放最常用的：

- **安裝**：`uv sync`（repo 搬家後 `uv run` 若報 "trampoline failed"，用 `uv sync --reinstall`）
- **執行**：`uv run aegisub-render render input.ass -o output.ass`，或 `uv run aegisub-render.py ...`
- **測試**：`uv run pytest`；單一測試 `uv run pytest tests/test_cli.py::test_name`
- **型別**：`uv run basedpyright`（strict，必須 0 errors）
- **Lint／格式**：`uv run ruff check`、`uv run ruff format --check`
- **原生模組**：`uv run --group build python native/build.py`（重編 `.pyd` 並重新產生 `.pyi`；
  `tests/test_stubs.py` 會檢查一致）
- Windows 之外（WSL／Linux）的設定與 MSYS2 交叉建置見 `memory-bank/techContext.md` 與 `README.md`。

## 慣例

- **版面**：套件在 `src/aegisub_render`（src layout）。設定檔 `config.py` 故意留在 repo 根目錄，
  `src/aegisub_render/settings.py` 以 `Path(__file__).resolve().parents[2]`（也就是 `../../`）取它的絕對路徑；
  動到其中一邊時另一邊要一起改。
- **安裝方式**：本專案可以被別的專案以 `uv add --editable <repo>` 裝成指向這個 repo 的連結，
  **不要**建置或發佈 wheel —— `src/aegisub_render/**/*.pyd` 是針對單一直譯器編好後 commit 進 repo 的。
- **自動產生、不要手改**：`src/aegisub_render/**/*.pyi`（`native/stubgen.py` 產生）、
  `native/upstream.json` 與 `native/subprojects/`、`native/src/lpeg.*`、
  `src/aegisub_render/host/lua/moonscript.lua`（由 `native/sync_upstream.py` 從 Aegisub 樹取得，
  `native/build.py` 會驗 sha256）。
- **Python**：3.14；型別完整標註，**禁止** `from __future__ import annotations`（ruff 規則擋）。
  原生綁定用 Cython（`.pyx`/`.pxd`），不用 ctypes。
- **語言**：對使用者的回覆與文件用繁體中文；程式碼、註解、commit message 用英文。
- **忠於上游**：行為以 Aegisub 3.5.1 原始碼為準；刻意偏離要寫進 `docs/DESIGN.md` 與 README 的「已知差異」。
- **git**：commit 有 GPG 簽章，不可繞過。**只 stage 明確的路徑，不要 `git add -A`** —— 使用者會在 repo
  根目錄放自己的檔案（壓縮檔、設計草稿等），也會留下未提交的工作；與自己無關的變更不要動、不要一起 commit。
  沒有被要求就不要 commit。
- **編輯檔案**：避免在 Git Bash 的 heredoc 裡寫反斜線（會被吃掉），改用編輯工具或獨立的腳本檔。
- 呼叫 Python 一律帶 `PYTHONUTF8=1`／`-X utf8`，開檔明確寫 `encoding="utf-8"`：這台機器的 locale 是 cp950。
