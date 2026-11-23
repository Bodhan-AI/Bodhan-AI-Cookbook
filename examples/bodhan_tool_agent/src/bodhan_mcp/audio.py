import contextlib
import io
import re
import shutil
import subprocess
import wave

# Languages accepted by indic-transcribe (from the Cookbook notebook).
STT_LANGUAGES = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
}


def find_ffmpeg() -> str | None:
    """Use a system ffmpeg if there is one, otherwise the one bundled in imageio-ffmpeg."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def to_wav_16k_mono(src: str, dst: str, ffmpeg: str) -> None:
    """Convert any audio or video file into a plain 16 kHz mono WAV."""
    result = subprocess.run(
        [ffmpeg, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", dst],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-300:])


def find_silences(wav_path: str, ffmpeg: str, noise_db: int = -35, min_len: float = 0.4) -> list[float]:
    """Return the middle point (in seconds) of every quiet stretch."""
    result = subprocess.run(
        [ffmpeg, "-i", wav_path, "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", result.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


def plan_chunks(duration: float, silence_points: list[float],
                max_len: float = 25.0, min_len: float = 8.0) -> list[tuple[float, float]]:
    """Cut into windows of at most max_len seconds, preferring to cut in a silence."""
    cuts = [0.0]
    start = 0.0
    while duration - start > max_len:
        candidates = [p for p in silence_points if start + min_len <= p <= start + max_len]
        cut = max(candidates) if candidates else start + max_len
        cuts.append(cut)
        start = cut
    cuts.append(duration)
    return list(zip(cuts[:-1], cuts[1:]))


def cut_wav(path: str, start: float, end: float) -> bytes:
    """Return the part of a WAV between start and end seconds, as WAV bytes."""
    with contextlib.closing(wave.open(path, "rb")) as source:
        rate = source.getframerate()
        source.setpos(int(start * rate))
        frames = source.readframes(int((end - start) * rate))
        buffer = io.BytesIO()
        with contextlib.closing(wave.open(buffer, "wb")) as out:
            out.setnchannels(source.getnchannels())
            out.setsampwidth(source.getsampwidth())
            out.setframerate(rate)
            out.writeframes(frames)
        return buffer.getvalue()