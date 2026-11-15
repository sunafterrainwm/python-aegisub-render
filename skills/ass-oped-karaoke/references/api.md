# 用 `aegisub_render` 的 Python API，不要自己來

這個流程裡幾乎每一件和字幕有關的事，`aegisub_render` 都有對應的函式，而且它的目標是
「輸出與在 Aegisub 3.5.1 套用後存檔逐位元組相同」。自己寫的版本只會在某個邊界case上不一樣，
而那個 case 通常要到交付之後才會被發現。

## 對照表

| 要做的事 | 用這個 | 不要再這樣做 |
| --- | --- | --- |
| 讀 .ass | `AssFile.from_path(p)`／`from_bytes`／`from_text` | `open(...).read()` ＋ `split(",", 9)` |
| 寫 .ass | `p.write_bytes(ass.serialize())`（存檔）／`ass.export()`（匯出） | 自己拼 `\ufeff` ＋ `"\r\n".join` |
| 事件欄位 | `ev.comment / layer / start / end / style / actor / margin / effect / text` | 欄位索引 `f[3]`、`f[8]`、`f[9]` |
| 時間碼 | `parse_time("0:01:23.45")`、`ass_formatted(ms)` | 自己拆 `h:mm:ss.cc`、自己做 10ms 捨入 |
| 套用卡拉OK模板 | `ScriptRender(ass, "kara-templater", settings, subtitle_path=p)` → `macro_can_run()` / `run()` | `subprocess.run(["aegisub-render.exe", "render", ...])` |
| 一次做完讀→套→寫 | `render(in_path, out_path, settings, RenderOptions(...))` | 同上 |
| 平移時間軸 | `offset(in, out, "-0:01:09.98")`，或在記憶體裡用 `shift_time_by_frames(ms, n, framerate)` | 自己對每行做加減（會漏掉 10ms 捨入與 clamp） |
| 影格 ↔ 時間 | `probe_video(path)` ＋ `Framerate`（`frame_at_time` / `time_at_frame`） | `round(t * 24000 / 1001)` |
| 量字（寬高、descent、字身框、逐字位置、karaskel 座標） | `measure_text(style_or_MeasureStyle, text, layout)` | 用 ffmpeg 畫在黑底上數像素 |
| 從 .ass 取樣式來量 | `resolve_style_from_ass(path, "OPJP")` | 自己解析 `Style:` 行 |
| 卡拉OK音節 | `parse_karaoke_syllables(line)` | `re.findall(r"\{\\k(\d+)[^}]*\}([^{]*)")` |
| 解覆寫標籤 | `parse_override_tags(text, strict=True)`（重組回原字串）／預設切法（模仿 Aegisub） | 自己寫 `\{[^}]*\}` 正規式 |
| 剝標籤 | `line.get_stripped_text()` / `line.strip_tags()` | 自己 `re.sub` |
| 顏色 | `Color` | 自己處理 `&HBBGGRR&` |

`scripts/oped.py` 已經把常用的包好（`load`／`save`／`is_code`／`is_fx`／`render_file`／
`apply_template`／`parse_name`／`video_of`／`framerate_of`），其他工具都從它 import。

## 安裝與 import

本專案只支援「連結到 repo」的安裝方式：

```powershell
uv add --editable <repo>                                 # 一般情況
uv add --editable "<repo>[video]"                        # 還要讀真的影片（probe_video 需要 PyAV）
```

**不要** build／發佈 wheel：`.pyd` 是針對單一直譯器編好 commit 進 repo 的。

沒有裝成套件時，把 repo 的 `src/` 加進 `sys.path` 也可以（`aegisub-render.py` 就是這樣做的）。
`scripts/oped.py` 會在 import 失敗時給出這段說明，不要去猜。

## 常用片段

### 就地套用模板（不碰檔案）

```python
from aegisub_render import AssFile, ScriptRender, load_settings

ass = AssFile.from_path(path)
settings = load_settings()
settings.font_dirs = [fonts_dir]                 # 特殊字型（\fn 要量得到）
with ScriptRender(ass, "kara-templater", settings, subtitle_path=path) as sr:
    if not sr.macro_can_run():                   # Aegisub 會不會把選單變灰
        raise SystemExit(sr.refusal_reason())    # 典型原因：template 行不在前 50 行
    # 這裡不傳 force=True：交付檔必須是「按下去就會動」的檔案
    sr.run()                                     # ass 就地更新，舊的 fx 行自己會被換掉
path.write_bytes(ass.serialize(sr.save_ui_state))
```

`run()` 之後原本拿在手上的 `AssDialogue` 參考會失效（裡面是新物件），要重新走 `ass.events`。

### 冪等檢查（交付前一定要做）

```python
once = render_to_bytes(src)      # 見 scripts/verify.py
twice = render_to_bytes_from(once)
assert once == twice             # 不相同就是模板吃到了自己上次的輸出
```

### 量字取代像素量測

```python
from aegisub_render import measure_text, resolve_style_from_ass, PosLayout

style, (rx, ry) = resolve_style_from_ass(ass_path, "OPCredit")
m = measure_text(style, "シュガーソルト", PosLayout(align=8, x=520, y=120))
m.width, m.height, m.descent          # 排版框（karaskel 的 line.width 等）
m.padding_top, m.ink_height           # 字身（ink）框：不含空白的實際筆畫範圍
m.chars[0].left, m.chars[0].center    # 逐字座標（相對行首，加 line.left 才是絕對）
m.position.top, m.position.middle     # karaskel 的對齊座標
```

舊做法（libass 畫在黑底上數像素）只在「要驗證 libass 真的這樣排」時才需要，
`scripts/measure.py --ink` 保留了它，但**預設走 API**：它快、不需要 ffmpeg、也不會被 `\blur` 污染。

### 影格 ↔ 時間（NC 版對位、取幀）

```python
from aegisub_render.video.probe import probe_video
from aegisub_render.video.vfr import Framerate

info = probe_video(video)                       # 有快取
fr = Framerate.from_timecodes(info.timecodes)   # VFR 也正確
frame = fr.frame_at_time(ms, "start")
ms = fr.time_at_frame(frame, "start")
```

「NCOP 的第 0 格 = 正片第 1678 格」這種對應關係用影格數表示，不要用毫秒：
毫秒會因為捨入在換算之後差一格，而一格就是看得出來的錯位。
