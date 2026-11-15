#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已渲染的 fx 行歸回各卡拉OK來源行，列出元素清單與時間。

兩個用途：

1. **盤點**：每句歌詞各產生了幾條、分佈在哪些 layer。這張表就是元素清單，
   改寫完之後再跑一次對照，數量不對馬上知道是哪一句、哪一個元素。
2. **反推被寫死的時間資料**：被硬編碼化的檔案常常已經跑不動了（Name 被清空、
   時間表整體位移…），但已渲染的 fx 行是對的。`--times LAYER` 會印出該 layer
   每條行的起始時間與相對行首的偏移 —— 拍點之類的資料就在裡面。

歸屬判定：先用時間範圍（kara-templater 的輸出不會超出來源行的範圍），
多個候選時依序用「Name 欄位相同」→「樣式完全相同」→「樣式第一個字相同」縮小。
Name 是最可靠的（輸出行的 Name 是從來源行複製來的），樣式則要容忍解藕之後
「來源行掛量字樣式 `OPJP fx`、輸出行是 `OPJP`」。

字幕的讀取與時間換算都走 `aegisub_render`（見 ktd.py）。

用法:
    python -X utf8 fx_inventory.py FILE.ass                # 每句的 layer 計數
    python -X utf8 fx_inventory.py FILE.ass --times 13     # 再列出 layer 13 的起始時間
    python -X utf8 fx_inventory.py FILE.ass --style OPJP   # 只看來源樣式開頭是 OPJP 的
"""

import argparse
import collections
import sys

from ktd import ass_formatted, fx_lines, karaoke_lines, load

if hasattr(sys.stdout, "reconfigure"):  # CJK 版 Windows 的 console 編碼印不出日文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def assign(src, fx):
    """回傳 (來源行 index -> fx 行清單, 歸不到的清單)"""
    owner = collections.defaultdict(list)
    orphan = []
    for r in fx:
        hit = [i for i, s in enumerate(src) if s.start <= r.start and r.end <= s.end]
        for narrow in (
            lambda i: src[i].actor == r.actor,
            lambda i: src[i].style == r.style,
            lambda i: src[i].style.split()[0] == r.style.split()[0],
        ):
            if len(hit) > 1:
                better = [i for i in hit if narrow(i)]
                if better:
                    hit = better
        if len(hit) == 1:
            owner[hit[0]].append(r)
        else:
            orphan.append((len(hit), r))
    return owner, orphan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ass")
    ap.add_argument(
        "--times",
        type=int,
        action="append",
        default=[],
        help="額外列出這個 layer 每條行的起始時間（可重複）",
    )
    ap.add_argument("--style", default=None, help="只看來源樣式開頭是這個字的行")
    args = ap.parse_args()

    ass = load(args.ass)
    src = karaoke_lines(ass)
    fx = fx_lines(ass)
    print("卡拉OK來源行 %d 條，fx 行 %d 條" % (len(src), len(fx)))
    if not src:
        print("找不到 Effect=karaoke 的行；這個檔案可能只有渲染結果。")
        return 1

    owner, orphan = assign(src, fx)
    if orphan:
        print("!! %d 條 fx 行歸不到唯一的來源行（時間範圍重疊又分不出樣式？）" % len(orphan))
        for n, r in orphan[:5]:
            print(
                "   候選 %d 個: layer=%d %s-%s %s %s"
                % (n, r.layer, ass_formatted(r.start), ass_formatted(r.end), r.style, r.text[:50])
            )

    total = collections.Counter()
    for i, s in enumerate(src):
        if args.style and not s.style.startswith(args.style):
            continue
        rows = owner[i]
        per = collections.Counter(x.layer for x in rows)
        total.update(per)
        print(
            "[%2d] %-13s %s  Name=%-22s %3d 條  layer=%s  %s"
            % (
                i,
                s.style,
                ass_formatted(s.start),
                s.actor or "-",
                len(rows),
                dict(sorted(per.items())),
                s.get_stripped_text()[:26],
            )
        )
        for layer in args.times:
            ts = sorted({x.start for x in rows if x.layer == layer})
            if ts:
                print("      layer %d 起始時間: %s" % (layer, " ".join(str(t) for t in ts)))
                print("      （相對行首: %s）" % " ".join(str(t - s.start) for t in ts))
    print("全部 layer 計數: %s" % dict(sorted(total.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
