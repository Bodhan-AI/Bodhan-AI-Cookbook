"""Audio/video plumbing with ffmpeg + numpy. No speech recognition happens here:
this module only decodes, measures energy, cuts at pauses and encodes."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import errors as E
from .runtime import deadline_left

ASR_RATE = 16_000
TTS_RATE = 24_000
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".amr", ".wma", ".aiff", ".aif"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp", ".flv", ".wmv", ".mpeg", ".mpg"}


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise E.ToolError(E.MEDIA_ERROR, "ffmpeg is not available.", hint="Install ffmpeg (apt install ffmpeg / winget install ffmpeg).") from exc


def _run(args: list[str], *, input_bytes: bytes | None = None, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe. The timeout is capped by the run deadline, and subprocess.run
    kills the child when it expires, so slow or corrupt media cannot outlive a run."""
    left = deadline_left()
    if left is not None:
        if left <= 0:
            raise E.ToolError(E.UPSTREAM_TIMEOUT, "The run's time limit was reached before media processing could start.")
        timeout = min(timeout, left)
    try:
        return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise E.ToolError(E.MEDIA_ERROR, f"ffmpeg did not finish within {timeout:.0f}s.", hint="The file may be corrupt or very long.") from exc


# Media is treated as one self-contained file. The container is identified from its first
# bytes and passed to ffmpeg explicitly, so a playlist/concat/manifest file renamed to .wav
# can never make the decoder open other files (which would bypass the input sandbox).
_PROTOCOLS = ["-protocol_whitelist", "file,pipe"]


def container_format(path: Path) -> str:
    try:
        with path.open("rb") as f:
            h = f.read(200)
    except OSError as exc:
        raise E.ToolError(E.FILE_NOT_FOUND, f"Cannot read {path.name}: {exc}") from exc
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "wav"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "avi"
    if h[4:8] == b"ftyp":
        return "mov"
    if h[:4] == bytes.fromhex("1a45dfa3"):
        return "matroska"
    if h[:4] == b"OggS":
        return "ogg"
    if h[:4] == b"fLaC":
        return "flac"
    if h[:3] == b"ID3":
        return "mp3"
    if len(h) > 1 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        return "aac"
    if len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:
        return "mp3"
    if h[:5] == b"#!AMR":
        return "amr"
    if h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if h[:4] == b".snd":
        return "au"
    if h[:4] == b"caff":
        return "caf"
    if h[:4] == b"wvpk":
        return "wv"
    if h[:3] == b"FLV":
        return "flv"
    if h[:4] == bytes.fromhex("3026b275"):
        return "asf"
    if h[:4] == bytes.fromhex("000001ba"):
        return "mpeg"
    if h[:1] == b"G" and len(h) > 188 and h[188:189] == b"G":
        return "mpegts"
    raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"{path.name} is not a recognised self-contained audio/video file.",
                      hint="Use WAV, MP3, M4A/MP4, OGG/Opus, FLAC, WebM/MKV, AAC, AMR, AIFF, AVI, FLV, WMV or MPEG. Playlists and manifests are not accepted.")


@dataclass
class MediaInfo:
    duration: float | None
    has_audio: bool
    has_video: bool
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None
    container: str | None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def probe(path: Path) -> MediaInfo:
    """Inspect media with ffprobe when available, else by parsing `ffmpeg -i`."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        fmt = container_format(path)
        r = _run([ffprobe, "-v", "error", *_PROTOCOLS, "-f", fmt, "-print_format", "json", "-show_format", "-show_streams", str(path)], timeout=60)
        if r.returncode == 0:
            data = json.loads(r.stdout or b"{}")
            streams = data.get("streams", [])
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
            dur = data.get("format", {}).get("duration")
            return MediaInfo(float(dur) if dur not in (None, "N/A") else None, a is not None, v is not None,
                             a.get("codec_name") if a else None, int(a["sample_rate"]) if a and a.get("sample_rate") else None,
                             a.get("channels") if a else None, data.get("format", {}).get("format_name"))
    r = _run([ffmpeg_bin(), "-hide_banner", *_PROTOCOLS, "-f", container_format(path), "-i", str(path)], timeout=60)
    err = r.stderr.decode("utf-8", "replace")
    if "Invalid data found" in err or "No such file" in err:
        raise E.ToolError(E.MEDIA_ERROR, f"Could not read media file {path.name}.", hint="The file may be corrupt or not audio/video.")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    am = re.search(r"Audio: (\w+)[^\n]*?(\d+) Hz(?:, (mono|stereo))?", err)
    return MediaInfo(dur, am is not None, bool(re.search(r"Video: (?!png|mjpeg)", err)), am.group(1) if am else None,
                     int(am.group(2)) if am else None, {"mono": 1, "stereo": 2}.get(am.group(3) if am else "", None), None)


def decode_pcm(path: Path, rate: int = ASR_RATE) -> np.ndarray:
    """Decode any audio/video file's first audio stream to mono float32 at `rate`."""
    r = _run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *_PROTOCOLS, "-f", container_format(path), "-i", str(path),
              "-vn", "-ac", "1", "-ar", str(rate),
              "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"])
    if r.returncode != 0 or not r.stdout:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no audio stream"]
        raise E.ToolError(E.MEDIA_ERROR, f"Could not decode audio from {path.name}: {msg[0][:200]}",
                          hint="Check that the file contains an audio track.")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise E.ToolError(E.MEDIA_ERROR, f"Unexpected WAV sample width {width}.")
    x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


def frame_energy(x: np.ndarray, rate: int, frame_ms: int = 20) -> np.ndarray:
    n = max(1, int(rate * frame_ms / 1000))
    usable = len(x) - len(x) % n
    if usable <= 0:
        return np.zeros(1, dtype=np.float32)
    frames = x[:usable].reshape(-1, n)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


@dataclass
class Chunk:
    start: float
    end: float
    samples: np.ndarray

    @property
    def duration(self) -> float:
        return self.end - self.start


def chunk_audio(x: np.ndarray, rate: int, *, max_seconds: float = 28.0, min_seconds: float = 2.0,
                frame_ms: int = 20, trim_silence: bool = True) -> list[Chunk]:
    """Cut audio into chunks no longer than `max_seconds`, preferring the quietest
    pause in the last part of each window (energy-based VAD). Leading/trailing
    silence of each chunk is trimmed so we do not pay for it. Chunks that are pure
    silence are dropped. Times are relative to the input."""
    if len(x) == 0:
        return []
    energy = frame_energy(x, rate, frame_ms)
    fps = 1000 / frame_ms
    floor = float(np.percentile(energy, 10))
    peak = float(np.percentile(energy, 95))
    thresh = floor + 0.12 * max(peak - floor, 1e-6)
    voiced = energy > thresh
    total_frames = len(energy)
    max_f, min_f = int(max_seconds * fps), int(min_seconds * fps)
    cuts = [0]
    pos = 0
    while total_frames - pos > max_f:
        lo, hi = pos + min_f, pos + max_f
        window = energy[lo:hi]
        # prefer cuts in the back half of the window, inside a run of unvoiced frames
        smooth = np.convolve(window, np.ones(5) / 5, mode="same")
        weights = np.linspace(1.3, 1.0, len(smooth))
        cut = lo + int(np.argmin(smooth * weights))
        cuts.append(cut)
        pos = cut
    cuts.append(total_frames)
    spf = int(rate * frame_ms / 1000)
    chunks: list[Chunk] = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        seg_voiced = voiced[a:b]
        if not seg_voiced.any():
            continue  # silence only
        if trim_silence:
            idx = np.nonzero(seg_voiced)[0]
            pad = int(0.25 * fps)
            a2, b2 = max(a, a + idx[0] - pad), min(b, a + idx[-1] + 1 + pad)
        else:
            a2, b2 = a, b
        s0, s1 = a2 * spf, min(len(x), b2 * spf)
        if s1 - s0 < int(0.3 * rate):
            continue
        chunks.append(Chunk(s0 / rate, s1 / rate, x[s0:s1]))
    return chunks


def concat_wavs(parts: list[bytes], gap_ms: int = 180) -> tuple[bytes, float]:
    """Join same-rate mono WAV clips with a short pause between them."""
    arrays, rate = [], None
    for p in parts:
        x, r = read_wav(p)
        if rate is None:
            rate = r
        elif r != rate:
            raise E.ToolError(E.MEDIA_ERROR, f"Mixed sample rates in TTS output ({rate} vs {r}).")
        arrays.append(x)
    if rate is None:
        raise E.ToolError(E.EMPTY_RESULT, "No audio segments to join.")
    gap = np.zeros(int(rate * gap_ms / 1000), dtype=np.float32)
    joined = []
    for i, a in enumerate(arrays):
        if i:
            joined.append(gap)
        joined.append(a)
    full = np.concatenate(joined)
    return wav_bytes(full, rate), len(full) / rate


def encode_audio(wav: bytes, dest: Path, fmt: str, title: str, comment: str) -> None:
    """Write WAV directly, or encode MP3 with ffmpeg; tag the file as AI-generated."""
    codec = ["-codec:a", "pcm_s16le"] if fmt == "wav" else ["-codec:a", "libmp3lame", "-b:a", "96k"]
    r = _run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0", *codec, "-metadata", f"title={title}",
              "-metadata", f"comment={comment}", "-metadata", "artist=AI-generated (Bodhan Indic-Speak)", "-f", fmt, str(dest)], input_bytes=wav)
    if r.returncode != 0 and fmt == "wav":
        dest.write_bytes(wav)  # untagged WAV is still valid audio; the label is in the result and report
        return
    if r.returncode != 0:
        raise E.ToolError(E.MEDIA_ERROR, f"MP3 encoding failed: {r.stderr.decode('utf-8', 'replace')[-200:]}",
                          hint="Request format='wav' instead.")
