"""量字：排版框、字身（ink）框、逐字座標、karaskel 相容的對齊座標。

    python -X utf8 measure.py "シュガーソルト" --font "Noto Sans CJK TC" --size 80
    python -X utf8 measure.py "冷淡的" --ass x.ass --style Title --an 5 --pos 960,540 --chars
    python -X utf8 measure.py "嬌" --ass x.ass --style Title --scale 160 160 --ink --vertical

預設走 `aegisub_render` 的 `measure_text()`（就是 Aegisub 的 `text_extents`）：快、不需要 ffmpeg、
也不會被 `\\blur` 污染。**位置不要用算的，要量**——片名與 credit 的座標都是這樣定出來的。

`--ink` 另外用 libass 把字真的畫在黑底上數像素，用來複驗「libass 真的這樣排嗎」。
兩個門檻陷阱：量字身不要帶 `\\blur`（會往外擴 1～2px）、門檻 `>24` 會把柔光算進去，用 `>100`。

注意：**字身框是 GDI 回報的 glyph ink box**，對某些 CJK 字型（例如 Noto Sans CJK 的「ー」）
會比排版框寬、留白因此是負的；樣式有 ScaleX/ScaleY 時它也不跟著縮放。排版用的數字請看排版框
與 karaskel 座標，字身框只在「要知道筆畫實際落在哪裡」時用，必要時以 `--ink` 複驗。
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402

from aegisub_render import (  # noqa: E402
    MarginLayout,
    MeasureStyle,
    PosLayout,
    measure_text,
    resolve_style_from_ass,
)

HDR = """[Script Info]
ScriptType: v4.00+
PlayResX: {rx}
PlayResY: {ry}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: M,Arial,100,&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ink_box(text: str, tags: str, res: tuple[int, int], font_dir: Path | None) -> tuple[int, int, int, int] | None:
    """用 libass 畫一格，回傳筆畫的 bounding box（門檻 >100，不帶 \\blur）。"""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("--ink 需要 Pillow（pip install pillow）") from None
    rx, ry = res
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 檔名用純 ASCII 並以 cwd 切進去：filtergraph 會把磁碟機代號的冒號當參數分隔，
        # 中文與方括號也會出事
        (d / "m.ass").write_text(
            HDR.format(rx=rx, ry=ry) + "Dialogue: 0,0:00:00.00,0:00:05.00,M,,0,0,0,,{" + tags + "}" + text + "\n",
            encoding="utf-8",
        )
        sub = "m.ass"
        if font_dir:
            sub += ":fontsdir=" + str(font_dir.resolve()).replace("\\", "/").replace(":", r"\:")
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi",
                "-i", f"color=c=black:s={rx}x{ry}:d=1", "-vf", f"subtitles={sub}",
                "-frames:v", "1", "-y", "m.png",
            ],
            check=True,
            cwd=tmp,
        )
        im = Image.open(d / "m.png").convert("L").point(lambda v: 255 if v > 100 else 0)
        return im.getbbox()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--ass", type=Path, help="從這個檔取樣式（連同 PlayRes）")
    ap.add_argument("--style", default="Default")
    ap.add_argument("--font", help="不用 --ass 時的字型")
    ap.add_argument("--size", type=float, default=70)
    ap.add_argument("--bold", action="store_true")
    ap.add_argument("--spacing", type=float, default=0)
    ap.add_argument("--scale", nargs=2, type=float, metavar=("FSCX", "FSCY"))
    ap.add_argument("--an", type=int, default=5)
    ap.add_argument("--pos", help="X,Y（給 \\pos 的版面）")
    ap.add_argument("--margins", help="L,R,V（給邊界對齊的版面）")
    ap.add_argument("--res", default="1920x1080", help="沒有 --ass 時的 PlayRes（預設 1920x1080）")
    ap.add_argument("--chars", action="store_true", help="印逐字表")
    ap.add_argument("--ink", action="store_true", help="另外用 libass 畫一次複驗")
    ap.add_argument("--vertical", action="store_true", help="直書：@字型 + \\an8\\frz270（只影響 --ink）")
    ap.add_argument("--font-dir", type=Path, help="額外字型目錄")
    a = ap.parse_args()

    if a.ass:
        style, res = resolve_style_from_ass(a.ass, a.style)
        if a.scale:
            style = style.copy()
            style.scalex, style.scaley = a.scale
        font, size = style.font, style.fontsize
    else:
        rx, _, ry = a.res.partition("x")
        res = (int(rx), int(ry))
        style = MeasureStyle(
            font=a.font or "Arial",
            fontsize=a.size,
            bold=a.bold,
            spacing=a.spacing,
            scalex=a.scale[0] if a.scale else 100.0,
            scaley=a.scale[1] if a.scale else 100.0,
        )
        font, size = style.font, style.fontsize

    layout = None
    if a.pos:
        x, _, y = a.pos.partition(",")
        layout = PosLayout(align=a.an, x=float(x), y=float(y))
    elif a.margins:
        ml, mr, mv = (int(v) for v in a.margins.split(","))
        layout = MarginLayout(align=a.an, margin_l=ml, margin_r=mr, margin_v=mv, res_x=res[0], res_y=res[1])

    m = measure_text(style, a.text, layout, font_dirs=[a.font_dir] if a.font_dir else None)
    print(f"文字      {a.text!r}  {font} {size:g}")
    print(f"排版框    寬 {m.width:.2f}  高 {m.height:.2f}  descent {m.descent:.2f}  extlead {m.extlead:.2f}")
    print(
        f"字身框    寬 {m.ink_width:.2f}  高 {m.ink_height:.2f}  "
        f"留白 左{m.padding_left:.2f} 右{m.padding_right:.2f} 上{m.padding_top:.2f} 下{m.padding_bottom:.2f}"
    )
    if m.position:
        p = m.position
        print(f"karaskel  left {p.left:.1f} center {p.center:.1f} right {p.right:.1f}")
        print(f"          top {p.top:.1f} middle {p.middle:.1f} bottom {p.bottom:.1f}")
    if a.chars:
        for c in m.chars:
            print(f"  {c.index:3d} {c.char!r:6} 寬 {c.width:7.2f}  left {c.left:8.2f} center {c.center:8.2f}")
    for w in m.warnings:
        print(f"警告      {w}")

    if a.ink:
        px, py = 960, 540
        tags = (
            rf"\an8\pos({px},{py})\fn@{font}\frz270" if a.vertical else rf"\an5\pos({px},{py})\fn{font}"
        ) + rf"\b{1 if (a.bold or getattr(style, 'bold', 0)) else 0}\fs{size:g}\fsp{a.spacing:g}\bord0\shad0"
        if a.scale:
            tags += rf"\fscx{a.scale[0]:g}\fscy{a.scale[1]:g}"
        bb = ink_box(a.text, tags, res, a.font_dir)
        if bb is None:
            print("ink       什麼都沒畫出來——字型裝了嗎？")
            return 1
        left, top, right, bottom = bb
        print(f"ink(像素) {right - left} x {bottom - top}   絕對框 {bb}")
        print(f"          相對 \\pos：左 {left - px:+}  右 {right - px:+}  上 {top - py:+}  下 {bottom - py:+}")
        print(f"          對照 API 的字身框 {m.ink_width:.1f} x {m.ink_height:.1f}（差幾 px 是正常的）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
