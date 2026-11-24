"""MCP Tool: bodhan_speak
Text-to-speech synthesis via Indic-Speak with sentence chunking and MP3 stitching.
"""

import os
import re
import tempfile
from typing import List, Optional
from bodhan_mcp.client import bodhan_client
from bodhan_mcp.cost import calculate_speak_cost
from bodhan_mcp.types import SpeakInput, SpeakOutput, ToolError
from bodhan_mcp.audio_helpers import get_audio_info, stitch_audio_files


def split_text_into_sentences(text: str, max_chars: int = 180) -> List[str]:
    """Split text into natural clauses/sentences to avoid TTS omissions and quality loss."""
    # Split on common Indian and English sentence terminators
    raw_sentences = re.split(r'([।\.\?\!\n]+)', text)
    chunks: List[str] = []
    current = ""

    for part in raw_sentences:
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

    return chunks if chunks else [text]


def synthesize_speech(
    text: Optional[str] = None,
    language: str = "hi",
    voice: str = "Amit",
    output_path: Optional[str] = None,
    text_artifact_id: Optional[str] = None,
) -> SpeakOutput:
    """Synthesize speech from text or text_artifact_id, handling sentence splitting and MP3 stitching."""
    if (not text or not text.strip()) and text_artifact_id:
        if os.path.exists(text_artifact_id):
            with open(text_artifact_id, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            raise ToolError("FILE_NOT_FOUND", f"Text artifact reference file not found: {text_artifact_id}")

    if not text or not text.strip():
        raise ToolError("EMPTY_TEXT", "Text for speech synthesis cannot be empty. Provide either 'text' or a valid 'text_artifact_id'.")

    clean_text = text.strip()

    from bodhan_mcp.tools.voices import OFFICIAL_VOICES, get_default_voice
    valid_voices = {v.name for v in OFFICIAL_VOICES}
    if voice not in valid_voices:
        voice = get_default_voice(language=language)

    sentences = split_text_into_sentences(clean_text)
    temp_dir = tempfile.mkdtemp(prefix="tts_chunks_")
    chunk_files: List[str] = []
    all_cached = True

    try:
        for idx, sentence in enumerate(sentences):
            audio_bytes, is_cached = bodhan_client.speak(text=sentence, voice=voice, lang=language)
            if not is_cached:
                all_cached = False

            chunk_path = os.path.join(temp_dir, f"tts_{idx:03d}.mp3")
            with open(chunk_path, "wb") as f:
                f.write(audio_bytes)
            chunk_files.append(chunk_path)

        # Output target path (MP3)
        if output_path is None:
            output_path = os.path.join(temp_dir, "final_speech.mp3")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # Stitch MP3 chunks
        stitch_audio_files(chunk_files, output_path)
        info = get_audio_info(output_path)
        duration_s = info["duration"]

    finally:
        # Clean up chunk files
        for cf in chunk_files:
            if os.path.exists(cf) and cf != output_path:
                try:
                    os.remove(cf)
                except Exception:
                    pass

    total_chars = len(clean_text)
    cost_inr = 0.0 if all_cached else calculate_speak_cost(total_chars)

    return SpeakOutput(
        audio_path=output_path,
        duration_seconds=round(duration_s, 2),
        character_count=total_chars,
        cost_inr=cost_inr,
        cached=all_cached,
    )

