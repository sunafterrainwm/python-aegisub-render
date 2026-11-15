"""比對兩個 .ass 的 fx 行：Layer、起訖時間、Text 的多重集合（Style／Name 不比）。

    python -X utf8 cmpfx.py 舊.ass 新.ass
    python -X utf8 cmpfx.py 舊.ass 新.ass --ignore-fn        # 先拿掉 \\fn 再比（只換字型時）
    python -X utf8 cmpfx.py 舊.ass 新.ass --show 10          # 多印幾條差異

**重構之前先備份交付檔**，重構之後用它證明「畫面沒變」：理想是 0 差異。
有差異時逐條看是不是預期中的；NC 版與本篇比時，只有刻意不同的那幾句該有差異（見 variants.md）。
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402

FN = re.compile(r"\\fn[^\\}]*")


def fx_counter(path: Path, ignore_fn: bool) -> Counter[tuple[int, int, int, str]]:
    out: Counter[tuple[int, int, int, str]] = Counter()
    for e in oped.load(path).events:
        if not oped.is_fx(e):
            continue
        text = FN.sub("", e.text) if ignore_fn else e.text
        out[(e.layer, e.start, e.end, text)] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--ignore-fn", action="store_true")
    ap.add_argument("--show", type=int, default=3, help="各印幾條差異（預設 3）")
    a = ap.parse_args()

    old = fx_counter(a.old, a.ignore_fn)
    new = fx_counter(a.new, a.ignore_fn)
    only_old, only_new = old - new, new - old
    print(
        f"舊 {sum(old.values())}  新 {sum(new.values())}  "
        f"只在舊 {sum(only_old.values())}  只在新 {sum(only_new.values())}"
    )
    for label, c in (("舊", only_old), ("新", only_new)):
        for k in list(c)[: a.show]:
            print(f"  只在{label}: layer {k[0]} {oped.ass_formatted(k[1])} {k[3][:140]}")
    return 0 if not (only_old or only_new) else 1


if __name__ == "__main__":
    sys.exit(main())
