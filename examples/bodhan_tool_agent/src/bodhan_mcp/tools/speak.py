"""Speech synthesis tool with character limits, voice validation, and AI-generated metadata."""

from pathlib import Path

from bodhan_mcp import config, errors
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import SpeakOut


async def synthesize_speech(
    text: str,
    language: str,
    voice_id: str,
    out_dir: str | Path | None = None,
    client: BodhanClient | None = None,
) -> SpeakOut | errors.ToolErrorPayload:
    """Synthesize speech audio from text using Indic-Speak."""
    if not isinstance(language, str):
        language = str(language or "hi")
    lang = language.lower().strip()
    lang_name_map = {
        "hindi": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn",
        "marathi": "mr", "gujarati": "gu", "kannada": "kn", "malayalam": "ml",
        "punjabi": "pa", "odia": "or", "assamese": "as", "urdu": "ur", "english": "en"
    }
    lang = lang_name_map.get(lang, lang)

    if lang not in config.SUPPORTED_LANGS["speak"]:
        return errors.make_tool_error(
            code=errors.UNSUPPORTED_LANGUAGE,
            message=f"Language '{language}' is not supported for speech synthesis.",
            hint=f"Supported speech languages: {', '.join(config.SUPPORTED_LANGS['speak'])}. Note: English TTS is not supported.",
            details={"language": language},
        )

    # Bulletproof voice_id resolution
    if isinstance(voice_id, dict):
        if lang in voice_id and isinstance(voice_id[lang], list) and voice_id[lang]:
            voice_id = voice_id[lang][0]
        else:
            voice_id = "Kavya"
    elif not isinstance(voice_id, str):
        voice_id = "Kavya"

    voice_lower = voice_id.lower()
    if "kavya" in voice_lower or voice_lower in ("default", "auto", ""):
        voice_id = "Kavya"
    elif "ankur" in voice_lower:
        voice_id = "Ankur"
    elif "voices=" in voice_lower:
        voice_id = "Kavya"

    valid_voices = config.VOICE_LIBRARY.get(lang, [])
    if voice_id not in valid_voices:
        return errors.make_tool_error(
            code=errors.INVALID_ARGUMENT,
            message=f"Voice '{voice_id}' is invalid for language '{lang}'.",
            hint=f"Valid voices for {lang}: {', '.join(valid_voices)}",
            details={"language": lang, "voice_id": voice_id},
        )

    char_count = len(text)
    if char_count > config.SPEAK_MAX_CHARS:
        est_cost = (char_count / 10000.0) * config.PRICES["speak_per_10k_chars"]
        return errors.make_tool_error(
            code=errors.INPUT_TOO_LONG,
            message=f"Text exceeds {config.SPEAK_MAX_CHARS} character maximum (got {char_count} chars).",
            hint="Chunk text before calling synthesize_speech.",
            details={"chars": char_count, "max_chars": config.SPEAK_MAX_CHARS, "estimated_cost_inr": est_cost},
        )

    bodhan = client or BodhanClient()
    try:
        res = await bodhan.speak(text=text, language=lang, voice_id=voice_id)
        cost = float(res.get("cost_inr", 0.0))

        # Write audio and sidecar AI label
        output_dir = Path(out_dir) if out_dir else Path("out/artifacts")
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"speech_{lang}_{voice_id}.wav"

        # If data has audio bytes
        audio_content = res.get("audio_bytes") or b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        audio_path.write_bytes(audio_content)

        # AI-generated sidecar (.ai.txt)
        sidecar_path = audio_path.with_suffix(".ai.txt")
        sidecar_path.write_text(f"ai_generated=True\nmodel=bodhan-speak\nlanguage={lang}\nvoice={voice_id}\n")

        duration_est = round(char_count / 15.0, 2)  # ~15 chars per sec approx
        return SpeakOut(
            path=str(audio_path),
            duration_s=duration_est,
            chars=char_count,
            ai_generated=True,
            cost_inr=round(cost, 4),
        )
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.INTERNAL,
            message=f"Speech synthesis failed: {exc}",
            hint="Check upstream API status.",
            details={"error": str(exc)},
        )
