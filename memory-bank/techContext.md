# 技術脈絡

## 工具鏈

- CPython 3.14（`C:\Program Files\Python314`）＋ uv；`[tool.uv] package = false`，預設 group 是 `dev`
  （basedpyright、pytest、ruff、distlib，以及 cython、meson、ninja）；`build` group 也有 cython、meson、ninja。
- 執行期依賴：`av`（PyAV）、`regex`、`tqdm`。
- 原生建置：Visual Studio 2022 C++ x64、meson（根目錄 `meson.build`，subproject 在 `native/subprojects/`）、動態 CRT。

## 常用指令

```powershell
uv sync                                            # repo 搬家後若 uv run 報 "trampoline failed"，用 uv sync --reinstall
uv run pytest                                      # pytest 設定含 -p no:faulthandler（LuaJIT 用 SEH 拋錯）
uv run basedpyright                                # strict，要 0 errors
uv run ruff check ; uv run ruff format --check
uv run python native/sync_upstream.py SOURCE [--ref REF] [--dry-run]   # 換 Lua 執行環境的來源（見 README）
uv run --group build python native/build.py        # 重編 .pyd 並重新產生 .pyi（tests/test_stubs.py 檢查一致）
uv run --group build python native/build.py --msys2 F:\msys64\ucrt64   # Windows 上編 Linux 分支（會取代 commit 的 .pyd；
                                                   # 之後跑一次一般建置換回）
                                                   # repo 搬家後先刪 native/builddir
AEGISUB_RENDER_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_cases.py   # 重新記錄 snapshot（只在 Windows）
uv run python scripts/gen_cases.py                 # 以固定 seed 重新產生測試案例（CRLF）
uv run python scripts/build_exe.py                 # 產生 aegisub-render.exe
```

## WSL（Debian 13）

```bash
# ~/.local/bin/uv（uv 自己管理的 Python 3.14）；venv 用 .venv-linux 和 Windows 的 .venv 分開
export PATH=$HOME/.local/bin:$PATH UV_PROJECT_ENVIRONMENT=.venv-linux UV_LINK_MODE=copy
cd /mnt/d/git/python-aegisub-render
uv sync --group build && uv run --group build python native/build.py && uv run pytest
```

需要 `libfontconfig-dev libharfbuzz-dev`（已安裝）。從 Windows 端呼叫：`wsl.exe -e bash -lc '...'`。

## 測試

- `tests/cases/` 12 個案例（CRLF，`.ass` 在 git 裡是 `-text`）；`tests/aegisub/` 是 Aegisub 用 `Karaoke template`
  濾鏡匯出的結果，`--filter` 的輸出必須逐位元組相同（只差 header 行），巨集輸出去掉 Project Garbage 後也相同；
  `tests/snapshots/` 是巨集的完整輸出。
- 缺少 Aegisub 參考檔的案例直接失敗；新增或重產案例後要在 Aegisub 匯出。

## 開發慣例（使用者的偏好）

- 回覆與文件用繁體中文（zh-TW）；程式碼、註解、commit message 用英文。
- 大的設計先用 grilling（逐輪提問）對齊再動手；忠於上游、偏離要說明。
- git：commit 有 GPG 簽章（`commit.gpgsign=true`，可能跳 pinentry，放背景執行並用
  `git log --show-signature -1` 確認），**不可**繞過簽章。
- **只 stage 明確的路徑，不要 `git add -A`**：使用者會在 repo 根目錄放自己的檔案（zip 等），
  也會在工作樹留下未提交的工作；碰到與自己無關的變更就不要動、不要一起 commit。
- 檔案編修避免 heredoc 裡的反斜線（Git Bash 會吃掉），改用編輯工具或寫成檔案的腳本。
