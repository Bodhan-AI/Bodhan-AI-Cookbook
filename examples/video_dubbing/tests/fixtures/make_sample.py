"""Generate a tiny synthetic test video (no committed media).

Produces a clip with alternating tone bursts (speech-like energy) and silence so
the VAD finds real segments, muxed onto a test video pattern. Used by the e2e
smoke test and for manual mock runs.
"""

from __future__ import annotations

import os
import subprocess

import numpy as np
import soundfile as sf

from bodhan_dub.ffmpeg import ffmpeg_bin


def make_sample(path: str, duration: float = 30.0, sr: int = 16000) -> str:
    """Write a `duration`-second MP4 to `path` and return it."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    t = np.arange(int(duration * sr)) / sr
    audio = np.zeros_like(t, dtype=np.float32)
    # Alternating speech-like bursts (varying pitch) and silence gaps.
    pattern = [(0.5, 3.5, 170), (4.5, 8.0, 220), (9.0, 12.0, 160),
               (13.5, 17.0, 240), (18.0, 22.5, 175), (24.0, 28.5, 210)]
    for start, end, freq in pattern:
        if end > duration:
            break
        a, b = int(start * sr), int(end * sr)
        env = np.hanning(b - a)
        audio[a:b] += (0.3 * env * np.sin(2 * np.pi * freq * t[a:b])).astype(np.float32)

    wav = os.path.splitext(path)[0] + ".wav"
    sf.write(wav, audio, sr, subtype="PCM_16")
    subprocess.run(
        [ffmpeg_bin(), "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={duration}",
         "-i", wav,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.remove(wav)
    return path


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "sample.mp4"
    print(make_sample(out))
