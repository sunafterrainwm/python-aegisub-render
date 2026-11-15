#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 ffmpeg/libass 實際燒字，比較兩個 .ass 的畫面。

fx 行文字層面的 0.01px 看不出意義，這裡才是「渲染結果有沒有變」的答案。

做三件事：
1. 整部算 PSNR（每格也會記錄），順便數有幾格是逐像素完全相同
2. 逐格的最大像素差（blend=difference + signalstats），找出最差的幾格
3. 對最差的那幾格（與抽樣的幾格）實際數「差超過門檻的像素有幾個」
   —— 需要 numpy + Pillow，沒裝就跳過這步

用法:
    python frame_diff.py VIDEO REF.ass NEW.ass [-t 90] [--top 4] [--sample 6]
"""
import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile



if hasattr(sys.stdout, 'reconfigure'):          # CJK 版 Windows 的 console 編碼印不出日文
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def esc(path):
    """ffmpeg filter 參數裡的路徑轉義（Windows 的 : 與反斜線）。"""
    p = path.replace('\\', '/')
    return p.replace(':', r'\:').replace("'", r"\'")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors='replace')


def psnr_pass(video, ref, new, seconds, tmp):
    log = os.path.join(tmp, 'psnr.log')
    cmd = ['ffmpeg', '-hide_banner', '-v', 'info', '-i', video, '-i', video,
           '-filter_complex',
           "[0:v]subtitles='%s'[a];[1:v]subtitles='%s'[b];[a][b]psnr=stats_file='%s'"
           % (esc(ref), esc(new), esc(log))]
    if seconds:
        cmd += ['-t', str(seconds)]
    cmd += ['-f', 'null', '-']
    r = run(cmd)
    summary = re.search(r'PSNR.*', r.stderr)
    inf = 0
    vals = []
    if os.path.exists(log):
        for line in open(log):
            m = re.search(r'psnr_avg:(\S+)', line)
            if m:
                if m.group(1) == 'inf':
                    inf += 1
                else:
                    vals.append(float(m.group(1)))
    return summary.group(0) if summary else r.stderr[-400:], inf, vals


def ymax_pass(video, ref, new, seconds, tmp):
    log = os.path.join(tmp, 'ymax.log')
    cmd = ['ffmpeg', '-hide_banner', '-v', 'info', '-i', video, '-i', video,
           '-filter_complex',
           "[0:v]subtitles='%s',format=gbrp[a];[1:v]subtitles='%s',format=gbrp[b];"
           "[a][b]blend=all_mode=difference,signalstats,"
           "metadata=print:key=lavfi.signalstats.YMAX:file='%s'"
           % (esc(ref), esc(new), esc(log))]
    if seconds:
        cmd += ['-t', str(seconds)]
    cmd += ['-f', 'null', '-']
    run(cmd)
    rows = []
    cur = None
    if os.path.exists(log):
        for line in open(log):
            m = re.match(r'frame:(\d+)\s+pts:\S+\s+pts_time:([\d.]+)', line)
            if m:
                cur = (int(m.group(1)), float(m.group(2)))
            m2 = re.search(r'YMAX=([\d.]+)', line)
            if m2 and cur:
                rows.append((cur[0], cur[1], float(m2.group(1))))
    return rows


def pixel_pass(video, ref, new, frames, tmp, thresh=8):
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print('（沒有 numpy / Pillow，跳過逐像素統計）')
        return
    for n in frames:
        paths = []
        for i, ass in enumerate((ref, new)):
            out = os.path.join(tmp, 'f%d_%d.png' % (n, i))
            run(['ffmpeg', '-hide_banner', '-v', 'error', '-i', video, '-vf',
                 "subtitles='%s',select=eq(n\\,%d)" % (esc(ass), n),
                 '-frames:v', '1', '-y', out])
            paths.append(out)
        if not all(os.path.exists(p) for p in paths):
            continue
        a = np.asarray(Image.open(paths[0]).convert('RGB')).astype(int)
        b = np.asarray(Image.open(paths[1]).convert('RGB')).astype(int)
        d = np.abs(a - b).max(axis=2)
        n_gt = int((d > thresh).sum())
        print('  frame %6d  最大差 %3d  差>%d 的像素 %6d  有差的像素 %6d  （共 %d，%.4f%%）'
              % (n, d.max(), thresh, n_gt, int((d > 0).sum()), d.size,
                 100.0 * n_gt / d.size))
        if n_gt:
            ys, xs = (d > thresh).nonzero()
            print('        範圍 x=%d..%d y=%d..%d' % (xs.min(), xs.max(), ys.min(), ys.max()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('ref')
    ap.add_argument('new')
    ap.add_argument('-t', '--seconds', type=float, default=None)
    ap.add_argument('--top', type=int, default=4, help='最差幾格做逐像素統計')
    ap.add_argument('--sample', type=int, default=6, help='另外抽樣幾格')
    args = ap.parse_args()

    if not shutil.which('ffmpeg'):
        print('找不到 ffmpeg')
        return 1

    tmp = tempfile.mkdtemp(prefix='framediff-')
    try:
        summary, inf, vals = psnr_pass(args.video, args.ref, args.new, args.seconds, tmp)
        print(summary)
        n = len(vals) + inf
        if n:
            print('格數 %d，逐像素完全相同 %d (%.1f%%)%s'
                  % (n, inf, 100.0 * inf / n,
                     '，其餘最低 PSNR %.1f dB' % min(vals) if vals else ''))

        rows = ymax_pass(args.video, args.ref, args.new, args.seconds, tmp)
        if rows:
            # 完全相同的格子在這個管線裡會是固定的底值（通常 16），取最小值當底
            floor = min(r[2] for r in rows)
            diffy = [r for r in rows if r[2] > floor]
            print('逐格最大像素差 > 底值(%g) 的格數: %d / %d' % (floor, len(diffy), len(rows)))
            worst = sorted(rows, key=lambda r: -r[2])[:args.top]
            print('最差的格: %s' % ', '.join('#%d(%.2fs, %g)' % (r[0], r[1], r[2]) for r in worst))
            picks = [r[0] for r in worst]
            if args.sample and rows:
                step = max(1, len(rows) // (args.sample + 1))
                picks += [rows[i][0] for i in range(step, len(rows), step)][:args.sample]
            print('逐像素統計:')
            pixel_pass(args.video, args.ref, args.new, picks, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
