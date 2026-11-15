"""取幀：把字幕用 libass 燒在影片上，取出指定的幾格，拼成一張對照圖。

    # 指定時間點（秒或 h:mm:ss.cc），拼成 2 欄
    python -X utf8 frames.py out.png --ass x.ass -t 194.6 195.32 --cols 2

    # 指定影格編號（用影片真正的時間碼換算，VFR 也正確）
    python -X utf8 frames.py out.png --ass x.ass --frame 1678 1700 1722

    # 連續張：從 15.0 秒起 3 秒、每秒 4 張
    python -X utf8 frames.py sheet.png --ass x.ass --sheet 15.0 3 4 --cols 4

    # 本篇時間軸的交付檔，直接燒在 NC 影片上看（不必先做 NC 版）
    python -X utf8 frames.py shot.png --ass op_kfx.ass --video ncop.mp4 --shift -1678 --fps 24000/1001 -t 15 59

    # 只看畫面不燒字（量原片的 staff 字幕、找設計依據時）
    python -X utf8 frames.py ref.png --video ep01.mp4 -t 0:03:55.693 --crop 280:260:420:560

    # 可讀性檢查：自動挑出最亮與最暗的一格，燒字拼成一張（交付前必看）
    python -X utf8 frames.py extremes.png --ass op_kfx.ass --video ncop.mp4 --extremes

`--extremes [每秒取樣數]`（預設每秒 1 格）用 ffmpeg 把影片縮成小灰階圖、算每格的平均亮度，
把最亮與最暗那兩格加進要取的時間點（可和 -t 等一起用），時間與亮度（0–255）印在 stdout 與圖的標籤上。
有 `--ass` 時只在「有歌詞正唱到一半」的時間裡挑（卡拉OK來源行的中間一半；檔裡沒有來源行就用
有字幕顯示的區間，都以平移後的時間為準）——否則最暗的多半是片頭片尾的黑場，上面沒有字，等於沒檢查。
亮度是整個畫面的平均，字幕那一帶比較亮或比較暗的片段可能沒被挑到，必要時用 -t 補幾格。

影片預設取字幕檔連結的 Video File（和 Aegisub 一樣相對於字幕檔解析），可用 `--video` 覆蓋。
`--shift` 的語法同 `aegisub-render macro offset`：`[+-]影格數`（要 `--fps` 或能讀時間碼的影片）
或 `[+-][[[hh:]mm:]ss].ms`；平移在記憶體裡做，不會動到你的檔案。

幾個 ffmpeg 的坑（都已經處理掉，改這支之前先看）：`subtitles=` 的 filtergraph 會把磁碟機代號的
冒號當參數分隔，中文與方括號也會出事 → 字幕一律複製到暫存資料夾用純 ASCII 檔名、以 cwd 切進去跑；
`-ss` 之後要 `-copyts`，濾鏡才看得到真正的時間。
"""

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402

from aegisub_render import parse_offset, shift_time_by_frames  # noqa: E402
from aegisub_render.ass.time import clamp_time  # noqa: E402


def seconds(spec: str) -> float:
    """`194.6`、`3:14.5`、`0:03:55.693` 都接受。"""
    return sum(float(x) * 60**i for i, x in enumerate(reversed(spec.split(":"))))


def prepare_ass(ass_path: Path, shift: str | None, fps: str | None, video: Path | None, dest: Path) -> None:
    """把字幕（必要時先平移）寫到暫存資料夾的純 ASCII 檔名。"""
    ass = oped.load(ass_path)
    if shift:
        off = parse_offset(shift)
        if off.by_frames:
            fr = oped.framerate_of(video, fps)
            for ev in ass.events:
                ev.start = shift_time_by_frames(ev.start, off.amount, fr)
                ev.end = shift_time_by_frames(ev.end, off.amount, fr)
        else:
            for ev in ass.events:
                ev.start = clamp_time(ev.start + off.amount)
                ev.end = clamp_time(ev.end + off.amount)
        shown = [ev for ev in ass.events if not ev.comment]
        if shown and all(ev.end <= 0 for ev in shown):
            raise SystemExit(
                f"{ass_path.name}: 平移 {shift} 之後所有字幕都落在 0 秒之前，畫面上不會有任何字。\n"
                "位移的方向或數值不對，或這份字幕不在你以為的那條時間軸上。"
            )
    dest.write_bytes(ass.serialize())


def luma_samples(video: Path, rate: float) -> list[tuple[float, float]]:
    """約每 1/rate 秒取一格，回傳 [(該格真正的時間（秒）, 平均亮度 Y)]。只用 ffmpeg，不需要 numpy。

    用 select 取「距上一個選中的格至少 1/rate 秒」的那一格，時間直接讀那一格的 pts；
    不用 fps 濾鏡，因為它輸出的第 N 格其實是 N 秒後半秒內的某一格，拿 N 去燒字會燒到另一個畫面。
    """
    with tempfile.TemporaryDirectory() as tmp:
        vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{1 / rate:.6f})',"
              "scale=64:36:flags=area,signalstats,"
              "metadata=print:key=lavfi.signalstats.YAVG:file=y.txt")
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-copyts", "-i", str(video), "-an",
               "-vf", vf, "-fps_mode", "passthrough", "-f", "null", "-"]
        subprocess.run(cmd, check=True, cwd=tmp)
        out: list[tuple[float, float]] = []
        t = None
        for ln in (Path(tmp) / "y.txt").read_text(encoding="utf-8").splitlines():
            if "pts_time:" in ln:
                t = float(ln.split("pts_time:")[1].split()[0])
            elif ln.startswith("lavfi.signalstats.YAVG=") and t is not None:
                out.append((math.floor(t * 1000) / 1000, float(ln.split("=")[1])))
        return out


def lyric_windows(ass_path: Path) -> list[tuple[float, float]]:
    """「正唱到一半」的區間（秒）：卡拉OK來源行的中間一半；沒有來源行就用有字幕顯示的區間。"""
    evs = oped.load(ass_path).events
    kara = [e for e in evs if oped.is_karaoke(e)]
    if kara:
        return [(e.start / 1000 + (e.end - e.start) / 4000, e.end / 1000 - (e.end - e.start) / 4000) for e in kara]
    return [(e.start / 1000, e.end / 1000) for e in evs if not e.comment]


def pick_extremes(samples: list[tuple[float, float]], windows: list[tuple[float, float]] | None):
    """回傳 ((最亮秒, 亮度), (最暗秒, 亮度))；windows 給了就只在裡面挑。"""
    pool = samples
    if windows is not None:
        pool = [(t, y) for t, y in samples if any(s <= t <= e for s, e in windows)]
        if not pool:
            print("警告：取樣點沒有一個落在歌詞正在唱的區間裡，改從全片挑（檢查 --shift）", file=sys.stderr)
            pool = samples
    if not pool:
        raise SystemExit("讀不到任何影格，無法挑最亮／最暗")
    return max(pool, key=lambda p: p[1]), min(pool, key=lambda p: p[1])


def frame_seconds(video: Path, fps: str | None) -> float:
    """一格的長度（秒）。讀不到幀率時用 0.05（比 24fps 的一格略長，判斷只會更寬鬆）。

    只是「沒有字幕」提示的容許誤差，所以讀不到時間碼（`framerate_of` 以 SystemExit 回報：
    沒裝 PyAV、影片打不開、時間戳不足）就退回預設值；其他例外是程式的錯，照常拋出。
    """
    try:
        fr = oped.framerate_of(video, fps)
    except SystemExit:
        return 0.05
    return (fr.time_at_frame(1, "start") - fr.time_at_frame(0, "start")) / 1000 or 0.05


def grab(video: Path, times: list[float], work: Path, *, sub: str | None, crop: str | None, scale: float) -> list[Path]:
    out: list[Path] = []
    for i, t in enumerate(times):
        name = f"f{i:03d}.png"
        vf = []
        if sub:
            vf.append(f"subtitles={sub}")
        if crop:
            x, y, w, h = crop.split(":")
            vf.append(f"crop={w}:{h}:{x}:{y}")
        if scale != 1:
            vf.append(f"scale=iw*{scale}:-1")
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.3f}", "-copyts", "-i", str(video)]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += ["-frames:v", "1", name]
        subprocess.run(cmd, check=True, cwd=work)
        out.append(work / name)
    return out


def tile(images: list[Path], out: Path, cols: int, labels: list[str]) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        if len(images) == 1:
            shutil.copyfile(images[0], out)
            return
        raise SystemExit("拼圖需要 Pillow（pip install pillow）；一次只取一格則不需要") from None
    ims = []
    for p, label in zip(images, labels):
        im = Image.open(p).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 8 * len(label) + 8, 18], fill=(0, 0, 0))
        d.text((4, 3), label, fill=(255, 255, 0))
        ims.append(im)
    cols = max(1, min(cols, len(ims)))
    rows = (len(ims) + cols - 1) // cols
    w, h = ims[0].size
    sheet = Image.new("RGB", (w * cols, h * rows))
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--ass", type=Path, help="要燒上去的字幕（省略＝只取畫面）")
    ap.add_argument("--video", type=Path, help="影片（省略＝字幕連結的 Video File）")
    ap.add_argument("-t", "--times", nargs="+", default=[], help="時間點（秒或 h:mm:ss.cc）")
    ap.add_argument("--frame", nargs="+", type=int, default=[], help="影格編號（用影片時間碼換算）")
    ap.add_argument("--sheet", nargs=3, metavar=("START", "DUR", "FPS"), help="連續張：起點、長度(秒)、每秒張數")
    ap.add_argument("--extremes", nargs="?", type=float, const=1.0, metavar="RATE",
                    help="加上全片最亮與最暗的一格（每秒取樣 RATE 格，預設 1；有 --ass 時只在唱到一半的區間挑）")
    ap.add_argument("--shift", help="先平移字幕：[+-]影格數 或 [+-][[[hh:]mm:]ss].ms")
    ap.add_argument("--fps", help="影格換算用的 fps（24000/1001）")
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--crop", help="x:y:w:h")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--font-dir", type=Path, help="額外字型目錄（libass 用）")
    a = ap.parse_args()

    video = a.video
    if video is None:
        if not a.ass:
            ap.error("需要 --video 或 --ass（從字幕的 Video File 取）")
        video = oped.video_of(oped.load(a.ass), a.ass)
        if video is None:
            ap.error("字幕裡沒有可用的 Video File，請用 --video")
    if not video.exists():
        ap.error(f"找不到影片：{video}")

    times: list[float] = [seconds(t) for t in a.times]
    labels: list[str] = [f"{t:.3f}s" for t in times]
    if a.frame:
        fr = oped.framerate_of(video, a.fps)
        for n in a.frame:
            ms = fr.time_at_frame(n, "start")
            times.append(ms / 1000)
            labels.append(f"#{n} {ms / 1000:.3f}s")
    if a.sheet:
        start, dur, fps = seconds(a.sheet[0]), float(a.sheet[1]), float(a.sheet[2])
        n = max(1, round(dur * fps))
        for i in range(n):
            t = start + i / fps
            times.append(t)
            labels.append(f"{t:.3f}s")
    if not times and a.extremes is None:
        ap.error("需要 -t／--frame／--sheet／--extremes 其中之一")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sub = None
        if a.ass:
            prepare_ass(a.ass, a.shift, a.fps, video, work / "s.ass")
            sub = "s.ass"
        if a.extremes is not None:
            windows = lyric_windows(work / "s.ass") if a.ass else None
            (tb, yb), (td, yd) = pick_extremes(luma_samples(video.resolve(), a.extremes), windows)
            print(f"最亮 {tb:.3f}s（平均亮度 {yb:.0f}）  最暗 {td:.3f}s（平均亮度 {yd:.0f}）")
            times += [tb, td]
            labels += [f"bright Y={yb:.0f} {tb:.3f}s", f"dark Y={yd:.0f} {td:.3f}s"]
        if a.ass:
            # 啟發式：取的每一格看起來都沒有字幕，多半是時間軸沒對上（位移方向打反、-t 給成另一條時間軸的秒數）。
            # 真正被燒的是 ffmpeg 在 t 之後選到的那一格，不一定剛好是 t，所以起訖各放寬一格：
            # 邊界上寧可漏報，也不要對「圖上明明有字」的那一格誤報。
            shown = [(e.start / 1000, e.end / 1000) for e in oped.load(work / "s.ass").events if not e.comment]
            fd = frame_seconds(video, a.fps)
            if shown and not any(s - fd <= t < e + fd for t in times for s, e in shown):
                print(
                    f"注意：取的 {len(times)} 格看起來都不在字幕顯示的時間內（字幕在 {min(s for s, _ in shown):.2f}–"
                    f"{max(e for _, e in shown):.2f} 秒），圖上可能沒有字。"
                    "如果是這樣，檢查 --shift 的正負號，以及 -t 是不是影片自己的時間。",
                    file=sys.stderr,
                )
            if a.font_dir:
                sub += ":fontsdir=" + str(a.font_dir.resolve()).replace("\\", "/").replace(":", r"\:")
        images = grab(video.resolve(), times, work, sub=sub, crop=a.crop, scale=a.scale)
        tile(images, a.out.resolve(), a.cols, labels)
    print(f"寫出 {a.out}（{len(times)} 格）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
