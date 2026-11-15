"""交付前的驗證：環境、可套用、冪等、行數與同時事件數。

    python -X utf8 verify.py env                 # 環境：aegisub_render、Aegisub 安裝、ffmpeg、字型
    python -X utf8 verify.py x.ass               # 可套用 + 冪等（**交付前一定要跑**）
    python -X utf8 verify.py x.ass --stats       # 只看行數與同時事件數，不渲染
    python -X utf8 verify.py a.ass b.ass c.ass   # 一次驗好幾個

冪等＝「把套用過的結果再套用一次，結果逐位元組相同」。不相同代表模板吃到了自己上一次的輸出
（例如來源行被模板改到、或 fx 行沒有被正確地重建），那是會在下一次改稿爆開的問題。

「最新」＝檔案裡存的 fx 行和現在重新套用的結果相同。這一項要靠量字，所以**樣式用到的字型
這台機器沒有時無法判定**：量字會退到替代字型，座標本來就和原本渲染的那台機器不同。
這時會印「無法判定」並以 exit 2 結束（真的有項目沒過是 exit 1）——不要為了讓它變綠
就在缺字型的機器上重新渲染覆蓋交付檔，那會把正確的座標換成替代字型的座標。
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402


def check_env() -> int:
    bad = 0
    import aegisub_render

    print(f"aegisub_render  {aegisub_render.__file__}")
    settings = oped.settings_for()
    root = settings.aegisub_root
    ok = (root / "automation").is_dir()
    print(f"Aegisub 安裝     {root}  {'OK' if ok else '找不到 automation/，用 --config 或 config.py 指定'}")
    bad += 0 if ok else 1
    kara = root / "automation" / "autoload" / "kara-templater.lua"
    print(f"kara-templater  {'OK' if kara.is_file() else '找不到：' + str(kara)}")
    bad += 0 if kara.is_file() else 1
    ff = shutil.which("ffmpeg")
    print(f"ffmpeg          {ff or '找不到（只有取幀／頻譜／對位需要）'}")
    try:
        import av  # noqa: F401

        print("PyAV            OK（可以讀影片時間碼）")
    except ImportError:
        print("PyAV            沒有（--fps 仍可用；要讀真影片請裝 video extra）")
    try:
        from aegisub_render import MeasureStyle, measure_text

        m = measure_text(MeasureStyle(font="Arial", fontsize=48), "Ag")
        print(f"量字            OK（Arial 'Ag' 寬 {m.width:g}）")
    except Exception as e:  # noqa: BLE001
        print(f"量字            失敗：{e}")
        bad += 1
    return bad


def verify_one(path: Path, *, font_dirs: list[Path], stats_only: bool) -> tuple[int, int]:
    """回傳（沒過的項數, 無法判定的項數）。"""
    ass = oped.load(path)
    print(f"\n{path.name}: {oped.stats(ass, size=path.stat().st_size)}")
    if stats_only:
        return 0, 0
    settings = oped.settings_for(font_dirs)
    bad = 0
    missing = oped.missing_fonts(ass, settings.font_dirs)
    if missing:
        what = "、".join(f"{want}（量字退到 {got}）" for want, got in missing)
        print(f"  字型      缺 {what}")
    # 1) 能不能套用（validation 要過，沒有 force 這條路）；順便拿到第一次的結果
    try:
        once = oped.render_bytes(path, settings=settings)
    except SystemExit as e:
        print(f"  可套用    NO：{e}")
        return 1, 0
    print("  可套用    OK（validation 通過）")
    # 2) 冪等：把結果寫出去再套一次
    with tempfile.TemporaryDirectory() as tmp:
        p2 = Path(tmp) / path.name
        p2.write_bytes(once)
        twice = oped.render_bytes(p2, settings=settings)
    if once == twice:
        print("  冪等      OK（重新套用逐位元組相同）")
    else:
        print(f"  冪等      NO（{len(once)} vs {len(twice)} bytes）")
        bad += 1
    # 3) 檔案裡存的結果是不是最新的
    unknown = 0
    if path.read_bytes() == once:
        print("  最新      OK（檔案裡存的就是重新套用的結果）")
        if missing:
            print("            但座標是照替代字型算的：交付前要在裝有該字型的機器上重新套用一次")
    elif missing:
        print("  最新      無法判定：這台機器缺字型，量字用的是替代字型，重新套用的座標本來就會不同")
        print("            裝好字型（或用 --font-dir 指過去）再驗；不要在這台機器上重新渲染覆蓋交付檔")
        unknown += 1
    else:
        print("  最新      NO：檔案裡的 fx 行和重新套用的結果不同，記得重新渲染並存檔")
        bad += 1
    return bad, unknown


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="+", help="`env` 或要驗的 .ass")
    ap.add_argument("--stats", action="store_true", help="只看行數，不渲染")
    ap.add_argument("--font-dir", type=Path, action="append", default=[])
    a = ap.parse_args()

    if a.targets == ["env"]:
        return 1 if check_env() else 0
    bad = unknown = 0
    for t in a.targets:
        b, u = verify_one(Path(t), font_dirs=a.font_dir, stats_only=a.stats)
        bad += b
        unknown += u
    if bad:
        print(f"\n{bad} 項沒過" + (f"、{unknown} 項無法判定（缺字型）" if unknown else ""))
        return 1
    if unknown:
        print(f"\n{unknown} 項無法判定（缺字型）；其餘通過")
        return 2
    print("\n全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
