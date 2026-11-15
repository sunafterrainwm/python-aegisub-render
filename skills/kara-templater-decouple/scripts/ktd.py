"""解藕 kara-templater 特效時的共用入口。

字幕一律走 `aegisub_render` 的模型（`AssFile`／`AssDialogue`），**不要自己解析 .ass**：
欄位、時間碼捨入、UTF-8 BOM + CRLF 的存檔慣例都已經和 Aegisub 3.5.1 對齊了，自己寫的版本
只會在某個邊界 case 上不一樣，而那通常要到交付之後才會發現。渲染一律用 `ScriptRender`
（或一次做完的 `render()`），不要呼叫 `aegisub-render.exe`。

提供：

* `load()` / `save()`      讀寫（存檔＝UTF-8 BOM + CRLF）
* `fx_lines()`             Effect=fx 的 Dialogue 行（上一次渲染的產物）
* `karaoke_lines()`        Effect=karaoke 的卡拉OK來源行（含註解掉的）
* `canonical()`            一條行的正規字串（`Dialogue: layer,start,end,...`），用來比對
* `ass_formatted()` / `parse_time()`   毫秒 ↔ `0:00:00.00`

Python 一律以 `-X utf8` 執行（開發機的 locale 是 cp950）。
"""

import os
import sys
from pathlib import Path

# --- 找到 aegisub_render ----------------------------------------------------------------

_HINT = """\
找不到 aegisub_render。三選一：
  1. 在這個專案裡以連結方式安裝（建議）：uv add --editable <python-aegisub-render repo>
     要讀真的影片再加 extra：uv add --editable "<repo>[video]"
  2. 設環境變數 AEGISUB_RENDER_ROOT 指向那個 repo（本模組會把它的 src/ 加進 sys.path）
  3. 用該 repo 的 .venv 來跑這些腳本
**不要**為了繞過這件事自己寫 ASS 解析器。"""


def _bootstrap() -> None:
    try:
        import aegisub_render  # noqa: F401
    except ImportError:
        root = os.environ.get("AEGISUB_RENDER_ROOT")
        candidates = [Path(root)] if root else []
        candidates += [Path(__file__).resolve().parents[n] for n in (3, 4, 5)]
        for base in candidates:
            src = base / "src"
            if (src / "aegisub_render" / "__init__.py").is_file():
                sys.path.insert(0, str(src))
                return
        raise SystemExit(_HINT) from None


_bootstrap()

from aegisub_render import AssDialogue, AssFile  # noqa: E402
from aegisub_render.ass.time import ass_formatted, parse_time  # noqa: E402,F401

__all__ = [
    "AssDialogue",
    "AssFile",
    "ass_formatted",
    "canonical",
    "fx_lines",
    "karaoke_lines",
    "load",
    "parse_time",
    "save",
]


def load(path):
    """讀一個 .ass（UTF-8 可有 BOM，或帶 BOM 的 UTF-16），回傳 AssFile。"""
    return AssFile.from_path(Path(path))


def save(path, ass, save_ui_state=True):
    """像 Aegisub 存檔那樣寫回去（UTF-8 BOM + CRLF）。"""
    Path(path).write_bytes(ass.serialize(save_ui_state))


def fx_lines(ass, layer=None):
    """上一次渲染產生的行：Effect == "fx" 的 Dialogue（不含註解）。"""
    out = [ev for ev in ass.events if not ev.comment and ev.effect == "fx"]
    if layer is not None:
        out = [ev for ev in out if ev.layer == layer]
    return out


def karaoke_lines(ass):
    """卡拉OK來源行：Effect 含 karaoke（大小寫皆可），註解掉的也算 —— 和 kara-templater 一致。"""
    return [ev for ev in ass.events if "karaoke" in ev.effect.lower()]


def canonical(ev, keep_name=False):
    """一條行的正規字串，用來做多重集合比對與骨架配對。

    走 `entry_data()`（Aegisub 寫檔用的同一條路），所以欄位順序、時間碼與數字格式都和
    存檔結果一致。`keep_name=False` 時把 Name 清掉：解藕之後設定會搬進 Name 欄位，
    輸出行必然帶著，拿它比對只會製造假差異。
    """
    if keep_name or not ev.actor:
        return ev.entry_data()
    bare = ev.copy()
    bare.actor = ""
    return bare.entry_data()
