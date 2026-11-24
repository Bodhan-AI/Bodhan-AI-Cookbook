"""ffmpeg / numpy audio helpers: extraction, silence detection, pitch, clip fitting, loudness, mixing, muxing."""
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .segmentation import parse_silences

SR = 44100


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({r.returncode}): {r.stderr[-500:]}")
    return r


def probe_duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(r.stdout.strip())


def has_audio(path: Path) -> bool:
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
             str(path)])
    return bool(r.stdout.strip())


def extract_audio(video: Path, out: Path, sr: int = 16000, mono: bool = True) -> None:
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1" if mono else "2", "-ar", str(sr), str(out)])


def cut(wav: Path, start: float, end: float, out: Path) -> None:
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(wav), str(out)])


def detect_silences(wav: Path, duration: float, noise_db: float = -35.0, min_dur: float = 0.25):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
                        "-f", "null", "-"], capture_output=True, text=True)
    return parse_silences(r.stderr, duration)


def vad_spans(wav: Path, duration: float, hop_s: float = 0.02):
    """Adaptive energy voice-activity detection on 16 kHz mono audio (no ASR model involved)."""
    from .segmentation import energy_spans
    x, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    hop = int(sr * hop_s)
    n = len(x) // hop
    if n == 0:
        return []
    fr = x[:n * hop].reshape(n, hop)
    db = (20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)).tolist()
    return [(s, min(e, duration)) for s, e in energy_spans(db, hop_s)]


def wav_info(data: bytes) -> tuple[np.ndarray, int]:
    x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    return (x.mean(axis=1) if x.ndim > 1 else x), sr


def wav_duration(data: bytes) -> float:
    x, sr = wav_info(data)
    return len(x) / sr


def median_f0(path: Path, start: float, end: float, max_frames: int = 60):
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    x = x[int(start * sr):int(end * sr)]
    frame, lo, hi = int(0.04 * sr), int(sr / 300), int(sr / 70)
    if len(x) < frame * 2:
        return None
    idx = np.linspace(0, len(x) - frame, min(max_frames, (len(x) - frame) // (frame // 2) + 1)).astype(int)
    vals = []
    for i in idx:
        f = x[i:i + frame] - x[i:i + frame].mean()
        if np.sqrt(np.mean(f ** 2)) < 0.01:
            continue
        ac = np.correlate(f, f, "full")[len(f) - 1:]
        k = int(ac[lo:hi].argmax()) + lo
        if ac[0] > 0 and ac[k] / ac[0] > 0.3:
            vals.append(sr / k)
    return float(np.median(vals)) if vals else None


def assign_speakers(f0s: list, split_hz: float = 165.0, min_sep: float = 35.0) -> list[dict]:
    """Group segments into at most two speakers by median pitch, and label each group male/female.
    Without diarization this is a heuristic; it is logged and can be overridden in voices.yaml."""
    known = [f for f in f0s if f]
    if not known:
        return [{"speaker": "S1", "gender": "female", "f0": None} for _ in f0s]
    lo, hi = min(known), max(known)
    two = hi - lo >= min_sep * 2
    if two:  # 1-D 2-means
        c1, c2 = lo, hi
        for _ in range(20):
            a = [f for f in known if abs(f - c1) <= abs(f - c2)]
            b = [f for f in known if abs(f - c1) > abs(f - c2)]
            c1, c2 = (np.mean(a) if a else c1), (np.mean(b) if b else c2)
        two = abs(c1 - c2) >= min_sep
    out, mid = [], np.mean(known)
    for f in f0s:
        if f is None:
            f = mid
        if two:
            low = abs(f - c1) <= abs(f - c2)
            cen = c1 if low else c2
            out.append({"speaker": "S1" if low else "S2", "gender": "male" if cen < split_hz else "female", "f0": round(f, 1)})
        else:
            out.append({"speaker": "S1", "gender": "male" if mid < split_hz else "female", "f0": round(f, 1)})
    if two and len({o["gender"] for o in out}) == 1:  # both clusters same side of the split: keep distinct voices
        for o in out:
            if o["speaker"] == "S2":
                o["gender"] = "female" if o["gender"] == "male" else "male"
    return out


def fit_clip(data: bytes, stretch: float, out: Path) -> float:
    """Resample a TTS clip to 44.1 kHz mono and time-stretch it (pitch preserved). Returns its duration."""
    tmp = out.with_suffix(".src.wav")
    tmp.write_bytes(data)
    flt = "aresample=%d" % SR
    if abs(stretch - 1.0) > 1e-3:
        flt = f"atempo={stretch:.4f},aresample={SR}"
    run(["ffmpeg", "-y", "-i", str(tmp), "-ac", "1", "-af", flt, str(out)])
    tmp.unlink(missing_ok=True)
    return sf.info(str(out)).duration


def to_16k(src: Path, out: Path) -> None:
    run(["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(out)])


def concat_wavs(parts: list[bytes], gap_s: float = 0.15) -> bytes:
    """Join several TTS clips (same sample rate) with a short pause."""
    arrs, sr0 = [], None
    for p in parts:
        x, sr = wav_info(p)
        sr0 = sr0 or sr
        arrs.append(x)
        arrs.append(np.zeros(int(gap_s * sr0), dtype="float32"))
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(arrs[:-1]) if len(arrs) > 1 else arrs[0], sr0, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def measure_lufs(path: Path) -> float | None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return None
    v = float(json.loads(m.group(0))["input_i"])
    return v if v > -70 else None


def _limit(x: np.ndarray, tmp: Path, ceiling: float = 0.89) -> np.ndarray:
    """Look-ahead limiter (ffmpeg alimiter, no auto level) so peaks stay under about -1 dBFS without scaling the whole track down."""
    src, dst = tmp.with_name(tmp.stem + "_lim_in.wav"), tmp.with_name(tmp.stem + "_lim_out.wav")
    sf.write(str(src), x, SR)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"alimiter=limit={ceiling}:attack=3:release=60:level=0",
                        "-ar", str(SR), str(dst)], capture_output=True, text=True)
    if r.returncode or not dst.exists():
        peak = float(np.abs(x).max()) if len(x) else 0.0
        return x * (ceiling / peak) if peak > ceiling else x
    y, _ = sf.read(str(dst), dtype="float32", always_2d=False)
    y = np.pad(y, (0, max(0, len(x) - len(y))))[:len(x)]
    peak = float(np.abs(y).max()) if len(y) else 0.0
    return y * (ceiling / peak) if peak > ceiling else y


def normalise_speech(dub: np.ndarray, lufs: float | None, tmp: Path, target: float | None = None) -> tuple[np.ndarray, float | None, float]:
    """Bring the dubbed speech to the target loudness (-16 LUFS). Speech synthesis is peaky, so a plain gain would clip: apply the
    gain, limit the peaks, measure again, and correct the gain for what the limiter took away (up to 4 passes)."""
    target = config.LUFS_TARGET if target is None else target
    if lufs is None or not len(dub) or not np.abs(dub).max():
        return dub, lufs, 0.0
    g_db, best, best_l = target - lufs, dub, lufs
    for _ in range(4):
        g_db = min(g_db, 30.0)
        y = _limit(dub * (10 ** (g_db / 20)), tmp)
        sf.write(str(tmp), y, SR)
        got = measure_lufs(tmp)
        if got is None:
            return y, None, round(g_db, 2)
        best, best_l = y, got
        if abs(got - target) <= 0.4:
            break
        g_db += target - got
    return best, best_l, round(g_db, 2)


def render_mix(video: Path, duration: float, gate_spans: list[tuple[float, float]],
               clips: list[dict], out_wav: Path, work: Path) -> dict:
    """Original soundtrack with the English speech gated out, plus the dubbed clips at -16 LUFS.
    clips: [{path, start, max_end}]. Non-speech audio (music, effects) is left in place."""
    n = int(round(duration * SR))
    if has_audio(video):
        bg_path = work / "bg.wav"
        run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", str(SR), str(bg_path)])
        bg, _ = sf.read(str(bg_path), dtype="float32", always_2d=True)
        bg = np.pad(bg, ((0, max(0, n - len(bg))), (0, 0)))[:n]
    else:
        bg = np.zeros((n, 2), dtype="float32")
    gain = np.ones(n, dtype="float32")
    fade = int(0.12 * SR)
    for s, e in gate_spans:
        a, b = max(0, int(s * SR)), min(n, int(e * SR))
        gain[a:b] = 0.0
        f0, f1 = max(0, a - fade), min(n, b + fade)
        if a > f0:
            gain[f0:a] = np.minimum(gain[f0:a], np.linspace(1, 0, a - f0, dtype="float32"))
        if f1 > b:
            gain[b:f1] = np.minimum(gain[b:f1], np.linspace(0, 1, f1 - b, dtype="float32"))
    bg *= gain[:, None]

    dub = np.zeros(n, dtype="float32")
    for c in clips:
        x, _ = sf.read(str(c["path"]), dtype="float32", always_2d=False)
        a = max(0, int(round(c["start"] * SR)))
        b = min(n, a + len(x))
        if c.get("max_end") is not None:
            b = min(b, max(a, int(round(c["max_end"] * SR))))
        x = x[:b - a].copy()
        if len(x) > int(0.06 * SR):
            x[-int(0.06 * SR):] *= np.linspace(1, 0, int(0.06 * SR), dtype="float32")
        dub[a:b] += x
    tmp = work / "dub_only.wav"
    sf.write(str(tmp), dub, SR)
    lufs = measure_lufs(tmp)
    dub, final_lufs, g_db = normalise_speech(dub, lufs, tmp)
    bg += dub[:, None]
    np.clip(bg, -1.0, 1.0, out=bg)
    sf.write(str(out_wav), bg, SR, subtype="PCM_16")
    return {"speech_lufs_before": lufs, "speech_lufs": final_lufs, "gain_db": round(g_db, 2)}


def mux(video: Path, mix_wav: Path, out: Path, duration: float) -> None:
    base = ["ffmpeg", "-y", "-i", str(video), "-i", str(mix_wav), "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration:.3f}"]
    audio = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    try:
        run(base + ["-c:v", "copy"] + audio + [str(out)])
    except RuntimeError:  # video codec not allowed in mp4 (e.g. VP9 from WebM): re-encode
        run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"] + audio + [str(out)])
