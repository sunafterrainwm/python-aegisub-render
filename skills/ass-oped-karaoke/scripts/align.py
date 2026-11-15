"""歌詞對位器：聽人聲的起音與旋律變化，把每個詞吸到附近的節拍格點上。

主要的初稿工具是 `falign.py`（人聲分離＋強制對位）；這支是它之後的微調（`falign.py --refine`
就是用 `--prior current --sigma 0.25`、音訊來源換成分離後的人聲來跑這支），
或沒有強制對位環境時的替代（`--prior spread`，粗得多）。

    # 報告：每個詞「目前 → 對位結果」（以拍為單位）
    python -X utf8 align.py x.ass --video ep01.mp4 --style OPJP --grid 88659:162.96 --report

    # 把指定的幾行寫回去（**一定要 --only**，否則會把已經用耳朵確認過的詞也改掉）
    python -X utf8 align.py x.ass --video ep01.mp4 --style OPJP --grid 88659:162.96 \\
        --only 0:22:39.17 0:22:45.13 --write

    # 作品自己的漢字讀音表與釘住的詞
    python -X utf8 align.py x.ass ... --readings readings.json --pin 0:22:39.17:3 0:22:45.13:3

`--grid B0:BPM`：`B0` 是第 0 拍的絕對毫秒（用 `bpm.py` 量），`BPM` 是速度。

**先驗（`--prior`）決定結果能不能用。** 對位器不是從零聽出每個詞在哪，而是在「大概的位置」附近
找最像起音的格點：

* `current`：以行上現有的 `\\k` 當大概位置（±0.6 拍，`--sigma` 可改）。只有在 `\\k` 已經有初稿
  （強制對位或手抓的）時才對；拿平均分配的假時間來跑，結果只會貼著假時間，等於沒跑。
* `spread`：不信任現有的 `\\k`，改以「音拍數在行內等比例分配」當大概位置並放寬到 ±2 拍。
  歌詞剛切好分詞、還沒有任何時間、又沒有強制對位可用時才用這個；它是從起音反推歌詞，
  實測只有一成多的詞落在 ±1/4 拍內（見 references/timing.md），幾乎每句都要再對著頻譜修。
* `auto`（預設）：一行的詞全部擠在同一個起點（剛切好、全是 `\\k0`）就用 `spread`，否則 `current`。

`--report` 會標出用了哪一種，以及相鄰兩個詞距離不到 1/4 拍的地方（`!`）——那種行不要採用。

做法：
1. 從影片切出該段立體聲音訊（快取在 `--cache`），只取「置中聲像 × 有音高（HPSS 諧波）」的人聲成分；
2. 證據 ＝ 人聲頻帶的起音強度 ＋ 旋律音高變化（4096 點 chroma 的前後相似度）；
3. 每個詞拆成假名音拍（漢字查 `--readings`，非日文就整個詞算一拍），用 Viterbi 放到 1/4 拍格點上：
   節奏先驗（詞內偏八分／十六分、詞間可休止）＋ 證據 ＋ 目前時間的位置先驗（寬鬆）；
   「ー」不需要起音且偏好落在長音上，「ん／っ」只弱要求；
4. `--phrases`：**同一個詞**（讀音逐字相同）在行內同一個位置重複 ≥3 次（`--min-repeat`）時，
   先互相對齊、平均證據、只解一次節奏再套回每一次。只認歌詞相同的重複：音拍形狀剛好一樣的
   不同詞不是同一個樂句，硬對齊只會把它們擠在一起。

**這只是初稿。** 聽起來不對的詞直接改數字（`ktime.py`）或在 Aegisub 調；確認過的詞加進 `--pin`，
之後重跑就不會動到它。中文行不要對位，用 `ktime.py copy --from OPJP --to OPCHT` 複製過去。

需要 numpy 與 ffmpeg。
"""

import argparse
import json
import math
import pickle
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402
import ktime  # noqa: E402
import oped  # noqa: E402
from oped import AssDialogue, ass_formatted, parse_time  # noqa: E402

HOP = 220  # 5 ms @ 44.1 kHz
SMALL = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
# 節奏先驗（鍵是距離，單位 1/4 拍）：詞內／詞間／長音進入／長音離開
INWORD = {1: 0.20, 2: 0.35, 3: 0.10, 4: 0.20, 5: 0.03, 6: 0.08, 8: 0.04}
ACROSS = {1: 0.10, 2: 0.30, 3: 0.08, 4: 0.25, 5: 0.03, 6: 0.10, 8: 0.08, 10: 0.02, 12: 0.03, 16: 0.01}
HOLD_IN = {1: 0.02, 2: 0.10, 3: 0.10, 4: 0.33, 5: 0.05, 6: 0.25, 8: 0.15}
HOLD_OUT = {1: 0.03, 2: 0.15, 3: 0.10, 4: 0.30, 5: 0.05, 6: 0.22, 8: 0.15}
KIND_W = {"full": 1.0, "weak": 0.5, "hold": 0.0}
RHYTHM_W = 0.5


# --- 音拍 -------------------------------------------------------------------------------


def morae(word: str, readings: dict[str, str]) -> list[tuple[str, str]]:
    """把一個詞拆成 (字, 種類)；種類 full＝要有起音、weak＝弱、hold＝長音不需要起音。"""
    for k in sorted(readings, key=len, reverse=True):
        word = word.replace(k, readings[k])
    out: list[tuple[str, str]] = []
    for ch in word:
        if ch in SMALL and out:
            out[-1] = (out[-1][0] + ch, out[-1][1])
        elif ch == "ー":
            out.append((ch, "hold"))
        elif ch in "んンっッ":
            out.append((ch, "weak"))
        elif "ぁ" <= ch <= "ヺ":
            out.append((ch, "full"))
    return out or [(word, "full")]


# --- 證據 -------------------------------------------------------------------------------


def _median_time(x, k: int):
    import numpy as np

    pad = k // 2
    xp = np.pad(x, ((pad, pad), (0, 0)), mode="edge")
    out = np.empty_like(x)
    for a in range(0, x.shape[0], 1500):
        b = min(a + 1500, x.shape[0])
        w = np.lib.stride_tricks.sliding_window_view(xp[a : b + 2 * pad], k, axis=0)
        out[a:b] = np.median(w, axis=-1)
    return out


def _median_freq(x, k: int):
    import numpy as np

    pad = k // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="edge")
    out = np.empty_like(x)
    for a in range(0, x.shape[0], 1500):
        b = min(a + 1500, x.shape[0])
        w = np.lib.stride_tricks.sliding_window_view(xp[a:b], k, axis=1)
        out[a:b] = np.median(w, axis=-1)
    return out


def features(wav: Path, t0_ms: int, cache: Path | None = None):
    """(時間 ms, 起音強度, 旋律變化)，時間戳對準分析視窗的中心。"""
    import numpy as np

    if cache and cache.exists():
        return pickle.loads(cache.read_bytes())
    w = wave.open(str(wav))
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32).reshape(-1, 2) / 32768
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2

    def spec(s, n: int):
        win = np.hanning(n).astype(np.float32)
        fr = np.lib.stride_tricks.sliding_window_view(s, n)[::HOP] * win
        return np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32)

    def centre_harmonic(n: int, lo: float, hi: float, kt: int, kf: int):
        f = np.fft.rfftfreq(n, 1 / sr)
        b = (f > lo) & (f < hi)
        a_, s_ = spec(mid, n)[:, b], spec(side, n)[:, b]
        h, p = _median_time(a_, kt), _median_freq(a_, kf)
        return f[b], a_ * (h**2 / (h**2 + p**2 + 1e-9)) * (a_ / (a_ + s_ + 1e-6)) ** 2

    _, v = centre_harmonic(1024, 150, 6000, 9, 15)
    lg = np.log1p(300 * v)
    d = 3
    flux = np.concatenate([np.zeros(d), np.maximum(0, lg[d:] - lg[:-d]).sum(axis=1)])
    t = t0_ms + (np.arange(len(flux)) * HOP + 512) / sr * 1000
    f4, v4 = centre_harmonic(4096, 170, 1400, 9, 25)
    pc = np.round(12 * np.log2(f4 / 440)).astype(int) % 12
    c = np.stack([v4[:, pc == k].sum(axis=1) for k in range(12)], axis=1)
    c = c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-9)
    k = 10
    chg4 = np.zeros(len(c))
    chg4[k:-k] = 1 - (c[2 * k :] * c[: -2 * k]).sum(axis=1)
    t4 = t0_ms + (np.arange(len(c)) * HOP + 2048) / sr * 1000
    chg = np.interp(t, t4, chg4)

    def norm(v_):
        v_ = np.maximum(v_ - np.convolve(v_, np.ones(161) / 161, mode="same"), 0)
        return v_ / (np.percentile(v_, 99) + 1e-9)

    res = (t, norm(flux), norm(chg))
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(res))
    return res


# --- 對位 -------------------------------------------------------------------------------


class Aligner:
    def __init__(self, feat, grid: tuple[float, float], w_flux: float = 0.7, w_chg: float = 1.2) -> None:
        self.t, self.flux, self.chg = feat
        self.g0, self.per = grid  # 第 0 拍的時間、每拍長度
        self.q = self.per / 4  # 1/4 拍格點
        self.wf, self.wc = w_flux, w_chg

    def ev(self, tm: float) -> float:
        m = (self.t >= tm - 30) & (self.t <= tm + 45)
        return float(self.wf * self.flux[m].max() + self.wc * self.chg[m].max()) if m.any() else 0.0

    def snap(self, tm: float) -> float:
        return self.g0 + round((tm - self.g0) / self.q) * self.q

    def viterbi(self, cand, ev, units, prior, sigma, last_max):
        import numpy as np

        n, m = len(units), len(cand)
        neg = -1e18
        sc = np.full((n, m), neg)
        bk = np.zeros((n, m), int)
        sig = sigma * self.per

        def pos_pen(i: int, c: int) -> float:
            wi, j, _ = units[i]
            return ((cand[c] - prior[wi]) / sig) ** 2 if j == 0 and wi in prior else 0.0

        for c in range(m):
            sc[0, c] = ev[c] - pos_pen(0, c)
        for i in range(1, n):
            _, j, kind = units[i]
            tab = INWORD if j > 0 else ACROSS
            if kind == "hold":
                tab = HOLD_IN
            elif units[i - 1][2] == "hold":
                tab = HOLD_OUT
            for c in range(1, m):
                if cand[c] > last_max:
                    break
                best, arg = neg, 0
                for dd in range(1, min(c, 16) + 1):
                    s = sc[i - 1, c - dd]
                    if s <= neg / 2:
                        continue
                    s += RHYTHM_W * math.log(tab.get(dd, 0.004))
                    if s > best:
                        best, arg = s, c - dd
                sc[i, c] = best + KIND_W[kind] * ev[c] - pos_pen(i, c)
                bk[i, c] = arg
        c = int(np.argmax(sc[n - 1]))
        pos = [0] * n
        for i in range(n - 1, -1, -1):
            pos[i] = c
            c = bk[i, c]
        return [cand[p] for p in pos]

    def align_words(self, units, prior, start, end, sigma=0.6):
        k0 = math.ceil((start - 0.5 * self.per - self.g0) / self.q)
        k1 = math.floor((end - 0.25 * self.per - self.g0) / self.q)
        cand = [self.g0 + k * self.q for k in range(k0, k1 + 1)]
        if len(cand) < 2:
            return {wi: prior.get(wi, start) for wi, j, _ in units if j == 0}
        ev = [self.ev(c) for c in cand]
        tm = self.viterbi(cand, ev, units, prior, sigma, cand[-1])
        return {wi: tm[i] for i, (wi, j, _) in enumerate(units) if j == 0}

    def template(self, units, anchors, span_beats):
        """一個重複樂句的各次出現：互相對齊 → 平均證據 → 只解一次節奏 → 套回每一次。"""
        import numpy as np

        anchors = [self.snap(a) for a in anchors]
        grid_rel = [k * self.q for k in range(-6, int(span_beats * 4) + 6)]
        curves = [np.array([self.ev(a + r) for r in grid_rel]) for a in anchors]
        shift = [0] * len(anchors)
        core = slice(4, len(grid_rel) - 4)
        for _ in range(8):
            new = []
            for j in range(len(anchors)):
                others = [np.roll(curves[o], -shift[o]) for o in range(len(anchors)) if o != j]
                ref = np.mean(others, axis=0)[core]
                new.append(
                    max(
                        (float(np.corrcoef(np.roll(curves[j], -s)[core], ref)[0, 1]) - 0.01 * abs(s), s)
                        for s in range(-4, 5)
                    )[1]
                )
            if new == shift:
                break
            shift = new
        anchors = [a + s * self.q for a, s in zip(anchors, shift)]
        # 重複樂句每次都落在小節的同一個位置：把例外整拍搬過去（要有過半數才知道誰是例外）
        barpos = [round(((a - self.g0) / self.per) % 4 * 4) % 16 for a in anchors]
        major = max(sorted(set(barpos)), key=barpos.count)
        for j, a in enumerate(anchors):
            if barpos[j] != major and barpos.count(major) * 2 > len(barpos):
                opts = [a + k * self.q for k in range(-8, 9) if (barpos[j] + k) % 16 == major]
                anchors[j] = min(opts, key=lambda o: abs(o - a))
        rel = [k * self.q for k in range(-6, int(span_beats * 4))]
        offs: list[float] = []
        for _ in range(3):
            ev = [float(np.mean([self.ev(a + r) for a in anchors])) for r in rel]
            tm = self.viterbi(rel, ev, units, {0: 0.0}, 1.0, rel[-1])
            offs = list(tm)
            new = []
            for a in anchors:
                best = max(
                    (sum(KIND_W[u[2]] * self.ev(a + s + o) for u, o in zip(units, offs)), s)
                    for s in (-self.q, 0, self.q)
                )
                new.append(a + best[1])
            if new == anchors:
                break
            anchors = new
        word_off = {wi: offs[i] for i, (wi, j, _) in enumerate(units) if j == 0}
        return word_off, anchors


# --- 從 .ass 取詞 ------------------------------------------------------------------------


def line_words(ev: AssDialogue) -> tuple[list[str], list[int], list[int]]:
    """(詞, 詞的起點 ms, 詞在音節表裡的索引)——空白音節不算詞。"""
    syls = ktime.syllables(ev)
    words, starts, idx = [], [], []
    for i, (cs, text) in enumerate(syls):
        if text.strip(" 　"):
            words.append(text.strip(" 　"))
            starts.append(ev.start + cs * 10)
            idx.append(i)
    return words, starts, idx


def write_back(ev: AssDialogue, idx: list[int], new_starts: list[float]) -> bool:
    """把對位結果寫回這一行的 `\\k`：空白音節跟著後面那個詞（長度 0）。"""
    syls = ktime.syllables(ev)
    cs = [c for c, _ in syls]
    for k, i in zip(idx, new_starts):
        cs[k] = max(0, round((i - ev.start) / 10))
    for i in range(len(cs)):
        if i not in idx:
            after = next((cs[j] for j in range(i + 1, len(cs))), cs[i])
            cs[i] = after
    # 第一個 `\k` 的起點就是行首。行首的空音節（`{\k0}` 或 `{\k30}` 這種前導）因此從 0 開始、
    # 長到第一個詞為止；不這樣做的話前導長度會被壓成 0，整行的詞跟著提早。
    if cs:
        cs[0] = 0
    for i in range(1, len(cs)):
        cs[i] = max(cs[i], cs[i - 1])
    # 最後一段的長度沒有東西可以對：行上原本有值就沿用；還是 `\\k0`（剛切好分詞的行）就拉到行尾，
    # 否則最後一個詞長度 0，逐詞模板會讓它整個不顯示。
    cs_end = None
    if ktime._last_duration(ev) == 0:
        cs_end = max(cs[-1], round((ev.end - ev.start) / 10)) if cs else None
    return ktime.set_cs(ev, cs, cs_end)


# --- 驅動 -------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path)
    ap.add_argument("--video", type=Path, help="音訊來源（省略＝字幕連結的 Video File）")
    ap.add_argument("--style", help="只處理這個樣式的行（通常是日文那個）")
    ap.add_argument("--grid", required=True, help="B0:BPM，例如 88659:162.96")
    ap.add_argument("--readings", type=Path, help="漢字讀音表 JSON（作品自己的）")
    ap.add_argument("--pin", nargs="*", default=[], help="釘住的詞：行起點:詞索引，例如 0:22:39.17:3")
    ap.add_argument("--only", nargs="*", default=[], help="只處理／只寫回這些行（行的開始時間）")
    ap.add_argument("--phrases", action="store_true", help="同一個詞在行內同位置重複出現時用模板比對")
    ap.add_argument("--min-repeat", type=int, default=3, help="--phrases：至少重複幾次才啟用（預設 3）")
    ap.add_argument(
        "--prior",
        choices=["auto", "current", "spread"],
        default="auto",
        help="詞的大概位置從哪來：current＝現有的 \\k；spread＝音拍等比例；auto＝全擠在同一點的行用 spread",
    )
    ap.add_argument("--sigma", type=float, help="位置先驗的寬度（拍）；預設 current 0.6、spread 2.0")
    ap.add_argument("--report", action="store_true", help="印出 目前 → 對位結果（以拍為單位）")
    ap.add_argument("--write", action="store_true", help="寫回 .ass（一定要配 --only）")
    ap.add_argument("--cache", type=Path, help="音訊與特徵的快取資料夾（預設 .align-cache）")
    a = ap.parse_args()

    if a.write and not a.only:
        raise SystemExit("--write 一定要配 --only：不指定行會把已經確認過的詞也改掉")

    b0, _, bpm = a.grid.partition(":")
    grid = (float(b0), 60000 / float(bpm))
    readings: dict[str, str] = json.loads(a.readings.read_text(encoding="utf-8")) if a.readings else {}
    pins = {(":".join(p.split(":")[:-1]), int(p.split(":")[-1])) for p in a.pin}
    cache = a.cache or (a.target.parent / ".align-cache")

    ass = oped.load(a.target)
    lines = [e for e in ktime.targets(ass) if not a.style or e.style == a.style]
    if not lines:
        raise SystemExit("沒有符合條件的行（--style 對嗎？）")

    video = a.video or oped.video_of(ass, a.target)
    if video is None or not Path(video).exists():
        raise SystemExit("需要 --video（或字幕裡要有可用的 Video File）")
    lo = min(e.start for e in lines) - 2000
    hi = max(e.end for e in lines) + 2000
    tag = f"{a.style or 'all'}_{lo}"
    wav = audio_mod.wav_stereo(Path(video), max(0, lo), hi - lo, cache / f"{tag}.wav")
    al = Aligner(features(wav, max(0, lo), cache / f"{tag}.pkl"), grid)

    # 每一行：單位表（詞 → 音拍）、先驗（目前的時間）
    plans = []
    for ev in lines:
        words, starts, idx = line_words(ev)
        if not words:
            continue
        units = [
            (wi, j, kind) for wi, w in enumerate(words) for j, (_, kind) in enumerate(morae(w, readings))
        ]
        plans.append({"ev": ev, "words": words, "starts": starts, "idx": idx, "units": units})

    fixed: dict[tuple[int, int], float] = {}
    spread_lines = 0
    squeezed: list[str] = []
    phrase_fallback: list[str] = []
    if a.phrases:
        groups: dict[tuple, list[tuple[int, int]]] = {}
        for li, p in enumerate(plans):
            for wi, w in enumerate(p["words"]):
                said = tuple(morae(w, readings))  # 讀音逐字相同才算同一個樂句
                groups.setdefault((said, wi), []).append((li, wi))
        for (said, _), occ in groups.items():
            if len(occ) < max(2, a.min_repeat):
                continue
            units = [(0, j, k) for j, (_, k) in enumerate(said)]
            anchors = [plans[li]["starts"][wi] for li, wi in occ]
            offs, anchors = al.template(units, anchors, 2.0)
            for (li, wi), anc in zip(occ, anchors):
                fixed[(li, wi)] = anc + offs.get(0, 0.0)
        # 每個詞是各自對齊的，所以同一行裡可能互相打架（後面的詞被搬到前面的詞之前或貼在一起）。
        # 那代表這一行的重複樂句沒對上：整行放棄模板結果，退回一般的逐行對位。
        for li, p in enumerate(plans):
            got = [fixed[(li, wi)] for wi in range(len(p["words"])) if (li, wi) in fixed]
            if any(b - a < al.q for a, b in zip(got, got[1:])):
                for wi in range(len(p["words"])):
                    fixed.pop((li, wi), None)
                phrase_fallback.append(ass_formatted(p["ev"].start))

    for li, p in enumerate(plans):
        ev: AssDialogue = p["ev"]
        stamp = ass_formatted(ev.start)
        for wi in range(len(p["words"])):
            if (stamp, wi) in pins:
                fixed[(li, wi)] = p["starts"][wi]
        # 大概位置：現有的 \k，或（沒有可用的 \k 時）音拍數在行內等比例分配
        usable = len(p["words"]) < 2 or len(set(p["starts"])) > 1
        mode = a.prior if a.prior != "auto" else ("current" if usable else "spread")
        if mode == "spread":
            total = len(p["units"])
            before = [sum(1 for u in p["units"] if u[0] < wi) for wi in range(len(p["words"]))]
            base = [ev.start + (ev.end - ev.start) * b / total for b in before]
            sigma = a.sigma or 2.0
            spread_lines += 1
        else:
            base = p["starts"]
            sigma = a.sigma or 0.6
        prior = {wi: fixed.get((li, wi), t) for wi, t in enumerate(base)}
        if all((li, wi) in fixed for wi in range(len(p["words"]))):
            res = prior
        else:
            res = al.align_words(p["units"], prior, ev.start, ev.end, sigma)
            res = {wi: fixed.get((li, wi), res[wi]) for wi in range(len(p["words"]))}
        new = [min(max(res[wi], ev.start), ev.end - 100) for wi in range(len(p["words"]))]
        for i in range(1, len(new)):
            new[i] = max(new[i], new[i - 1] + 60)
        p["new"] = new
        tight = [i for i in range(1, len(new)) if new[i] - new[i - 1] < 0.9 * al.q]
        if tight:
            squeezed.append(stamp)
        if a.report:
            def beat(t: float) -> float:
                return (t - al.g0) / al.per

            cols = "  ".join(
                f"{'!' if i in tight else ''}{w}:{beat(o):.2f}->{beat(n):.2f}"
                for i, (w, o, n) in enumerate(zip(p["words"], p["starts"], new))
            )
            print(f"{stamp}  {'〔等比例先驗〕' if mode == 'spread' else ''}{cols}")

    if spread_lines:
        print(
            f"{spread_lines} 行沒有可用的 \\k（或指定了 --prior spread），以音拍等比例當大概位置：結果比有初稿時粗，更要聽過。",
            file=sys.stderr,
        )
    # 自我檢查：歌詞逐詞相同的行（副歌）解出來的節奏應該一樣。不一樣就是證據不夠，兩次都不能信。
    # （副歌第二次常會換一兩個詞，所以條件是：每個詞的音拍形狀都相同，而且過半的詞逐字相同。）
    same: dict[tuple, list[dict]] = {}
    for p in plans:
        if len(p["words"]) > 1:
            shape = tuple(tuple(k for _, k in morae(w, readings)) for w in p["words"])
            same.setdefault(shape, []).append(p)
    unstable: list[str] = []
    for group in same.values():
        first = group[0]
        alike = [
            p for p in group
            if sum(x == y for x, y in zip(p["words"], first["words"])) * 2 > len(first["words"])
        ]
        rhythms = {tuple(round((t - p["new"][0]) / al.q) for t in p["new"]) for p in alike}
        if len(rhythms) > 1:
            unstable.append("／".join(ass_formatted(p["ev"].start) for p in alike))
    if unstable:
        print(
            f"歌詞相同的行解出不同的節奏（{'；'.join(unstable)}）：這幾行的結果不可信。\n"
            "先把其中一次用 spec.py --vocal 對好、寫回並 --pin，再用 ktime.py 把同樣的分段時間套到另一次。",
            file=sys.stderr,
        )
    if phrase_fallback:
        print(
            f"--phrases：{len(phrase_fallback)} 行的重複樂句互相對不齊，已退回逐行對位：{' '.join(phrase_fallback)}",
            file=sys.stderr,
        )
    if squeezed:
        print(
            f"{len(squeezed)} 行有相鄰的詞擠在 1/4 拍以內（報告裡標 ! 的詞）：{' '.join(squeezed)}\n"
            "這些行的結果不可信，不要寫回；改用 spec.py 用眼睛對，或先在 Aegisub 抓個大概再跑。",
            file=sys.stderr,
        )

    if a.write:
        n = 0
        for p in plans:
            if ass_formatted(p["ev"].start) not in a.only:
                continue
            if write_back(p["ev"], p["idx"], p["new"]):
                n += 1
        oped.save(a.target, ass)
        print(f"{a.target.name}: 寫回 {n} 行（記得用耳朵確認，再把確定的詞加進 --pin）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
