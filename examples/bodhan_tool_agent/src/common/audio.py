"""Audio Engine and Utilities for Bodhan Indic Models.

Includes:
1. 4-Tier Lightweight Audio Architecture:
   - Tier 1: Zero-dependency stdlib `wave` + `numpy` for PCM WAV fast-path.
   - Tier 2: Dynamic in-process `av` (PyAV) if installed, for 100% universal in-memory decoding.
   - Tier 3: In-process `soundfile` (`libsndfile`) for WAV, FLAC, OGG, MP3 (bypasses M4A/AAC).
   - Tier 4: Auto-discovered bundled FFmpeg (`imageio_ffmpeg`) or system FFmpeg with safe subprocess stdio.
2. Indic sentence tokenizer for TTS splitting (handling Danda U+0964, Double Danda U+0965, Khatam U+06D4).
3. Silence-aware audio chunker for ASR 30-second windows with zero-crossing snapping.
4. Subtitle generation formatters (SRT and WebVTT).
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload
import numpy as np


def _resolve_tool_path(name: str) -> str:
    """Find command executable, proactively checking User/Machine PATH if not in current process PATH."""
    found = shutil.which(name)
    if found:
        return found
    # Check if PATH in registry (Windows) has it
    if os.name == "nt":
        for scope in ("User", "Machine"):
            try:
                import winreg

                key_path = (
                    r"Environment"
                    if scope == "User"
                    else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                )
                root_hkey = winreg.HKEY_CURRENT_USER if scope == "User" else winreg.HKEY_LOCAL_MACHINE
                with winreg.OpenKey(root_hkey, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    for entry in val.split(os.pathsep):
                        if entry.strip():
                            try:
                                candidate = Path(entry.strip()) / f"{name}.exe"
                                if candidate.is_file():
                                    return str(candidate)
                            except (OSError, PermissionError):
                                continue
            except Exception:
                pass
    return name


def _resolve_ffmpeg_cmd() -> str:
    """Resolve ffmpeg binary path: bundled imageio_ffmpeg, current PATH, or Windows registry."""
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except (ImportError, Exception):
        pass
    return _resolve_tool_path("ffmpeg")


def _resolve_ffprobe_cmd() -> str:
    """Resolve ffprobe binary path: system PATH or Windows registry."""
    return _resolve_tool_path("ffprobe")


def _get_subprocess_flags() -> dict[str, int]:
    """Return platform-specific subprocess creation flags to prevent GUI/console window spawning."""
    flags: dict[str, int] = {}
    if os.name == "nt":
        # 0x08000000 = CREATE_NO_WINDOW
        flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags


# Indic-aware sentence boundary pattern (including Danda '।' and Double Danda '॥' and Urdu Khatam '۔')
INDIC_SENTENCE_REGEX = re.compile(
    r'([.!?。！？؟۔।॥]+[\"”\'’\)\]]*)'
)

# Common honorific abbreviations and titles that should not trigger sentence boundaries
KNOWN_HONORIFICS = (
    "डॉ.", "पं.", "श्री.", "श्रीमती.", "प्रो.", "सुश्री.", "ले.", "कै.", "इंजी.", "न्या.",
    "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Sr.", "Jr.", "vs.", "etc.", "e.g.", "i.e."
)
_PLACEHOLDER_DOT = "\u2024"  # Unicode one-dot leader


@dataclass(frozen=True)
class AudioMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int
    num_frames: int
    warnings: tuple[str, ...] = ()
    backend: str = "wave"

    @property
    def is_24khz_mono(self) -> bool:
        return self.sample_rate == 24000 and self.channels == 1

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


@dataclass(frozen=True)
class AudioChunkSegment:
    path: Path
    start_sec: float
    end_sec: float


def _generate_audio_warnings(
    path: Path,
    sr: int,
    ch: int,
    sw: int,
    duration: float,
    backend: str,
) -> tuple[str, ...]:
    """Generate diagnostic and container warnings for Bodhan models."""
    diag_warnings: list[str] = []
    suffix = path.suffix.lower()

    # Container warning (backward compatible string)
    if suffix != ".wav":
        diag_warnings.append(
            f"Compressed/non-WAV container detected ({path.suffix}). Converted via ffmpeg for processing."
        )
        # Explicit reliability warnings for M4A/AAC containers
        if suffix in {".m4a", ".aac"}:
            if backend == "av":
                diag_warnings.append(
                    "M4A/AAC container detected: libsndfile unsupported; decoded in-process via PyAV."
                )
            else:
                diag_warnings.append(
                    "M4A/AAC container detected: libsndfile unsupported; bypassed libsndfile and used FFmpeg subprocess."
                )

    if ch > 1:
        diag_warnings.append(
            f"Multi-channel audio detected ({ch} channels). Bodhan Indic models expect mono (1 channel). Downmixing required."
        )
    if sr == 8000:
        diag_warnings.append(
            "Telephony 8kHz sample rate detected. Low acoustic bandwidth may degrade Indic transcription accuracy; 16kHz or 24kHz recommended."
        )
    elif sr == 48000:
        diag_warnings.append(
            "48kHz high-definition sample rate detected. Exceeds standard 16kHz/24kHz rate for Bodhan models; downsampling recommended to conserve payload bandwidth."
        )
    elif sr not in {16000, 24000}:
        diag_warnings.append(
            f"Sample rate {sr}Hz differs from recommended 16kHz (Indic-Transcribe) or 24kHz (Indic-Speak)."
        )
    if sw != 2:
        diag_warnings.append(
            f"Sample width is {sw * 8}-bit ({sw} bytes). Bodhan Indic models require 16-bit signed PCM (PCM16)."
        )
    if duration > 30.0:
        diag_warnings.append(
            f"Duration {duration:.2f}s exceeds Indic-Transcribe 30.0s chunking limit. Silence-aware slicing required."
        )

    return tuple(diag_warnings)


def _ffprobe_duration(file_path: Path) -> float:
    """Get audio duration in seconds using tiered strategy:
    1. In-process soundfile.info for non-M4A formats (0ms overhead).
    2. ffprobe CLI with safe Windows stdio.
    3. ffmpeg CLI duration parsing as fallback.
    """
    path = Path(file_path).resolve()
    suffix = path.suffix.lower()

    # Step 1: In-process soundfile for non-M4A formats
    if suffix not in {".m4a", ".aac"}:
        try:
            import soundfile as sf  # type: ignore[import-untyped]

            info = sf.info(str(path))
            if info.duration and info.duration > 0.0:
                return float(info.duration)
        except (ImportError, Exception):
            pass

    # Step 2: ffprobe subprocess with safe stdio
    extra_flags = _get_subprocess_flags()
    ffprobe_cmd = _resolve_ffprobe_cmd()
    try:
        result = subprocess.run(
            [
                ffprobe_cmd, "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            **extra_flags,
        )
        if result.returncode == 0 and result.stdout.strip():
            dur = float(result.stdout.strip())
            if dur > 0.0:
                return dur
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass

    # Step 3: ffmpeg duration parsing fallback from stderr
    try:
        ffmpeg_cmd = _resolve_ffmpeg_cmd()
        result = subprocess.run(
            [ffmpeg_cmd, "-hide_banner", "-i", str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            **extra_flags,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if m:
            h, m_val, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return h * 3600.0 + m_val * 60.0 + s
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass

    return 0.0


def resample_pcm16(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample a 1D int16 PCM audio array using linear interpolation (pure NumPy, zero external binary)."""
    if orig_sr <= 0 or target_sr <= 0:
        raise ValueError(f"Sample rates must be positive integers, got orig_sr={orig_sr}, target_sr={target_sr}")
    if orig_sr == target_sr or len(audio) == 0:
        return audio
    num_orig = len(audio)
    num_target = int(round(num_orig * target_sr / orig_sr))
    if num_target == 0:
        return np.empty(0, dtype=np.int16)
    orig_indices = np.linspace(0, num_orig - 1, num_orig)
    target_indices = np.linspace(0, num_orig - 1, num_target)
    interp = np.interp(target_indices, orig_indices, audio.astype(np.float32))
    return np.clip(interp, -32768, 32767).astype(np.int16)


def load_audio_pcm16(
    file_path: str | Path,
    target_sr: int = 16000,
) -> tuple[np.ndarray, int]:
    """Load any audio file into a 1D int16 NumPy array (mono PCM16) at target_sr.

    Implements 4-tier lightweight audio architecture:
    - Tier 1: Zero-dependency stdlib `wave` + `numpy` for PCM WAV fast-path.
    - Tier 2: Dynamic in-process `av` (PyAV) if installed, for 100% in-memory decoding.
    - Tier 3: In-process `soundfile` (`libsndfile`) for WAV, FLAC, OGG, MP3 (skips M4A/AAC).
    - Tier 4: Auto-discovered bundled FFmpeg (`imageio_ffmpeg`) or system FFmpeg with safe subprocess stdio.

    Args:
        file_path: Path to the audio file.
        target_sr: Target sample rate in Hz (default 16000).

    Returns:
        tuple[np.ndarray, int]: (mono int16 numpy array, target_sr).

    Raises:
        FileNotFoundError: If the audio file does not exist.
        ValueError: If audio cannot be decoded with any available backend.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Audio file is empty (0 bytes): {path.name}")

    suffix = path.suffix.lower()

    # --- Tier 1: Zero-dependency stdlib wave + numpy for PCM WAV ---
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                frames = wf.getnframes()
                raw_bytes = wf.readframes(frames)

            if sw == 2:
                audio = np.frombuffer(raw_bytes, dtype="<i2")
            elif sw == 1:
                raw_u8 = np.frombuffer(raw_bytes, dtype=np.uint8)
                audio = ((raw_u8.astype(np.int32) - 128) * 256).astype(np.int16)
            else:
                raise ValueError(f"Unsupported sample width for stdlib wave: {sw * 8}-bit")

            if ch > 1:
                audio = audio.reshape(-1, ch).mean(axis=1).astype(np.int16)

            if sr != target_sr:
                audio = resample_pcm16(audio, sr, target_sr)

            return audio, target_sr
        except (wave.Error, EOFError, ValueError):
            pass  # Fall through to subsequent tiers (e.g., 24-bit/32-bit float WAV)

    # --- Tier 2: Dynamic in-process av (PyAV) ---
    try:
        import av  # type: ignore[import-untyped]

        with av.open(str(path)) as container:
            audio_streams = [s for s in container.streams if s.type == "audio"]
            if audio_streams:
                stream = audio_streams[0]
                resampler = av.AudioResampler(format="s16", layout="mono", rate=target_sr)
                frames_list: list[np.ndarray] = []
                for frame in container.decode(stream):
                    for resampled in resampler.resample(frame):
                        frames_list.append(resampled.to_ndarray())
                for resampled in resampler.resample(None):
                    frames_list.append(resampled.to_ndarray())

                if frames_list:
                    audio = np.concatenate([f.flatten() for f in frames_list]).astype(np.int16)
                    return audio, target_sr
    except (ImportError, Exception):
        pass

    # --- Tier 3: In-process soundfile (libsndfile) for WAV/FLAC/OGG/MP3 ---
    # Explicitly avoid Tier 3 for M4A/AAC as libsndfile does not support it
    if suffix not in {".m4a", ".aac"}:
        try:
            import soundfile as sf  # type: ignore[import-untyped]

            data, sr = sf.read(str(path), dtype="int16", always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1).astype(np.int16)
            if sr != target_sr:
                data = resample_pcm16(data, sr, target_sr)
            return data, target_sr
        except (ImportError, Exception):
            pass

    # --- Tier 4: FFmpeg pipe with strictly safe Windows stdio ---
    ffmpeg_cmd = _resolve_ffmpeg_cmd()
    extra_flags = _get_subprocess_flags()
    try:
        result = subprocess.run(
            [
                ffmpeg_cmd,
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(path),
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(target_sr),
                "-ac", "1",
                "pipe:1",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            **extra_flags,
        )
        if result.returncode == 0 and result.stdout:
            audio = np.frombuffer(result.stdout, dtype="<i2")
            return audio, target_sr
        if result.returncode != 0:
            err_msg = result.stderr.decode("utf-8", errors="replace")[:300]
            raise ValueError(f"ffmpeg conversion failed for {path.name}: {err_msg}")
    except FileNotFoundError:
        raise ValueError(
            "ffmpeg not found on PATH or bundled binaries. Install ffmpeg to process non-WAV audio. "
            "See: https://ffmpeg.org/download.html"
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"ffmpeg conversion timed out for {path.name}")

    raise ValueError(f"Failed to decode audio file {path.name} with any available backend.")


def ensure_wav(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    target_sr: int = 16000,
) -> Path:
    """Convert any audio file to 16-bit mono PCM WAV using tiered backends.

    If the input is already a valid PCM WAV with target sample rate and mono channel,
    returns it unchanged. Non-WAV or non-conforming formats are loaded via the 4-tier
    lightweight audio architecture and exported as standard PCM16 mono WAV.

    Args:
        input_path: Path to the source audio file.
        output_dir: Directory for the converted WAV (defaults to input's parent).
        target_sr: Target sample rate in Hz (default 16000 for Indic-Transcribe).

    Returns:
        Path to a guaranteed-PCM16 WAV file.

    Raises:
        FileNotFoundError: If input file does not exist.
        ValueError: If audio conversion fails.
    """
    path = Path(input_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Audio file is empty (0 bytes): {path.name}")

    # Already target PCM16 mono WAV — verify header and return as-is
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                if (
                    wf.getnchannels() == 1
                    and wf.getsampwidth() == 2
                    and wf.getframerate() == target_sr
                ):
                    return path
        except (wave.Error, EOFError):
            pass  # Corrupted or non-standard WAV header — fall through to reconversion

    out_dir = Path(output_dir) if output_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}_converted.wav"
    if out_path.resolve() == path.resolve():
        out_path = out_dir / f"{path.stem}_pcm16.wav"

    audio, sr = load_audio_pcm16(path, target_sr=target_sr)
    if len(audio) == 0:
        raise ValueError(f"ffmpeg produced empty output for {path.name}")

    with wave.open(str(out_path), "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(target_sr)
        out_wf.writeframes(audio.tobytes())

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ValueError(f"ffmpeg produced empty output for {path.name}")

    return out_path


# Currency terminology across major Indic languages
_CURRENCY_EXPANSIONS: dict[str, dict[str, str]] = {
    "hi": {
        "inr": "रुपये",
        "usd": "डॉलर",
        "eur": "यूरो",
        "percent": "प्रतिशत",
    },
    "ta": {
        "inr": "ரூபாய்",
        "usd": "டாலர்",
        "eur": "யூரோ",
        "percent": "சதவீதம்",
    },
    "mr": {
        "inr": "रुपये",
        "usd": "डॉलर",
        "eur": "युरो",
        "percent": "टक्के",
    },
    "bn": {
        "inr": "টাকা",
        "usd": "ডলার",
        "eur": "ইউরো",
        "percent": "শতাংশ",
    },
    "te": {
        "inr": "రూపాయలు",
        "usd": "డాలర్లు",
        "eur": "యూరో",
        "percent": "శాతం",
    },
    "kn": {
        "inr": "ರೂಪಾಯಿ",
        "usd": "ಡಾಲರ್",
        "eur": "ಯುರೋ",
        "percent": "ಪ್ರತಿಶತ",
    },
    "gu": {
        "inr": "રૂપિયા",
        "usd": "ડોલર",
        "eur": "યુરો",
        "percent": "ટકા",
    },
    "ml": {
        "inr": "രൂപ",
        "usd": "ഡോളർ",
        "eur": "യൂറോ",
        "percent": "ശതമാനം",
    },
    "pa": {
        "inr": "ਰੁਪਏ",
        "usd": "ਡਾਲਰ",
        "eur": "ਯੂਰੋ",
        "percent": "ਪ੍ਰਤੀਸ਼ਤ",
    },
    "en": {
        "inr": "rupees",
        "usd": "dollars",
        "eur": "euros",
        "percent": "percent",
    },
}

_MONTH_NAMES_INDIC: dict[str, dict[int, str]] = {
    "hi": {
        1: "जनवरी", 2: "फ़रवरी", 3: "मार्च", 4: "अप्रैल", 5: "मई", 6: "जून",
        7: "जुलाई", 8: "अगस्त", 9: "सितंबर", 10: "अक्टूबर", 11: "नवंबर", 12: "दिसंबर"
    },
    "ta": {
        1: "ஜனவரி", 2: "பிப்ரவரி", 3: "மார்ச்", 4: "ஏப்ரல்", 5: "மே", 6: "ஜூன்",
        7: "ஜூலை", 8: "ஆகஸ்ட்", 9: "செப்டம்பர்", 10: "அக்டோபர்", 11: "நவம்பர்", 12: "டிசம்பர்"
    },
    "mr": {
        1: "जानेवारी", 2: "फेब्रुवारी", 3: "मार्च", 4: "एप्रिल", 5: "मे", 6: "जून",
        7: "जुलै", 8: "ऑगस्ट", 9: "सप्टेंबर", 10: "ऑक्टोबर", 11: "नोव्हेंबर", 12: "डिसेंबर"
    },
    "bn": {
        1: "জানুয়ারি", 2: "ফেব্রুয়ারি", 3: "মার্চ", 4: "এপ্রিল", 5: "মে", 6: "জুন",
        7: "জুলাই", 8: "আগস্ট", 9: "সেপ্টেম্বর", 10: "অক্টোবর", 11: "নভেম্বর", 12: "ডিসেম্বর"
    },
    "en": {
        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
        7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
    },
}


def normalize_indic_speech_text(text: str, language: str = "hi") -> str:
    """Pre-process and normalize Indian currency symbols, percentages, and dates into spoken words before TTS.

    Expands:
    - Currency: ₹500, Rs. 500, INR 500 -> '500 रुपये' (Hindi), '500 ரூபாய்' (Tamil), '500 rupees' (English).
    - USD / EUR: $50 -> '50 डॉलर', €50 -> '50 यूरो'.
    - Percentages: 25% -> '25 प्रतिशत', '25 சதவீதம்'.
    - Dates: 15/08/2026 -> '15 अगस्त 2026' (Hindi) or '15 ஆகஸ்ட் 2026' (Tamil).
    - Large commas: '1,50,000' or '1,500' -> '150000' / '1500' (prevents TTS pausing on Indian digit commas).
    """
    if not text:
        return text

    lang = language.split("-")[0].lower() if language else "hi"
    exp = _CURRENCY_EXPANSIONS.get(lang, _CURRENCY_EXPANSIONS["hi"])

    normalized = text

    # 1. Clean digit grouping commas (e.g. 1,50,000 -> 150000, 2,500 -> 2500) so TTS doesn't stumble or pause
    normalized = re.sub(r'(?<=\d),(?=\d)', '', normalized)

    # 2. Currency: ₹ / Rs. / INR preceding digits (e.g. ₹500, Rs. 1500, ₹ 20)
    inr_word = exp["inr"]
    normalized = re.sub(
        r'(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?)',
        rf'\1 {inr_word}',
        normalized,
        flags=re.IGNORECASE,
    )
    # Currency trailing: 500₹ or 500/-
    normalized = re.sub(r'(\d+(?:\.\d+)?)\s*(?:₹|/-)', rf'\1 {inr_word}', normalized)

    # 3. Dollar ($) and Euro (€)
    usd_word = exp["usd"]
    normalized = re.sub(r'\$\s*(\d+(?:\.\d+)?)', rf'\1 {usd_word}', normalized)
    eur_word = exp["eur"]
    normalized = re.sub(r'€\s*(\d+(?:\.\d+)?)', rf'\1 {eur_word}', normalized)

    # 4. Percent (%)
    pct_word = exp["percent"]
    normalized = re.sub(r'(\d+(?:\.\d+)?)\s*%', rf'\1 {pct_word}', normalized)

    # 5. Date expansion (DD/MM/YYYY or DD-MM-YYYY)
    months_map = _MONTH_NAMES_INDIC.get(lang, _MONTH_NAMES_INDIC.get("hi", {}))

    def _expand_date(match: re.Match) -> str:
        d = int(match.group(1))
        m = int(match.group(2))
        y = match.group(3)
        if 1 <= d <= 31 and 1 <= m <= 12:
            m_name = months_map.get(m, str(m))
            return f"{d} {m_name} {y}"
        return match.group(0)

    normalized = re.sub(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', _expand_date, normalized)

    return normalized


def split_indic_sentences(
    text: str,
    min_chars: int = 25,
    max_chars: int = 300,
) -> list[str]:
    """Split Indic or English text into natural spoken sentences.

    Splits on sentence punctuation, then greedily merges short fragments so
    each output chunk is at least `min_chars` long. Any chunk that would
    exceed `max_chars` is broken on a word boundary. This keeps text blocks
    a reasonable size before sending to Indic-Speak, preventing TTS timeouts
    on oversized blocks while preserving prosodically complete clauses.
    """
    text = text.strip()
    if not text:
        return []

    # Step 0: Mask known honorific abbreviations, initials, and decimals
    masked = text
    for h in KNOWN_HONORIFICS:
        masked = re.sub(re.escape(h), h[:-1] + _PLACEHOLDER_DOT, masked, flags=re.IGNORECASE)
    # Mask single Latin or Indic initials followed by dot (e.g. 'A. P. J.' or 'क.')
    masked = re.sub(r'\b([A-Za-z\u0900-\u097F])\.', r'\1' + _PLACEHOLDER_DOT, masked)
    # Mask decimal numbers (e.g. '2.5' or '99.9%')
    masked = re.sub(r'(\d+)\.(\d+)', r'\1' + _PLACEHOLDER_DOT + r'\2', masked)

    tokens = INDIC_SENTENCE_REGEX.split(masked)

    # Step 1: pair each text part with the punctuation that followed it
    raw: list[str] = []
    i = 0
    while i < len(tokens):
        part = tokens[i].strip()
        punct = tokens[i + 1].strip() if i + 1 < len(tokens) else ""
        if part:
            raw.append(f"{part}{punct}".strip())
        i += 2

    raw = [s.replace(_PLACEHOLDER_DOT, ".") for s in raw]

    # Step 2: greedily merge short fragments
    merged: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for s in raw:
        if not buf:
            buf.append(s)
            buf_len = len(s)
        elif buf_len < min_chars:
            buf.append(s)
            buf_len += 1 + len(s)
        else:
            merged.append(" ".join(buf))
            buf = [s]
            buf_len = len(s)
    if buf:
        merged.append(" ".join(buf))

    # Step 3: hard-split anything still longer than max_chars
    out: list[str] = []
    for s in merged:
        while len(s) > max_chars:
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            chunk = s[:cut].strip()
            if chunk:
                out.append(chunk)
            s = s[cut:].strip()
        if s:
            out.append(s)

    return out if out else [text]


def inspect_audio(wav_path: str | Path) -> AudioMetadata:
    """Inspect audio file parameters across 4 tiers and generate diagnostic warnings for Bodhan models.

    Tier 1: stdlib `wave` for standard PCM WAV files.
    Tier 2: dynamic in-process `av` (PyAV) for in-memory container inspection.
    Tier 3: `soundfile.info` for WAV, FLAC, OGG, MP3 (skips M4A/AAC).
    Tier 4: `_ffprobe_duration` / FFmpeg fallback with safe Windows stdio.
    """
    path = Path(wav_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Audio file is empty (0 bytes): {path.name}")

    suffix = path.suffix.lower()
    is_wav = (suffix == ".wav")

    # --- Tier 1: stdlib wave for standard PCM WAV files ---
    if is_wav:
        try:
            with wave.open(str(path), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                frames = wf.getnframes()
                duration = frames / float(sr) if sr > 0 else 0.0

                warnings = _generate_audio_warnings(
                    path=path,
                    sr=sr,
                    ch=ch,
                    sw=sw,
                    duration=duration,
                    backend="wave",
                )
                return AudioMetadata(
                    duration_seconds=round(duration, 3),
                    sample_rate=sr,
                    channels=ch,
                    sample_width=sw,
                    num_frames=frames,
                    warnings=warnings,
                    backend="wave",
                )
        except (wave.Error, EOFError):
            pass  # Corrupted or non-standard WAV header — fall through to next tiers

    # --- Tier 2: Dynamic in-process av (PyAV) ---
    try:
        import av  # type: ignore[import-untyped]

        with av.open(str(path)) as container:
            audio_streams = [s for s in container.streams if s.type == "audio"]
            if audio_streams:
                st = audio_streams[0]
                sr = st.codec_context.sample_rate or st.sample_rate or 16000
                ch = st.codec_context.channels or st.channels or 1
                sw = 2
                dur = 0.0
                if container.duration is not None:
                    dur = float(container.duration) / av.time_base
                elif st.duration is not None and st.time_base is not None:
                    dur = float(st.duration * st.time_base)

                if dur > 0.0:
                    frames = int(dur * sr)
                    warnings = _generate_audio_warnings(
                        path=path,
                        sr=sr,
                        ch=ch,
                        sw=sw,
                        duration=dur,
                        backend="av",
                    )
                    return AudioMetadata(
                        duration_seconds=round(dur, 3),
                        sample_rate=sr,
                        channels=ch,
                        sample_width=sw,
                        num_frames=frames,
                        warnings=warnings,
                        backend="av",
                    )
    except (ImportError, Exception):
        pass

    # --- Tier 3: In-process soundfile.info for non-M4A formats ---
    if suffix not in {".m4a", ".aac"}:
        try:
            import soundfile as sf  # type: ignore[import-untyped]

            info = sf.info(str(path))
            if info.duration and info.duration > 0.0:
                sr = info.samplerate
                ch = info.channels
                frames = info.frames
                dur = float(info.duration)
                sw = 2
                subtype = (info.subtype or "").upper()
                if "24" in subtype:
                    sw = 3
                elif "32" in subtype or "FLOAT" in subtype:
                    sw = 4
                elif "U8" in subtype or "PCM_S8" in subtype:
                    sw = 1

                warnings = _generate_audio_warnings(
                    path=path,
                    sr=sr,
                    ch=ch,
                    sw=sw,
                    duration=dur,
                    backend="soundfile",
                )
                return AudioMetadata(
                    duration_seconds=round(dur, 3),
                    sample_rate=sr,
                    channels=ch,
                    sample_width=sw,
                    num_frames=frames,
                    warnings=warnings,
                    backend="soundfile",
                )
        except (ImportError, Exception):
            pass

    # --- Tier 4: FFmpeg / FFprobe fallback ---
    dur = _ffprobe_duration(path)
    if dur > 0.0:
        warnings = _generate_audio_warnings(
            path=path,
            sr=16000,
            ch=1,
            sw=2,
            duration=dur,
            backend="ffmpeg",
        )
        return AudioMetadata(
            duration_seconds=round(dur, 3),
            sample_rate=16000,
            channels=1,
            sample_width=2,
            num_frames=int(dur * 16000),
            warnings=warnings,
            backend="ffmpeg",
        )

    raise ValueError(f"Unable to inspect or decode audio file: {path.name}")


def convert_to_indic_pcm16(
    input_wav: str | Path,
    output_wav: str | Path,
    target_sr: int = 24000,
) -> Path:
    """Convert an arbitrary WAV (or other audio) file to standardized 24kHz mono PCM16 format for Bodhan models.

    Performs mono downmixing and linear interpolation resampling with zero external binaries for WAV.
    """
    in_path = Path(input_wav)
    out_path = Path(output_wav)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    audio, _ = load_audio_pcm16(in_path, target_sr=target_sr)

    with wave.open(str(out_path), "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(target_sr)
        out_wf.writeframes(audio.tobytes())

    return out_path


def concatenate_wav_files(
    wav_paths: list[str | Path],
    output_path: str | Path,
    silence_gap_ms: int = 200,
) -> Path:
    """Concatenate multiple PCM16 WAV files of matching parameters into one file.

    Args:
        wav_paths: List of file paths to WAV audio clips.
        output_path: Path where the merged WAV audio will be written.
        silence_gap_ms: Milliseconds of PCM silence inserted between clips (default 200ms)
            to avoid unnatural co-articulation and ASR boundary clipping.
    """
    if not wav_paths:
        raise ValueError("No WAV files provided for concatenation.")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    first_meta = inspect_audio(wav_paths[0])

    silence_frames = (
        int((silence_gap_ms / 1000.0) * first_meta.sample_rate)
        if silence_gap_ms > 0
        else 0
    )
    silence_bytes = b"\x00" * (silence_frames * first_meta.channels * first_meta.sample_width)

    with wave.open(str(out_file), "wb") as out_wf:
        out_wf.setnchannels(first_meta.channels)
        out_wf.setsampwidth(first_meta.sample_width)
        out_wf.setframerate(first_meta.sample_rate)

        for idx, p in enumerate(wav_paths):
            if idx > 0 and silence_bytes:
                out_wf.writeframes(silence_bytes)
            with wave.open(str(p), "rb") as in_wf:
                if (
                    in_wf.getframerate() != first_meta.sample_rate
                    or in_wf.getnchannels() != first_meta.channels
                    or in_wf.getsampwidth() != first_meta.sample_width
                ):
                    raise ValueError(f"WAV mismatch: {p} does not match {wav_paths[0]}")
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))

    return out_file


@overload
def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: Literal[False] = False,
) -> list[Path]: ...


@overload
def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: Literal[True] = ...,
) -> list[AudioChunkSegment]: ...


def chunk_wav_silence_aware(
    wav_path: str | Path,
    output_dir: str | Path,
    max_chunk_sec: float = 28.0,
    search_window_start: float = 20.0,
    return_segments: bool = False,
) -> list[Path] | list[AudioChunkSegment]:
    """Slice an audio file longer than max_chunk_sec at natural silence boundaries.

    Guarantees every chunk is strictly under 30.0 seconds to satisfy Indic-Transcribe.
    Snaps cuts to the nearest zero-crossing within ±5ms to avoid audio pops/clicks.
    Auto-converts non-WAV formats via ensure_wav.
    """
    path = Path(wav_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() != ".wav":
        path = ensure_wav(path, output_dir=out_dir)

    meta = inspect_audio(path)
    if meta.duration_seconds <= max_chunk_sec:
        if return_segments:
            return [AudioChunkSegment(path=path, start_sec=0.0, end_sec=meta.duration_seconds)]
        return [path]

    chunks: list[Path] = []
    segments: list[AudioChunkSegment] = []
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()

        # Read entire raw audio as numpy array (explicitly little-endian 16-bit signed integer)
        raw_bytes = wf.readframes(wf.getnframes())
        if sw == 2:
            audio_data = np.frombuffer(raw_bytes, dtype="<i2")
        else:
            raise ValueError(f"Unsupported sample width: {sw} bytes (only PCM16 supported).")

        if ch > 1:
            audio_data = audio_data.reshape(-1, ch)
            mono_data = audio_data.mean(axis=1).astype(np.int16)
        else:
            mono_data = audio_data

        total_samples = len(mono_data)
        current_sample = 0
        chunk_idx = 1

        while current_sample < total_samples:
            start_sec = round(current_sample / float(sr), 3)
            remaining = total_samples - current_sample
            remaining_sec = remaining / float(sr)

            if remaining_sec <= max_chunk_sec:
                slice_end = total_samples
            else:
                win_start_samp = int(current_sample + (search_window_start * sr))
                win_end_samp = int(current_sample + (max_chunk_sec * sr))
                win_start_samp = min(win_start_samp, total_samples)
                win_end_samp = min(win_end_samp, total_samples)

                # 100ms window RMS scan
                hop_len = int(0.1 * sr)
                min_rms = float("inf")
                best_cut = win_end_samp

                for cut_cand in range(win_start_samp, win_end_samp, hop_len):
                    test_win = mono_data[cut_cand : cut_cand + hop_len]
                    if len(test_win) > 0:
                        rms = np.sqrt(np.mean(test_win.astype(np.float64) ** 2))
                        if rms < min_rms:
                            min_rms = rms
                            best_cut = cut_cand

                # Snap cut to nearest zero-crossing within ±5ms to prevent audio pop/click
                zwin = int(0.005 * sr)
                lo = max(best_cut - zwin, current_sample)
                hi = min(best_cut + zwin, total_samples)
                if hi > lo:
                    best_cut = lo + int(np.argmin(np.abs(mono_data[lo:hi])))

                slice_end = best_cut

            end_sec = round(slice_end / float(sr), 3)

            # Write chunk
            chunk_file = out_dir / f"chunk_{chunk_idx:03d}.wav"
            chunk_slice = audio_data[current_sample:slice_end]

            with wave.open(str(chunk_file), "wb") as cw:
                cw.setnchannels(ch)
                cw.setsampwidth(sw)
                cw.setframerate(sr)
                cw.writeframes(chunk_slice.tobytes())

            chunks.append(chunk_file)
            segments.append(AudioChunkSegment(path=chunk_file, start_sec=start_sec, end_sec=end_sec))
            current_sample = slice_end
            chunk_idx += 1

    return segments if return_segments else chunks


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds into SubRip (SRT) timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    remainder = total_ms % 3600000
    minutes = remainder // 60000
    remainder %= 60000
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds into WebVTT timestamp HH:MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    remainder = total_ms % 3600000
    minutes = remainder // 60000
    remainder %= 60000
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_srt_content(cues: list[tuple[float, float, str]]) -> str:
    """Format a list of (start_sec, end_sec, text) cues into valid SubRip (.srt) string."""
    blocks: list[str] = []
    idx = 1
    for start, end, text in cues:
        clean_text = text.strip()
        if not clean_text:
            continue
        start_ts = format_timestamp_srt(start)
        end_ts = format_timestamp_srt(end)
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{clean_text}")
        idx += 1
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def format_vtt_content(cues: list[tuple[float, float, str]]) -> str:
    """Format a list of (start_sec, end_sec, text) cues into valid WebVTT (.vtt) string."""
    blocks: list[str] = ["WEBVTT"]
    idx = 1
    for start, end, text in cues:
        clean_text = text.strip()
        if not clean_text:
            continue
        start_ts = format_timestamp_vtt(start)
        end_ts = format_timestamp_vtt(end)
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{clean_text}")
        idx += 1
    if len(blocks) == 1:
        return "WEBVTT\n"
    return "\n\n".join(blocks) + "\n"


__all__ = [
    "AudioChunkSegment",
    "AudioMetadata",
    "chunk_wav_silence_aware",
    "concatenate_wav_files",
    "convert_to_indic_pcm16",
    "ensure_wav",
    "format_srt_content",
    "format_timestamp_srt",
    "format_timestamp_vtt",
    "format_vtt_content",
    "inspect_audio",
    "load_audio_pcm16",
    "normalize_indic_speech_text",
    "resample_pcm16",
    "split_indic_sentences",
    "INDIC_SENTENCE_REGEX",
    "KNOWN_HONORIFICS",
    "_resolve_tool_path",
    "_resolve_ffmpeg_cmd",
    "_resolve_ffprobe_cmd",
    "_ffprobe_duration",
]
