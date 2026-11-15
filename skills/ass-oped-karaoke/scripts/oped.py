"""OP／ED 卡拉OK特效工具的共用函式庫。

這個 skill 的每一支工具都從這裡拿東西，**不要自己解析 .ass**：字幕的模型一律是
`aegisub_render` 的 `AssFile`／`AssDialogue`，渲染一律是 `ScriptRender`，
時間與影格一律是 `aegisub_render` 的換算（理由與對照表見 references/api.md）。

提供：

* `load()` / `save()`                  讀寫（存檔慣例＝UTF-8 BOM + CRLF）
* `is_code()` / `is_template()` / `is_fx()` / `is_karaoke()`   行的分類
* `comment()`                          造一條 code／template 註解行
* `parse_name()` / `format_name()`     Name 欄位的迷你語言（見 references/fields.md）
* `apply_template()` / `render_bytes()` 套用 kara-templater
* `missing_fonts()`                    樣式用到、但這台機器沒有的字型
* `video_of()` / `framerate_of()`      影片與影格↔時間
* `stats()`                            行數與「同時事件數」

Python 一律以 `-X utf8` 執行（這台機器的 locale 是 cp950）。
"""

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# --- 找到 aegisub_render ----------------------------------------------------------------

_HINT = """\
找不到 aegisub_render。三選一：
  1. 在這個專案裡以連結方式安裝（建議）：uv add --editable <python-aegisub-render repo>
     要讀真的影片再加 extra：uv add --editable "<repo>[video]"
  2. 設環境變數 AEGISUB_RENDER_ROOT 指向那個 repo（本函式庫會把它的 src/ 加進 sys.path）
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

from aegisub_render import (  # noqa: E402
    AssDialogue,
    AssFile,
    ScriptRender,
    Settings,
    load_settings,
)
from aegisub_render.ass.time import ass_formatted, parse_time  # noqa: E402,F401

__all__ = [
    "Applied",
    "AssDialogue",
    "AssFile",
    "Stats",
    "apply_template",
    "ass_formatted",
    "code_lines",
    "comment",
    "events_of",
    "format_name",
    "framerate_of",
    "is_code",
    "is_fx",
    "is_karaoke",
    "is_template",
    "load",
    "missing_fonts",
    "parse_name",
    "parse_time",
    "render_bytes",
    "save",
    "settings_for",
    "stats",
    "template_lines",
    "video_of",
]


# --- 讀寫 -------------------------------------------------------------------------------


def load(path: Path | str) -> AssFile:
    """讀一份字幕（UTF-8／BOM／UTF-16 都認得，.ssa 以 SSA 解析）。"""
    return AssFile.from_path(Path(path))


def save(path: Path | str, ass: AssFile, *, save_ui_state: bool = True, keep_brand: bool = False) -> None:
    """照 Aegisub 存檔的方式寫出去（UTF-8 BOM + CRLF）。"""
    Path(path).write_bytes(ass.serialize(save_ui_state, keep_brand))


# --- 行的分類 ---------------------------------------------------------------------------


def _effect_head(ev: AssDialogue) -> str:
    return ev.effect.split(" ", 1)[0]


def is_code(ev: AssDialogue) -> bool:
    """`code once` / `code line all` / `code syl all` …"""
    return ev.comment and _effect_head(ev) == "code"


def is_template(ev: AssDialogue) -> bool:
    """`template line …` / `template syl …` / `template char …` / `template furi …`"""
    return ev.comment and _effect_head(ev) == "template"


def is_fx(ev: AssDialogue) -> bool:
    """模板產生出來的行（下一次套用時會被刪掉重建）。"""
    return ev.effect == "fx"


def is_karaoke(ev: AssDialogue) -> bool:
    """卡拉OK來源行（套用後 kara-templater 會把它轉成 Comment）。"""
    return ev.effect == "karaoke"


def code_lines(ass: AssFile, effect: str | None = None) -> list[AssDialogue]:
    """所有 code 行；給了 `effect` 就只要 Effect 完全相同的那些。"""
    return [e for e in ass.events if is_code(e) and (effect is None or e.effect == effect)]


def template_lines(ass: AssFile) -> list[AssDialogue]:
    return [e for e in ass.events if is_template(e)]


def events_of(ass: AssFile, style: str | None = None, actor_prefix: str | None = None) -> list[AssDialogue]:
    """歌詞來源行（排掉 code／template／fx 與 furigana 樣式）。"""
    out: list[AssDialogue] = []
    for e in ass.events:
        if is_code(e) or is_template(e) or is_fx(e) or e.style.endswith("-furigana"):
            continue
        if style is not None and e.style != style:
            continue
        if actor_prefix is not None and not e.actor.startswith(actor_prefix):
            continue
        out.append(e)
    return out


def comment(
    text: str,
    effect: str,
    *,
    layer: int = 0,
    style: str = "Default",
    actor: str = "",
) -> AssDialogue:
    """造一條 code／template 註解行（時間固定 0，欄位照 kara-templater 的慣例）。"""
    ev = AssDialogue()
    ev.comment = True
    ev.layer = layer
    ev.start = 0
    ev.end = 0
    ev.style = style
    ev.actor = actor
    ev.margin = [0, 0, 0]
    ev.effect = effect
    ev.text = text
    return ev


# --- Name 欄位的迷你語言 ------------------------------------------------------------------


def _num(tok: str) -> object:
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_name(actor: str) -> tuple[set[str], dict[str, object]]:
    """把 Name 欄位拆成（旗標集合, key=value 對照表）。

    >>> parse_name("credit jp an=8 x=420 y=283.5 last")
    ({'credit', 'jp', 'last'}, {'an': 8, 'x': 420, 'y': 283.5})

    數值自動轉成數字，其餘留字串（顏色 `1c=&H2A80F0&` 就是字串）。
    Name 欄位裡**不能有逗號**（ASS 用逗號分欄）。
    """
    flags: set[str] = set()
    data: dict[str, object] = {}
    for tok in actor.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            data[k] = _num(v)
        else:
            flags.add(tok)
    return flags, data


def format_name(flags: list[str] | set[str] = (), data: dict[str, object] | None = None) -> str:
    """`parse_name()` 的反向；旗標照給的順序，key=value 照插入順序。"""
    parts = list(flags)
    for k, v in (data or {}).items():
        parts.append(f"{k}={v}")
    name = " ".join(parts)
    if "," in name:
        raise ValueError(f"Name 欄位不能有逗號：{name!r}")
    return name


# --- 渲染 -------------------------------------------------------------------------------


def settings_for(font_dirs: list[Path] | None = None, config: Path | None = None) -> Settings:
    settings = load_settings(config)
    if font_dirs:
        settings.font_dirs = list(font_dirs)
    return settings


@dataclass(slots=True)
class Applied:
    """`apply_template()` 的結果：目前只有「存檔時要不要寫 UI 狀態」。"""

    save_ui_state: bool


def apply_template(
    ass: AssFile,
    path: Path,
    *,
    settings: Settings | None = None,
    quiet: bool = True,
    progress: bool = False,
) -> Applied:
    """就地套用 kara-templater（等同 Aegisub 的 Apply karaoke template）。

    **沒有 force 這個選項**，`ScriptRender` 也不帶 `force=`：validation 不過就直接失敗。
    交付檔必須是「在 Aegisub 裡按下去就會動」的檔案；繞過 validation 只會把
    「template 行掉到 `[Events]` 前 50 行之外」這種問題藏到下一次改稿。
    """
    settings = settings or settings_for()
    with ScriptRender(ass, "kara-templater", settings, subtitle_path=path, quiet=quiet, progress=progress) as sr:
        if not sr.macro_can_run():
            reason = sr.refusal_reason() or "kara-templater 拒絕執行"
            raise SystemExit(f"{path.name}: {reason}\n（template 行要排在 [Events] 的前 50 行內）")
        sr.run()
        return Applied(sr.save_ui_state)


def render_bytes(path: Path, *, settings: Settings | None = None) -> bytes:
    """讀 → 套用模板 → 序列化，回傳「如果存檔會寫出去的那串位元組」（不寫檔）。"""
    ass = load(path)
    return ass.serialize(apply_template(ass, path, settings=settings).save_ui_state)


_SUBSTITUTED = re.compile(r"font '(.+?)' is not installed; \S+ substituted '(.+?)'")


def missing_fonts(ass: AssFile, font_dirs: list[Path] | None = None) -> list[tuple[str, str]]:
    """事件行用到的樣式裡，這台機器沒有的字型：[(要的字型, 量字實際退到的字型)]。

    缺字型時 `text_extents` 量的是替代字型，模板算出來的座標就和裝有該字型的機器不同；
    libass 燒字時又可能退到**另一個**替代字型，於是畫面上的字會互相重疊或錯位。
    """
    from aegisub_render import measure_text

    used = {ev.style for ev in ass.events}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for style in ass.styles:
        if style.name not in used or style.font.lower() in seen:
            continue
        seen.add(style.font.lower())
        try:
            warnings = measure_text(style, "Ag", font_dirs=list(font_dirs) if font_dirs else None).warnings
        except Exception:  # noqa: BLE001  量不出來不是這個檢查該擋的事
            continue
        for w in warnings:
            m = _SUBSTITUTED.search(w)
            if m:
                out.append((m.group(1), m.group(2)))
    return out


# --- 影片 -------------------------------------------------------------------------------


def video_of(ass: AssFile, ass_path: Path) -> Path | None:
    """專案連結的 Video File，照 Aegisub 的方式解析（相對於字幕檔）。"""
    raw = ass.properties.video_file.strip()
    if not raw or raw.startswith("?dummy"):
        return None
    p = Path(raw)
    return p if p.is_absolute() else (ass_path.parent / p).resolve()


def framerate_of(video: Path | None = None, fps: str | None = None):
    """取得 `Framerate`：優先用 `--fps`（`24000/1001` 或 `23.976`），否則讀影片的時間碼。"""
    from aegisub_render.video.vfr import Framerate

    if fps:
        if "/" in fps:
            num, _, den = fps.partition("/")
            return Framerate.from_ratio(int(num), int(den))
        return Framerate.from_fps(float(fps))
    if video is None:
        raise SystemExit("需要 --fps 或一個能讀出時間碼的影片")
    from aegisub_render.video.probe import probe_video
    from aegisub_render.video.vfr import FramerateError

    # probe_video 回傳的是每一格的時間戳（ms，`VideoInfo.timestamps`），和 Aegisub 載入影片時一樣
    # 拿它建 Framerate（VFR 也正確）。讀不到時間碼是使用者能處理的事（裝 PyAV、改給 --fps），
    # 所以轉成 SystemExit；其他例外照常拋出，不要被當成「讀不到」吞掉。
    try:
        # IndexError：檔案裡沒有視訊串流（probe_video 取 streams.video[0]）
        info = probe_video(Path(video))
        if len(info.timestamps) < 2:
            raise FramerateError(f"只讀到 {len(info.timestamps)} 個影格的時間戳")
        return Framerate.from_timecodes(info.timestamps)
    except (OSError, ValueError, IndexError, FramerateError) as e:
        raise SystemExit(f"讀不出 {video} 的時間碼（{e or type(e).__name__}）；改給 --fps，例如 --fps 24000/1001") from e


# --- 統計 -------------------------------------------------------------------------------


@dataclass(slots=True)
class Stats:
    events: int
    fx: int
    code: int
    template: int
    karaoke: int
    max_concurrent: int
    bytes: int

    def __str__(self) -> str:
        return (
            f"{self.events} 行（fx {self.fx}、code {self.code}、template {self.template}、"
            f"karaoke {self.karaoke}）  同時最多 {self.max_concurrent} 個事件  {self.bytes / 1e6:.2f} MB"
        )


def stats(ass: AssFile, size: int | None = None) -> Stats:
    """行數與同時事件數。**同時事件數才是播放器會卡的地方**，總行數只決定檔案大小。"""
    marks: list[tuple[int, int]] = []
    for e in ass.events:
        if e.comment or e.end <= e.start:
            continue
        marks.append((e.start, 1))
        marks.append((e.end, -1))
    marks.sort()
    cur = peak = 0
    for _, d in marks:
        cur += d
        peak = max(peak, cur)
    return Stats(
        events=len(ass.events),
        fx=sum(1 for e in ass.events if is_fx(e)),
        code=sum(1 for e in ass.events if is_code(e)),
        template=sum(1 for e in ass.events if is_template(e)),
        karaoke=sum(1 for e in ass.events if is_karaoke(e)),
        max_concurrent=peak,
        bytes=size if size is not None else len(ass.serialize()),
    )
