#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比對兩個 .ass 的 fx 行（Effect == "fx" 的 Dialogue 行）。

直接 diff 沒有意義：改寫成一條模板一個元素之後，fx 行的順序一定會變，
座標也會因為改用實測字寬而差一點。所以這裡做三件事：

1. 多重集合比對 —— 有幾條是逐字完全相同的
2. 骨架配對 —— 把數字全部換成 # 之後，剩下的（Layer, Start, End, Style, 骨架）
   一樣的行互相配對。配不到對 = 標籤結構不一樣 = 真的寫錯了
3. 對配到的那些行逐一比數值，給出最大差與差分佈

Name 欄位預設忽略（解藕之後設定會搬進 Name，輸出行必然帶著）。

字幕的讀取走 `aegisub_render`（見 ktd.py）：被比對的字串是 `entry_data()` 產生的，
和存檔時寫出去的那一行相同。

用法:
    python -X utf8 compare_fx.py BASELINE.ass NEW.ass [--name] [--show N] [--layer N]

    --name     連 Name 欄位一起比
    --show N   最多印幾組有差異的範例（預設 3）
    --layer N  只比這個 layer
"""
import argparse
import collections
import re
import sys

from ktd import canonical, fx_lines, load

NUM = re.compile(r'-?\d+\.?\d*')


if hasattr(sys.stdout, 'reconfigure'):          # CJK 版 Windows 的 console 編碼印不出日文
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def read_fx(path, keep_name=False, layer=None):
    return [canonical(ev, keep_name) for ev in fx_lines(load(path), layer)]


def skeleton(line):
    f = line.split(',', 9)
    return (f[0], f[1], f[2], f[3], NUM.sub('#', f[9]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('baseline')
    ap.add_argument('new')
    ap.add_argument('--name', action='store_true', help='Name 欄位也要一致')
    ap.add_argument('--show', type=int, default=3)
    ap.add_argument('--layer', type=int, default=None)
    args = ap.parse_args()

    a = read_fx(args.baseline, args.name, args.layer)
    b = read_fx(args.new, args.name, args.layer)
    ca, cb = collections.Counter(a), collections.Counter(b)

    print('fx 行數      : %s = %d, %s = %d  %s'
          % (args.baseline, len(a), args.new, len(b),
             'OK' if len(a) == len(b) else '<<< 條數不同，邏輯有錯'))
    print('逐字完全相同 : %d / %d' % (sum((ca & cb).values()), len(a)))

    ga = collections.defaultdict(list)
    gb = collections.defaultdict(list)
    for line in (ca - cb).elements():
        ga[skeleton(line)].append(line)
    for line in (cb - ca).elements():
        gb[skeleton(line)].append(line)

    only_a = [x for k, v in ga.items() if k not in gb for x in v]
    only_b = [x for k, v in gb.items() if k not in ga for x in v]
    print('骨架配不到對 : baseline %d / new %d  %s'
          % (len(only_a), len(only_b), 'OK' if not (only_a or only_b) else '<<< 標籤結構不同'))
    for x in only_a[:args.show]:
        print('  只在 baseline: ' + x[:220])
    for x in only_b[:args.show]:
        print('  只在 new     : ' + x[:220])

    worst = (0.0, None, None)
    hist = collections.Counter()
    pairs = 0
    samples = []
    for k in ga:
        if k not in gb:
            continue
        for x, y in zip(sorted(ga[k]), sorted(gb[k])):
            pairs += 1
            nx = [float(v) for v in NUM.findall(x)]
            ny = [float(v) for v in NUM.findall(y)]
            local = 0.0
            for u, v in zip(nx, ny):
                d = abs(u - v)
                hist[round(d, 2)] += 1
                local = max(local, d)
                if d > worst[0]:
                    worst = (d, x, y)
            if len(samples) < args.show:
                samples.append((local, x, y))

    print('骨架相同但數值有差 : %d 行' % pairs)
    if pairs:
        print('單一數值最大差     : %.4f px  %s'
              % (worst[0], 'OK' if worst[0] <= 0.1 + 1e-9 else '<<< 超過量測誤差，檢查幾何推導'))
        print('差分佈（到 0.01）  : %s' % dict(sorted(hist.items())[:14]))
        for d, x, y in samples:
            print('  --- 這組最大差 %.4f' % d)
            print('  baseline: ' + x[:230])
            print('  new     : ' + y[:230])

    bad = len(a) != len(b) or only_a or only_b or worst[0] > 0.2 + 1e-9
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
