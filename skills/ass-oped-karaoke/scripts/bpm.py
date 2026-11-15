"""估計 BPM 與第一拍的時間（Lua 裡的 `BPM` / `B0` 就是從這裡來的）。

    python -X utf8 bpm.py ep01.mp4 --start 183.975 --duration 90
    # BPM=158.98   first beat 184.010s   ->   B0=184010

兩段式：先用頻譜起音的自相關抓粗略 tempo（把拍與 2×／4× 倍數一起加總，避免抓成半速或倍速），
再在它附近做 tempo × 相位的細搜尋，讓起音正好落在格線上。

回報的「拍點強度」低於平均的 2 倍就是**沒對上**，不要硬用——換一段時間（避開前奏與間奏）再試，
或改用 `spec.py` 用眼睛對。`B0` 是**本篇時間軸上第一拍的絕對毫秒**；NC 版要跟著平移
（`variants.py nc --const <常數名>`）。

需要 numpy 與 ffmpeg。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402

SR = 16000


def flux(x, hop: int, smooth: int):
    """頻譜起音強度與它的取樣率。"""
    import numpy as np

    n = 1024
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    spec = np.log1p(10 * np.abs(np.fft.rfft(frames, axis=1)))
    f = np.maximum(0, np.diff(spec, axis=0)).sum(1)
    f -= np.convolve(f, np.ones(smooth) / smooth, "same")
    return np.maximum(f, 0), SR / hop


def estimate(x, min_bpm: float, max_bpm: float) -> tuple[float, float, float, float]:
    import numpy as np

    f, fps = flux(x, 160, 50)
    ac = np.correlate(f, f, "full")[len(f) - 1 :]
    cand: list[tuple[float, float]] = []
    for bpm in np.arange(min_bpm, max_bpm, 0.25):
        lag = 60 / bpm * fps
        cand.append((float(np.interp([lag, 2 * lag, 4 * lag], np.arange(len(ac)), ac).sum()), float(bpm)))
    cand.sort(reverse=True)
    coarse = cand[0][1]
    print("粗估候選：" + "  ".join(f"{b:.2f}" for _, b in cand[:5]))

    f, fps = flux(x, 80, 100)
    t_end = len(f) / fps
    best = (0.0, coarse, 0.0)
    for bpm in np.arange(coarse - 1, coarse + 1, 0.01):
        per = 60 / bpm
        for ph in np.arange(0, per, 0.005):
            idx = (np.arange(ph, t_end, per) * fps).astype(int)
            v = float(f[idx].mean())
            if v > best[0]:
                best = (v, float(bpm), float(ph))
    return best[1], best[2], best[0], float(f.mean())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--start", type=float, required=True, help="分析起點（秒，影片時間軸）")
    ap.add_argument("--duration", type=float, default=90, help="分析長度（秒）")
    ap.add_argument("--min-bpm", type=float, default=70)
    ap.add_argument("--max-bpm", type=float, default=200)
    a = ap.parse_args()

    x = audio_mod.pcm_mono(a.video, a.start, a.duration, SR)
    bpm, phase, score, mean = estimate(x, a.min_bpm, a.max_bpm)
    print(f"\nBPM={bpm:.2f}   第一拍 {a.start + phase:.3f}s   ->   B0={round((a.start + phase) * 1000)}")
    ratio = score / mean if mean else 0
    print(f"（拍點強度 {score:.1f} vs 平均 {mean:.1f}，{ratio:.1f}×" + ("）" if ratio >= 2 else "，低於 2× 代表沒對上）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
