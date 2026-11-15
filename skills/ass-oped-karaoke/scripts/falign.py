"""人聲分離＋強制對位：歌詞剛切好分詞、還沒有任何時間時，用這個抓 `\\k` 初稿。

    # 一次性：建一個放 torch／demucs 的獨立環境（約 1 GB，第一次 run 再下載約 1.3 GB 的模型）。
    # 先問過使用者要不要裝；不裝就退回 align.py ＋ spec.py 目視。
    python -X utf8 falign.py setup

    # 報告：每個詞的起點（cs，距行首）與信心；順便產生分離後的人聲 wav
    python -X utf8 falign.py run x.ass --video ncop.mkv --style OPJP --readings readings.json

    # 選用：寫回後接著用 align.py 在分離後的人聲上把每個詞吸到附近的起音
    # （三首實測裡兩首變準、一首變差，要和沒微調的比過才用）
    python -X utf8 falign.py run x.ass --video ncop.mkv --style OPJP --readings readings.json \\
        --write --all --refine --grid 88659:162.96

    # 只寫回指定的幾行（和 align.py 一樣，已經確認過的行不要再被蓋掉）
    python -X utf8 falign.py run x.ass ... --write --only 0:00:46.32 0:00:48.78

做法：demucs 把人聲從伴奏裡分出來 → 歌詞轉成羅馬字 → torchaudio 的 MMS 強制對位模型在每一行
的時間範圍內找每個詞的起點。它知道歌詞是什麼，所以不像 `align.py` 要靠「大概的位置」；
副歌伴奏再滿也有人聲可以對。`--refine` 在寫回之後跑 `align.py --prior current --sigma 0.25`
（音訊來源換成分離後的人聲）：強制對位決定哪個字配哪個音，對位器只在 ±1/4 拍內把它吸到起音上。

**它還是只產生初稿。** 強制對位會把詞放在「某個音的起點」，但那個音不一定就是那個字——
拖長音、轉音、和聲、半唸半唱的句子最容易錯。跑完之後：

* 信心低的詞（報告裡前面有 `?`）錯的機率大約是其他詞的兩倍，優先看；整行信心都低的行當成沒對到。
  但信心高不代表對——高信心的詞也有一成多差超過半拍，所以沒被標的句子一樣要看；
* 逐句疊在**分離後的人聲**頻譜上看（跑完會印出 `spec.py` 的指令；用分離後的 wav 當音訊來源，
  伴奏已經拿掉了，比 `--vocal` 清楚得多）；
* 沒有人聽過就老實說沒聽過。

漢字要有讀音（`--readings`，和 `align.py` 共用同一份表）；讀音表裡沒有的漢字會被略過，
這種詞只剩假名的部分可以對，報告前會列出來。

需要另一個 Python 環境（`setup` 建在 `~/.cache/ass-oped-karaoke/fa-venv`，可用 `--env` 或環境變數
`OPED_FA_ENV` 換位置），因為 torch 不一定支援 aegisub_render 用的 Python 版本。這支本身仍在
原本的環境執行，分離與對位交給 `falign_worker.py`。另外需要 ffmpeg。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align as align_mod  # noqa: E402
import ktime  # noqa: E402
import oped  # noqa: E402
from oped import ass_formatted  # noqa: E402

DEFAULT_ENV = Path.home() / ".cache" / "ass-oped-karaoke" / "fa-venv"
# 低於這個信心的詞在報告裡標 `?`。三首歌、358 個詞對照有人聽過的參考時點：信心低於 0.20 的詞
# 約三成差超過半拍，其餘約一成六——是「比較可能錯」的提示，不是對錯的判定；0.20–0.30 那一段
# 並不比高信心的差，所以門檻不設在 0.30（那樣快歌幾乎每句都會被標）。
LOW = 0.20
KANJI = re.compile(r"[㐀-鿿豈-﫿々〆]")

# --- 假名 → 羅馬字（MMS_FA 的字彙只有 a-z） ------------------------------------------------

_ROWS = {
    "": "あいうえお", "k": "かきくけこ", "g": "がぎぐげご", "s": "さ_すせそ", "z": "ざ_ずぜぞ",
    "t": "た__てと", "d": "だ__でど", "n": "なにぬねの", "h": "はひ_へほ", "b": "ばびぶべぼ",
    "p": "ぱぴぷぺぽ", "m": "まみむめも", "r": "らりるれろ",
}
ROMA = {ch: c + v for c, row in _ROWS.items() for ch, v in zip(row, "aiueo") if ch != "_"}
ROMA.update({
    "し": "shi", "じ": "ji", "ち": "chi", "ぢ": "ji", "つ": "tsu", "づ": "zu", "ふ": "fu",
    "や": "ya", "ゆ": "yu", "よ": "yo", "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n", "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゎ": "wa",
})
SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
PARTICLE = {"は": "wa", "へ": "e", "を": "o"}


def _apply_readings(word: str, readings: dict[str, str]) -> str:
    for k in sorted(readings, key=len, reverse=True):
        word = word.replace(k, readings[k])
    return word


def romaji(word: str, readings: dict[str, str]) -> str:
    """一個詞的羅馬字。漢字先查讀音表；查不到、也不是假名或英文字母的字會被略過。"""
    word = _apply_readings(word, readings)
    kana = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in word)
    kana = "".join(ch for ch in kana if "ぁ" <= ch <= "ゖ" or ch == "ー" or ch.isascii() and ch.isalpha())
    if kana in PARTICLE:
        return PARTICLE[kana]
    out = ""
    for ch in kana:
        if ch in SMALL_Y and out:
            base = out[:-1]  # き+ゃ → kya；し+ゃ → sha
            out = base + SMALL_Y[ch] if base.endswith(("sh", "ch", "j")) else base + "y" + SMALL_Y[ch]
        elif ch in SMALL_V and out and out[-1] in "aiueo":
            out = out[:-1] + SMALL_V[ch]  # ふ+ぁ → fa
        elif ch in "っー":
            continue  # 促音與長音不發新的音，對位時跟著前後的音
        elif ch in ROMA:
            out += ROMA[ch]
        elif ch.isascii():
            out += ch.lower()
    return out


# --- 環境 -------------------------------------------------------------------------------


def env_python(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def cmd_setup(a: argparse.Namespace) -> int:
    env: Path = a.env
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit(
            "需要 uv 來建環境（https://docs.astral.sh/uv/）。或自己建一個 Python 3.10–3.12 的環境，"
            "裝好 torch、torchaudio、demucs、soundfile，再用 --env／OPED_FA_ENV 指過去。"
        )
    py = env_python(env)
    if not py.exists():
        subprocess.run([uv, "venv", "--python", "3.12", str(env)], check=True)
    # CPU 版就夠（一首 OP 分離加對位約一兩分鐘），也小得多，而且不會撞到顯示卡驅動與 CUDA 版本不合
    subprocess.run(
        [uv, "pip", "install", "--python", str(py), "torch==2.5.1", "torchaudio==2.5.1",
         "--index-url", "https://download.pytorch.org/whl/cpu"],
        check=True,
    )
    subprocess.run([uv, "pip", "install", "--python", str(py), "demucs", "soundfile"], check=True)
    subprocess.run([str(py), "-c", "import torch, torchaudio, demucs, soundfile; print('torch', torch.__version__)"], check=True)
    print(f"環境建好了：{env}\n第一次 run 會另外下載 demucs（約 80 MB）與 MMS_FA（約 1.2 GB）的模型。")
    return 0


# --- 對位 -------------------------------------------------------------------------------


def cmd_run(a: argparse.Namespace) -> int:
    if a.write and not (a.only or a.all):
        raise SystemExit("--write 要配 --only <行起點…>（只寫回這幾行）或 --all（整首都還沒抓過時）")
    if a.refine and not (a.write and a.grid):
        raise SystemExit("--refine 要配 --write 與 --grid B0:BPM（它是在寫回之後，對著分離後的人聲把詞吸到起音上）")
    py = env_python(a.env)
    if not py.exists():
        raise SystemExit(
            f"找不到強制對位用的環境：{a.env}\n"
            "先跑一次 `falign.py setup`（會下載約 1 GB 的套件與之後約 1.3 GB 的模型——先問過使用者），"
            "或用 --env／OPED_FA_ENV 指到已經裝好 torch、torchaudio、demucs、soundfile 的環境。"
        )
    readings: dict[str, str] = json.loads(a.readings.read_text(encoding="utf-8")) if a.readings else {}
    ass = oped.load(a.target)
    lines = [e for e in ktime.targets(ass) if not a.style or e.style == a.style]
    if not lines:
        raise SystemExit("沒有符合條件的行（--style 對嗎？來源行要先切好分詞、每段前面有 {\\k}）")
    video = a.video or oped.video_of(ass, a.target)
    if video is None or not Path(video).exists():
        raise SystemExit("需要 --video（或字幕裡要有可用的 Video File）")

    cache: Path = a.cache or (a.target.parent / ".falign-cache")
    cache.mkdir(parents=True, exist_ok=True)
    lo = max(0, min(e.start for e in lines) - 2000)
    hi = max(e.end for e in lines) + 2000
    # 快取檔名帶上影片的指紋：同一個資料夾換了 --video 也不會吃到上一支影片的混音與人聲
    st = Path(video).stat()
    tag = f"{lo}_{hi}_{zlib.crc32(f'{Path(video).resolve()}|{st.st_size}|{st.st_mtime_ns}'.encode()):08x}"
    mix = cache / f"mix_{tag}.wav"
    if not mix.exists():
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{lo / 1000:.3f}", "-t", f"{(hi - lo) / 1000:.3f}",
             "-i", str(video), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(mix)],
            check=True,
        )

    plans, job_lines, missing, unread = [], [], [], []
    for ev in lines:
        words, starts, idx = align_mod.line_words(ev)
        if not words:
            continue
        roms = [romaji(w, readings) for w in words]
        missing += [w for w, r in zip(words, roms) if not r]
        unread += [w for w, r in zip(words, roms) if r and KANJI.search(_apply_readings(w, readings))]
        plans.append({"ev": ev, "words": words, "starts": starts, "idx": idx})
        job_lines.append({"start_ms": ev.start, "end_ms": ev.end, "roms": roms})
    if missing:
        print(
            f"轉不出羅馬字、不參與對位的詞（{len(missing)} 個，漢字要寫進 --readings 的讀音表）："
            f"{' '.join(dict.fromkeys(missing))}",
            file=sys.stderr,
        )
    if unread:
        print(
            f"有漢字查不到讀音、只用了假名部分對位的詞（{len(unread)} 個，補進 --readings）："
            f"{' '.join(dict.fromkeys(unread))}",
            file=sys.stderr,
        )
    job, result = cache / "job.json", cache / "result.json"
    job.write_text(
        json.dumps({"mix": str(mix.resolve()), "offset_ms": lo, "pad_ms": a.pad, "lines": job_lines}, ensure_ascii=False),
        encoding="utf-8",
    )
    worker = Path(__file__).resolve().parent / "falign_worker.py"
    print(f"人聲分離＋強制對位（{len(job_lines)} 行；第一次會下載模型，CPU 上一首 OP 約一兩分鐘）…", file=sys.stderr)
    # demucs 會把進度印在 stdout；全部導到 stderr，stdout 只留下面的報告
    sys.stdout.flush()
    done = subprocess.run([str(py), "-X", "utf8", str(worker), str(job), str(result)], stdout=sys.stderr)
    if done.returncode:
        raise SystemExit(f"falign_worker.py 失敗（exit {done.returncode}）；上面的訊息是它印的")
    res = json.loads(result.read_text(encoding="utf-8"))

    # 分離後的人聲補上前面的空白，時間軸就和影片一樣，可以直接給 spec.py 當音訊來源
    vocals = cache / f"vocals_{tag}.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", res["vocals"], "-af", f"adelay={lo}:all=1",
         "-ac", "1", "-ar", "16000", str(vocals)],
        check=True,
    )

    only = set(a.only)
    weak_lines: list[str] = []
    written: list[str] = []
    for p, r in zip(plans, res["lines"]):
        ev = p["ev"]
        stamp = ass_formatted(ev.start)
        got, scores = r["starts_ms"], r["scores"]
        # 沒對到的詞（轉不出羅馬字、整行失敗）在前後兩個對到的詞之間平均分配；
        # 前面沒有就從行首算，後面沒有就分到行尾
        new: list[float] = []
        for i, t in enumerate(got):
            if t is None:
                j = next((k for k in range(i + 1, len(got)) if got[k] is not None), None)
                prev = new[-1] if new else ev.start
                nxt, steps = (got[j], j - i + 1) if j is not None else (ev.end - 100, len(got) - i + 1)
                t = prev + (nxt - prev) / steps
            new.append(min(max(t, ev.start), ev.end - 100))
        for i in range(1, len(new)):
            new[i] = max(new[i], new[i - 1] + 30)
        known = [s for s in scores if s is not None]
        mean = sum(known) / len(known) if known else 0.0
        low = sum(1 for s in scores if s is None or s < LOW)
        if mean < LOW or low * 3 >= len(scores) or (low and len(scores) <= 3):
            weak_lines.append(stamp)  # 整行都低、三分之一以上的詞低、或短句裡有低的
        cols = "  ".join(
            f"{'?' if s is None or s < LOW else ''}{w}:{round((t - ev.start) / 10)}"
            + ("" if s is None else f"({s:.2f})")
            for w, t, s in zip(p["words"], new, scores)
        )
        print(f"{stamp}  信心 {mean:.2f}  {cols}")
        if a.write and (a.all or stamp in only) and align_mod.write_back(ev, p["idx"], new):
            written.append(stamp)

    if weak_lines:
        print(
            f"{len(weak_lines)} 行有不少信心低於 {LOW:.2f} 或沒對到的詞（報告裡標 ? 的）：{' '.join(weak_lines)}\n"
            "這幾行優先對著分離後的人聲頻譜修，或在回報裡逐行列出來。其他行裡零星標 ? 的詞也先看；"
            "沒被標的不代表對，逐句還是要看過。",
            file=sys.stderr,
        )
    if a.write:
        oped.save(a.target, ass)
        print(f"{a.target.name}: 強制對位寫回 {len(written)} 行")
    if a.refine and written:
        # 音訊來源換成分離後的人聲；快取跟著這次的人聲走，不和對著原混音跑過的 .align-cache 混用
        cmd = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("align.py")), str(a.target),
               "--video", str(vocals), "--grid", a.grid, "--prior", "current", "--sigma", str(a.refine_sigma),
               "--cache", str(cache / f"align_{tag}"), "--write", "--only", *written]
        if a.style:
            cmd += ["--style", a.style]
        if a.readings:
            cmd += ["--readings", str(a.readings)]
        print(f"微調：align.py --prior current --sigma {a.refine_sigma}（音訊＝分離後的人聲）…", file=sys.stderr)
        sys.stdout.flush()  # 先把上面的報告送出去，不然會排在 align.py 的輸出後面
        if subprocess.run(cmd).returncode:
            raise SystemExit("align.py 微調失敗；強制對位的結果已經寫回，可以單獨重跑上面那道 align.py")
    print(
        f"分離後的人聲：{vocals}（時間軸與影片相同）\n逐句檢查用它當 spec.py 的音訊來源，例如：\n"
        f"  python -X utf8 {Path(__file__).with_name('spec.py')} {vocals} <起> <訖> out.png "
        f"--bpm <BPM> --b0 <第一拍秒數> --ass {a.target}" + (f" --style {a.style}" if a.style else ""),
        file=sys.stderr,
    )
    if a.write:
        print("這是初稿：逐句看過頻譜、最好有人聽過，再當成抓完。", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--env", type=Path, default=Path(os.environ.get("OPED_FA_ENV", DEFAULT_ENV)),
        help="放 torch／demucs 的 Python 環境（預設 ~/.cache/ass-oped-karaoke/fa-venv 或 $OPED_FA_ENV）",
    )
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("setup", help="建立強制對位用的環境（約 1 GB；先問過使用者）")
    p = sub.add_parser("run", help="分離人聲並對位")
    p.add_argument("target", type=Path)
    p.add_argument("--video", type=Path, help="音訊來源（省略＝字幕連結的 Video File）")
    p.add_argument("--style", help="只處理這個樣式的行（通常是日文那個）")
    p.add_argument("--readings", type=Path, help="漢字讀音表 JSON（和 align.py 共用同一份）")
    p.add_argument("--pad", type=int, default=400, help="每行前後多聽多少 ms（預設 400）")
    p.add_argument("--only", nargs="*", default=[], help="只寫回這些行（行的開始時間）")
    p.add_argument("--all", action="store_true", help="寫回全部的行（整首都還沒抓過時）")
    p.add_argument("--write", action="store_true", help="寫回 .ass")
    p.add_argument("--refine", action="store_true", help="寫回後用 align.py 在分離後的人聲上微調（要 --grid）")
    p.add_argument("--grid", help="--refine：B0:BPM，例如 88659:162.96（用 bpm.py 量）")
    p.add_argument("--refine-sigma", type=float, default=0.25, help="--refine：位置先驗的寬度（拍，預設 0.25）")
    p.add_argument("--cache", type=Path, help="音訊、分離結果與對位結果的資料夾（預設 .falign-cache）")
    a = ap.parse_args()
    return {"setup": cmd_setup, "run": cmd_run}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
