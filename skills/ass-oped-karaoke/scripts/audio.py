"""從影片切出一段音訊（ffmpeg），給 bpm／spec／align 用。結果快取在資料夾裡，可以隨時刪。"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pcm_mono(video: Path, start: float, duration: float, sr: int = 16000):
    """單聲道 float32 波形（用來估拍與畫頻譜）。"""
    import numpy as np

    out = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768


def pcm_stereo(video: Path, start: float, duration: float, sr: int = 16000):
    """立體聲 float32 波形，形狀 (n, 2)（用聲像把置中的人聲從伴奏裡分出來看）。"""
    import numpy as np

    out = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video), "-vn", "-ac", "2", "-ar", str(sr), "-f", "s16le", "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32).reshape(-1, 2) / 32768


def wav_stereo(video: Path, start_ms: int, length_ms: int, cache: Path) -> Path:
    """立體聲 44.1kHz 的 wav（對位器要用聲像判斷人聲），第一次呼叫時才切。"""
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-ss", f"{start_ms / 1000:.3f}", "-t", f"{length_ms / 1000:.3f}", "-i", str(video),
                "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(cache),
            ],
            check=True,
        )
    return cache
