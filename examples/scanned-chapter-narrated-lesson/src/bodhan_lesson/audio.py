import io
import json
import re
import time
import wave
from pathlib import Path
import requests
from .config import BODHAN_BASE_URL
from .lesson import StructuredLesson
from .translate import normalize_lang


def _split_sentences(text: str, max_chars: int = 240) -> list[str]:
    """Split narration into short sentence chunks suitable for ~30s indic-speak calls."""
    raw_parts = re.split(r"(?<=[।.?!])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for part in raw_parts:
        if not part:
            continue
        if len(current) + len(part) + 1 <= max_chars:
            current = f"{current} {part}".strip()
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def _tts_chunk(text: str, lang: str, voice: str, speak_key: str, max_retries: int = 5) -> bytes:
    delay = 4.0
    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            f"{BODHAN_BASE_URL}/audio/speech",
            headers={
                "Authorization": f"Bearer {speak_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": json.dumps({"lang": lang}),
            },
            timeout=120,
        )
        if resp.status_code == 429 and attempt < max_retries:
            wait = float(resp.headers.get("Retry-After", delay))
            print(f"[TTS] Rate limited (429). Waiting {wait:.0f}s...")
            time.sleep(wait)
            delay *= 2
            continue
        if not resp.ok:
            raise RuntimeError(f"TTS error {resp.status_code}: {resp.text}")
        return resp.content
    raise RuntimeError("TTS failed after max retries.")


def _concat_wavs(wav_blobs: list[bytes], out_path: Path) -> None:
    """Concatenate multiple PCM WAV byte buffers into a single WAV file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as out_wf:
        for idx, blob in enumerate(wav_blobs):
            with wave.open(io.BytesIO(blob), "rb") as in_wf:
                if idx == 0:
                    out_wf.setparams(in_wf.getparams())
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))


def _trim_wav_first_seconds(wav_path: Path, seconds: float = 25.0) -> bytes:
    """Extract the first ~25 seconds of a WAV file for indic-transcribe verification."""
    with wave.open(str(wav_path), "rb") as wf:
        params = wf.getparams()
        frames = wf.readframes(int(params.framerate * seconds))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out_wf:
            out_wf.setparams(params)
            out_wf.writeframes(frames)
        return buf.getvalue()


def verify_audio_with_asr(wav_path: Path, lang: str, transcribe_key: str) -> str:
    """Transcribe the opening clip of generated audio using indic-transcribe."""
    clip_bytes = _trim_wav_first_seconds(wav_path, seconds=25.0)
    resp = requests.post(
        f"{BODHAN_BASE_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {transcribe_key}"},
        files={"file": (wav_path.name, clip_bytes, "audio/wav")},
        data={"model": "indic-transcribe", "language": lang},
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"ASR error {resp.status_code}: {resp.text}")
    return resp.json().get("text", "").strip()


def synthesize_lesson_audio(
    lesson: StructuredLesson,
    target_lang: str,
    voice: str,
    out_dir: Path,
    speak_key: str,
    transcribe_key: str,
    verify_asr: bool = False,
) -> dict[str, Path]:
    lang = normalize_lang(target_lang)
    audio_dir = out_dir / f"audio_{lang}"
    audio_dir.mkdir(parents=True, exist_ok=True)

    section_paths: dict[str, Path] = {}
    all_wav_blobs: list[bytes] = []

    for sec in lesson.sections:
        sec_wav = audio_dir / f"{sec.section_id}.wav"
        if sec_wav.exists():
            print(f"[TTS] {sec.section_id}: loaded from cache ({sec_wav.name})")
            blob = sec_wav.read_bytes()
            all_wav_blobs.append(blob)
            section_paths[sec.section_id] = sec_wav
            continue

        chunks = _split_sentences(sec.narration_script)
        print(f"[TTS] Synthesizing {sec.section_id} ({len(chunks)} chunks, voice={voice}, lang={lang})...")
        sec_blobs = []
        for chunk in chunks:
            sec_blobs.append(_tts_chunk(chunk, lang, voice, speak_key))
            time.sleep(0.5)

        _concat_wavs(sec_blobs, sec_wav)
        all_wav_blobs.extend(sec_blobs)
        section_paths[sec.section_id] = sec_wav

    full_wav = audio_dir / "full_lesson.wav"
    if all_wav_blobs and not full_wav.exists():
        _concat_wavs(all_wav_blobs, full_wav)
    section_paths["full"] = full_wav

    if verify_asr and full_wav.exists():
        print("[ASR] Verifying generated audio with indic-transcribe...")
        transcript = verify_audio_with_asr(full_wav, lang, transcribe_key)
        print(f"[ASR] Heard back: {transcript[:220]}...")

    return section_paths