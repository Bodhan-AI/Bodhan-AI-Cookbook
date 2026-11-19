"""
Indic-Speak Tool: Speech synthesis with sentence-level chunking and audio concatenation.
"""

import os
import re
import tempfile
import wave
from typing import List
from ..models import SpeakInput, SpeakResult, ToolCallMetadata, SUPPORTED_LANGUAGES
from ..client import BodhanClient, BodhanAPIError
from ..pricing import calculate_speak_cost


def split_text_for_tts(text: str, max_chars: int = 250) -> List[str]:
    """
    Split text into sentence-length chunks suitable for TTS synthesis.
    Splits on sentence terminators: '.', '।', '?', '!', '\n'.
    """
    if len(text) <= max_chars:
        return [text]

    # Regex splitting on punctuation while keeping separators
    sentences = re.split(r"([।\.\?!;\n]+)", text)
    chunks = []
    current = ""

    for part in sentences:
        if not part:
            continue
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current.strip():
                chunks.append(current.strip())
            current = part

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text]


def concatenate_wav_files(input_paths: List[str], output_path: str) -> float:
    """Concatenate multiple WAV files into one and return total duration in seconds."""
    if not input_paths:
        return 0.0

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    if len(input_paths) == 1:
        # Just copy or read duration
        import shutil
        shutil.copyfile(input_paths[0], output_path)
        with wave.open(output_path, "rb") as f:
            return f.getnframes() / float(f.getframerate())

    data = []
    params = None

    for p in input_paths:
        try:
            with wave.open(p, "rb") as w:
                if params is None:
                    params = w.getparams()
                data.append(w.readframes(w.getnframes()))
        except Exception:
            continue

    if not params:
        # Fallback empty wave
        with wave.open(output_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"")
        return 0.0

    with wave.open(output_path, "wb") as w:
        w.setparams(params)
        for frame in data:
            w.writeframes(frame)

    with wave.open(output_path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def run_speak(args: SpeakInput, client: BodhanClient, default_output_dir: str = "./out/artifacts") -> SpeakResult:
    """
    Synthesize speech using Indic-Speak with automatic chunking.
    """
    lang = args.language.lower().strip()
    if lang not in SUPPORTED_LANGUAGES:
        name_to_code = {v.lower(): k for k, v in SUPPORTED_LANGUAGES.items()}
        if lang in name_to_code:
            lang = name_to_code[lang]
        else:
            raise BodhanAPIError(
                f"Unsupported language '{args.language}' for Indic-Speak. Only Indic languages are supported.",
                status_code=400,
                details={"unsupported_language": args.language}
            )

    output_path = args.output_audio_path
    if not output_path:
        os.makedirs(default_output_dir, exist_ok=True)
        output_path = os.path.join(default_output_dir, f"speech_{lang}_{os.getpid()}_{int(os.times().elapsed * 1000)}.wav")

    chunks = split_text_for_tts(args.text, max_chars=250)
    temp_dir = tempfile.mkdtemp(prefix="tts_chunks_")
    chunk_paths = []
    total_latency = 0.0

    for idx, chunk in enumerate(chunks):
        chunk_file = os.path.join(temp_dir, f"chunk_{idx:04d}.wav")
        res = client.speak(
            text=chunk,
            language=lang,
            output_path=chunk_file,
            gender=args.gender or "female",
        )
        total_latency += res["latency_ms"]
        chunk_paths.append(chunk_file)

    total_duration = concatenate_wav_files(chunk_paths, output_path)
    total_chars = len(args.text)
    total_cost = calculate_speak_cost(total_chars)

    return SpeakResult(
        audio_path=output_path,
        duration_seconds=round(total_duration, 2),
        character_count=total_chars,
        metadata=ToolCallMetadata(
            cost_inr=total_cost,
            latency_ms=round(total_latency, 2),
            model_name="indic-speak",
        ),
    )
