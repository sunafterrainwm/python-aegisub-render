"""falign.py 的後半段：人聲分離（demucs）＋已知歌詞的強制對位（torchaudio MMS_FA）。

**不要直接跑這支**，由 `falign.py run` 用另一個 Python 環境呼叫（torch／demucs 裝在那裡，
見 `falign.py setup`）。這支因此不 import aegisub_render 與這個 skill 的其他模組，
只吃 `job.json`、吐 `result.json`：

    job    = {"mix": "<立體聲 wav>", "offset_ms": 0, "pad_ms": 400,
              "lines": [{"start_ms": 360, "end_ms": 2320, "roms": ["ari", "fureta", "mainichi"]}, ...]}
    result = {"vocals": "<分離出來的人聲 wav>",
              "lines": [{"starts_ms": [460, 820, 1470], "scores": [0.71, 0.55, 0.62]}, ...]}

`roms` 裡是空字串的詞（轉不出羅馬字）不參與對位，`starts_ms`／`scores` 對應位置是 null；
整行對位失敗（行太短、塞不下那麼多音）時，那一行全部是 null。進度印在 stderr。
"""

import json
import subprocess
import sys
from pathlib import Path


def separate(mix: Path, cache: Path) -> Path:
    """demucs 兩軌分離，回傳人聲 wav（已經分過就直接用）。一律用 CPU：一首 OP 一兩分鐘就分完，
    而顯示卡的 CUDA 版本和 torch 對不上時會直接失敗。"""
    out = cache / "htdemucs" / mix.stem / "vocals.wav"
    if not out.exists():
        subprocess.run(
            [sys.executable, "-m", "demucs", "-d", "cpu", "--two-stems", "vocals", "-n", "htdemucs",
             "-o", str(cache), str(mix)],
            check=True,
        )
    if not out.exists():
        raise SystemExit(f"demucs 沒有產生 {out}")
    return out


def main() -> int:
    import soundfile as sf
    import torch
    import torchaudio

    job_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    cache = job_path.parent
    vocals = separate(Path(job["mix"]), cache)

    wav, sr = sf.read(str(vocals), dtype="float32", always_2d=True)
    mono = torch.from_numpy(wav.mean(axis=1))
    if sr != 16000:
        mono = torchaudio.functional.resample(mono, sr, 16000)
    sr = 16000

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=True)
    dic = bundle.get_dict(star="*")
    offset = job.get("offset_ms", 0) / 1000
    pad = job.get("pad_ms", 400) / 1000

    lines = []
    for ln in job["lines"]:
        roms = ln["roms"]
        a = max(0, int((ln["start_ms"] / 1000 - offset - pad) * sr))
        b = min(len(mono), int((ln["end_ms"] / 1000 - offset + pad) * sr))
        seg = mono[a:b].unsqueeze(0)
        # 前後各一個 star token 吃掉非歌詞的部分（前奏的尾巴、下一句的開頭）
        tokens, owner = [dic["*"]], [-1]
        for wi, r in enumerate(roms):
            for ch in r:
                if ch in dic:
                    tokens.append(dic[ch])
                    owner.append(wi)
        tokens.append(dic["*"])
        owner.append(-1)
        starts: list[float | None] = [None] * len(roms)
        scores: list[float | None] = [None] * len(roms)
        spans = []
        if len(tokens) > 2 and seg.shape[1] > sr // 10:
            with torch.inference_mode():
                em, _ = model(seg)
                try:
                    ali, sc = torchaudio.functional.forced_align(em, torch.tensor([tokens]), blank=0)
                    spans = torchaudio.functional.merge_tokens(ali[0], sc[0].exp())
                except RuntimeError as e:  # 行太短、塞不下這麼多音：整行當成沒對到
                    print(f"  {ln['start_ms'] / 1000:8.2f}s  對位失敗：{e}", file=sys.stderr, flush=True)
            if len(spans) != len(tokens):  # 每個 token 應該剛好一段；對不上就不要猜是哪個詞
                spans = []
        if spans:
            ratio = seg.shape[1] / em.shape[1] / sr
            acc: dict[int, list[float]] = {}
            for sp, ow in zip(spans, owner):
                if ow < 0:
                    continue
                if starts[ow] is None:
                    starts[ow] = round((a / sr + sp.start * ratio + offset) * 1000)
                acc.setdefault(ow, []).append(float(sp.score))
            for ow, vals in acc.items():
                scores[ow] = round(sum(vals) / len(vals), 3)
        lines.append({"starts_ms": starts, "scores": scores})
        print(f"  {ln['start_ms'] / 1000:8.2f}s  " + " ".join(
            f"{r or '-'}@{'-' if s is None else f'{s / 1000:.2f}'}" for r, s in zip(roms, starts)
        ), file=sys.stderr, flush=True)

    result_path.write_text(
        json.dumps({"vocals": str(vocals), "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
