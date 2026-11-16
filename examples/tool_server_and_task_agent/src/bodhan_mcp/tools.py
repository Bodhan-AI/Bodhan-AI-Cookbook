"""Tool implementations shared by the MCP server and the task agent.

Design: one tool per Bodhan model, plus two local helpers. Each tool
handles its model's limits internally, so callers never have to:
- STT: audio is converted to WAV and split into <=30s chunks.
- Translation: long text is split at sentence boundaries; a ``truncated``
  response triggers a smaller re-split instead of silent loss.
- TTS: long text is split into short chunks, synthesised separately and
  joined; the voice defaults to the language's female voice.
- OCR: PDFs are rendered to page images; each page is one OCR call.
- Media never travels as inline base64 in any LLM context: tools take
  file paths and return file paths. (The Bodhan OCR API itself requires a
  base64 data URI; that stays inside the API call, never in a prompt.)

Every tool returns a JSON-serializable dict and raises ToolError with a
stable code on failure. Costs are reported per call.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import languages, media
from .client import MAX_TRANSLATE_CHARS, BodhanClient, MockBodhanClient
from .errors import (
    INVALID_INPUT,
    UNSUPPORTED_LANGUAGE,
    UNSUPPORTED_MEDIA,
    ToolError,
)

Client = BodhanClient | MockBodhanClient

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most ``max_chars`` at sentence boundaries."""
    sentences = [s for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    if not sentences:
        return [text] if text.strip() else []
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            while len(sentence) > max_chars:
                chunks.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current = sentence
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# transcribe_audio
# ---------------------------------------------------------------------------
def transcribe_audio(
    client: Client,
    file: str,
    language: str | None = None,
    model: str = "core",
    workdir: str | None = None,
) -> dict:
    """Transcribe an audio file to text.

    ``file`` is a path (WAV, FLAC, OGG or MP3). Files longer than 30 seconds
    are split into <=30s chunks and the transcripts joined. ``language`` is
    the ISO code of the spoken language when known; omit it for auto-detect.
    Returns the transcript, detected language, per-chunk segments with
    timestamps, duration and cost in INR.
    """
    path = media.require_file(file)
    if path.suffix.lower() not in media.AUDIO_EXTS:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            f"Not an audio file: {path.name}",
            hint=f"Send one of: {', '.join(sorted(media.AUDIO_EXTS))}.",
        )
    if language is not None and language not in languages.STT_LANGUAGES:
        raise ToolError(
            UNSUPPORTED_LANGUAGE,
            f"Speech recognition in {language!r} is not supported.",
            hint=f"Supported: {', '.join(sorted(languages.STT_LANGUAGES))}.",
        )
    if model not in ("core", "flex"):
        raise ToolError(
            INVALID_INPUT,
            f"Unknown transcribe model {model!r}.",
            hint='Use "core" or "flex".',
        )
    tmp = workdir or tempfile.mkdtemp(prefix="bodhan_stt_")
    wav = media.ensure_wav(path, tmp)
    chunks = media.split_wav(wav, media.MAX_AUDIO_SECONDS, tmp)
    segments = []
    texts = []
    total_cost = 0.0
    cached_flags: list[bool] = []
    offset = 0.0
    for chunk in chunks:
        result = client.transcribe(str(chunk), language=language)
        cached_flags.append(bool(result.cached))
        texts.append(result.text)
        duration = result.duration_seconds
        segments.append(
            {
                "start": round(offset, 2),
                "end": round(offset + duration, 2),
                "text": result.text,
                "language": result.language,
            }
        )
        total_cost += 0.0 if result.cached else result.cost_inr
        offset += duration
    detected = next((s["language"] for s in segments if s["language"]), language)
    return {
        "text": " ".join(t for t in texts if t).strip(),
        "language": detected,
        "duration_seconds": round(offset, 2),
        "chunks": len(chunks),
        "segments": segments,
        "cost_inr": round(total_cost, 4),
        "cached": bool(cached_flags) and all(cached_flags),
    }


# ---------------------------------------------------------------------------
# translate_text
# ---------------------------------------------------------------------------
def translate_text(
    client: Client,
    text: str,
    target_language: str,
    script: str = "native",
) -> dict:
    """Translate text into ``target_language`` (ISO code, e.g. ``ta``).

    Long text is split at sentence boundaries so no request exceeds the
    context window; a truncated response is re-split smaller and retried
    once. Returns the translation, the normalised target/script, a
    ``truncated`` flag (False on success) and the cost in INR.
    """
    if not text or not text.strip():
        raise ToolError(INVALID_INPUT, "Nothing to translate: text is empty.")
    if script not in ("native", "roman", "codemix"):
        raise ToolError(
            INVALID_INPUT,
            f"Unknown script {script!r}.",
            hint='Use "native", "roman" or "codemix".',
        )
    if target_language == "en" and script == "roman":
        raise ToolError(
            INVALID_INPUT,
            'script "roman" is rejected for target_language "en".',
            hint='Use script "native" for English.',
        )
    # The client raises unsupported_language for bhb/bho/unknown codes.
    chunks = _split_sentences(text, MAX_TRANSLATE_CHARS)
    parts: list[str] = []
    total_cost = 0.0
    total_tokens = 0
    cached_flags: list[bool] = []
    for chunk in chunks:
        result = client.translate(chunk, target_language, script=script)
        cached_flags.append(bool(result.cached))
        if result.truncated:
            # Re-split smaller and retry once per chunk.
            subparts = []
            for sub in _split_sentences(chunk, MAX_TRANSLATE_CHARS // 2):
                sub_result = client.translate(sub, target_language, script=script)
                cached_flags.append(bool(sub_result.cached))
                if sub_result.truncated:
                    raise ToolError(
                        "invalid_input",
                        "Translation stayed truncated after re-splitting; "
                        "the input is too long for one request.",
                        hint="Send a shorter text.",
                    )
                subparts.append(sub_result.translation)
                total_cost += 0.0 if sub_result.cached else sub_result.cost_inr
                total_tokens += sub_result.output_tokens
            parts.append(" ".join(subparts))
        else:
            parts.append(result.translation)
            total_cost += 0.0 if result.cached else result.cost_inr
            total_tokens += result.output_tokens
    translation = " ".join(parts)
    return {
        "translation": translation,
        "target_language": target_language,
        "script": script,
        "truncated": False,
        "output_tokens": total_tokens,
        "cost_inr": round(total_cost, 4),
        "cached": bool(cached_flags) and all(cached_flags),
    }


# ---------------------------------------------------------------------------
# synthesize_speech
# ---------------------------------------------------------------------------
def synthesize_speech(
    client: Client,
    text: str,
    language: str,
    voice: str | None = None,
    style: str | None = None,
    out_path: str | None = None,
    workdir: str | None = None,
    chunk_chars: int = 400,
) -> dict:
    """Read text aloud in ``language`` and write a WAV file.

    ``voice`` is a name from the voice library (default: the language's
    female voice). Long text is split into short chunks (a sentence or two
    each), synthesised separately and joined into one WAV. Returns the
    audio file path, duration, chunk count, voice and cost in INR.
    The audio is AI-generated speech; label it as such when sharing it.
    """
    if not text or not text.strip():
        raise ToolError(INVALID_INPUT, "Nothing to speak: text is empty.")
    if language not in languages.TTS_LANGUAGES:
        raise ToolError(
            UNSUPPORTED_LANGUAGE,
            f"Speech synthesis in {language!r} is not supported by Indic-Speak.",
            hint=f"Supported: {', '.join(sorted(languages.TTS_LANGUAGES))}.",
        )
    if voice is None:
        voice = languages.default_voice(language, "female")
    elif voice not in languages.ALL_VOICE_NAMES:
        raise ToolError(
            INVALID_INPUT,
            f"Unknown voice {voice!r}.",
            hint="List voices with the bodhan://voices MCP resource.",
        )
    if style is not None and style not in languages.TTS_STYLES:
        raise ToolError(
            INVALID_INPUT,
            f"Unknown speaking style {style!r}.",
            hint=f"Supported styles: {', '.join(sorted(languages.TTS_STYLES))}.",
        )
    tmp = Path(workdir or tempfile.mkdtemp(prefix="bodhan_tts_"))
    tmp.mkdir(parents=True, exist_ok=True)
    # ~400 chars ≈ 30s of speech; split at sentence boundaries.
    chunks = _split_sentences(text, chunk_chars)
    wav_paths: list[Path] = []
    total_cost = 0.0
    total_duration = 0.0
    cached_flags: list[bool] = []
    for i, chunk in enumerate(chunks):
        result = client.synthesize(chunk, language, voice, style=style)
        cached_flags.append(bool(result.cached))
        chunk_path = tmp / f"tts_chunk{i:02d}.wav"
        chunk_path.write_bytes(result.audio_bytes)
        wav_paths.append(chunk_path)
        total_cost += 0.0 if result.cached else result.cost_inr
        total_duration += result.duration_seconds
    out = Path(out_path) if out_path else tmp / "speech.wav"
    if len(wav_paths) == 1:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(wav_paths[0].read_bytes())
    else:
        media.concat_wavs(wav_paths, out)
    # Lets a mock client back-transcribe the joined file later.
    register = getattr(client, "register_audio", None)
    if callable(register):
        register(out.read_bytes(), text)
    return {
        "audio_path": str(out),
        "duration_seconds": round(total_duration, 2),
        "chunks": len(chunks),
        "voice": voice,
        "language": language,
        "cost_inr": round(total_cost, 4),
        "cached": bool(cached_flags) and all(cached_flags),
        "note": "AI-generated speech (Indic-Speak). Label it as AI-generated.",
    }


# ---------------------------------------------------------------------------
# ocr_document
# ---------------------------------------------------------------------------
def ocr_document(
    client: Client,
    file: str,
    table_format: str = "markdown",
) -> dict:
    """Extract reading-ordered text from a page image or a PDF.

    PNG/JPEG are sent as one OCR call each. PDFs are rendered to page
    images first (one OCR call per page). Returns the Markdown, per-page
    text, the number of layout blocks and the cost in INR.
    """
    path = media.require_file(file)
    if table_format not in ("html", "markdown"):
        raise ToolError(
            INVALID_INPUT,
            f"Unknown table_format {table_format!r}.",
            hint='Use "html" or "markdown".',
        )
    if path.suffix.lower() in media.IMAGE_EXTS:
        pages = [path]
    elif path.suffix.lower() == ".pdf":
        pages = media.render_pdf_pages(path)
        if not pages:
            raise ToolError(
                "invalid_input",
                f"PDF has no pages: {path.name}",
            )
    else:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            f"OCR needs a PNG/JPEG image or a PDF, not {path.suffix}.",
            hint="Send a page image, or a PDF to render page by page.",
        )
    page_texts: list[str] = []
    total_blocks = 0
    total_cost = 0.0
    cached_flags: list[bool] = []
    for page in pages:
        result = _ocr_one_page(client, page, table_format)
        cached_flags.append(bool(result.cached))
        page_texts.append(result.markdown)
        total_blocks += len(result.blocks)
        total_cost += 0.0 if result.cached else result.cost_inr
    return {
        "markdown": "\n\n".join(page_texts),
        "pages": [
            {"page": i + 1, "markdown": text} for i, text in enumerate(page_texts)
        ],
        "page_count": len(pages),
        "blocks": total_blocks,
        "cost_inr": round(total_cost, 4),
        "cached": bool(cached_flags) and all(cached_flags),
    }


def _ocr_one_page(client: Client, image_path: Path, table_format: str):
    """One OCR call, retrying once with a larger token budget on a 502."""
    try:
        return client.ocr_image(str(image_path), table_format=table_format)
    except ToolError as exc:
        if exc.status == 502:
            return client.ocr_image(
                str(image_path), table_format=table_format, max_tokens=4096
            )
        raise


# ---------------------------------------------------------------------------
# inspect_media
# ---------------------------------------------------------------------------
def inspect_media(file: str) -> dict:
    """Describe a file for planning: kind, format, size, duration/pages.

    Local only, no model call, no cost. Use it before choosing tools.
    """
    return media.inspect_file(file)


# ---------------------------------------------------------------------------
# identify_language
# ---------------------------------------------------------------------------
def identify_language(
    client: Client,
    text: str | None = None,
    audio_file: str | None = None,
) -> dict:
    """Identify the language of text or of an audio clip.

    For text, detection is deterministic script analysis (no model call):
    it returns the script and candidate languages, and says plainly when
    several languages share that script. For audio, the clip is transcribed
    first (Bodhan STT, auto-detect) and the transcript is analysed.
    """
    if text is None and audio_file is None:
        raise ToolError(
            INVALID_INPUT,
            "Give either text or an audio_file to identify.",
        )
    if audio_file is not None:
        result = transcribe_audio(client, audio_file)
        text = result["text"]
        detected = result["language"]
        script = languages.detect_script(text or "")
        return {
            "language": detected,
            "script": script["script"],
            "candidates": script["candidates"],
            "confidence": script["confidence"],
            "transcript": text,
            "method": "transcribe-then-script-detect",
            "cost_inr": result.get("cost_inr", 0.0),
            "cached": bool(result.get("cached", False)),
        }
    script = languages.detect_script(text or "")
    candidates = script["candidates"]
    return {
        "language": candidates[0] if len(candidates) == 1 else None,
        "script": script["script"],
        "candidates": candidates,
        "confidence": script["confidence"],
        "method": "script-detect",
        "note": (
            "One candidate means the script pins the language; several "
            "candidates mean the script is shared and the language is "
            "ambiguous — transcribe or ask, do not guess."
            if len(candidates) != 1
            else "Script pins the language."
        ),
    }


TOOL_NAMES = [
    "transcribe_audio",
    "translate_text",
    "synthesize_speech",
    "ocr_document",
    "inspect_media",
    "identify_language",
]


def run_tool(client: Client, name: str, arguments: dict) -> dict:
    """Dispatch a tool call by name (used by the agent and the MCP server)."""
    tools = {
        "transcribe_audio": transcribe_audio,
        "translate_text": translate_text,
        "synthesize_speech": synthesize_speech,
        "ocr_document": ocr_document,
        "inspect_media": inspect_media,
        "identify_language": identify_language,
    }
    fn = tools.get(name)
    if fn is None:
        raise ToolError(
            INVALID_INPUT,
            f"Unknown tool {name!r}.",
            hint=f"Available tools: {', '.join(TOOL_NAMES)}.",
        )
    if name == "inspect_media":
        return fn(**arguments)
    return fn(client, **arguments)


def tool_schemas() -> list[dict]:
    """JSON-Schema descriptions of the six tools (for the MCP server and
    for the LLM's function-calling interface)."""
    return [
        {
            "name": "transcribe_audio",
            "description": (
                "Transcribe an audio file to text with Bodhan Indic-Transcribe. "
                "Give the file path; long audio is split into <=30s chunks "
                "automatically. Pass language (ISO code) when known, omit it "
                "for auto-detect. Returns the transcript, language, per-chunk "
                "segments with timestamps, duration and cost in INR."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to a WAV, FLAC, OGG or MP3 file.",
                    },
                    "language": {
                        "type": "string",
                        "description": "ISO code, e.g. 'hi'. Omit for auto-detect.",
                    },
                    "model": {"type": "string", "enum": ["core", "flex"], "default": "core"},
                },
                "required": ["file"],
            },
        },
        {
            "name": "translate_text",
            "description": (
                "Translate text into a target language with Bodhan Indic-Translate. "
                "The source language is inferred; only the target is named. "
                "Long text is split at sentence boundaries. Supported targets "
                "are the 23 Indic languages plus English; French, German and "
                "other European languages are NOT supported and fail with "
                "unsupported_language. Returns the translation and cost in INR."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to translate."},
                    "target_language": {
                        "type": "string",
                        "description": "ISO code to translate into, e.g. 'ta', 'mr', 'hi'.",
                    },
                    "script": {
                        "type": "string",
                        "enum": ["native", "roman", "codemix"],
                        "default": "native",
                    },
                },
                "required": ["text", "target_language"],
            },
        },
        {
            "name": "synthesize_speech",
            "description": (
                "Read text aloud with Bodhan Indic-Speak and write a WAV file. "
                "Give the text, the language ISO code and optionally a voice "
                "name (default: the language's female voice) and a speaking "
                "style. Long text is split into short chunks and joined. "
                "Returns the audio file path, duration, voice and cost in INR. "
                "The audio is AI-generated; label it as such when sharing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to read aloud."},
                    "language": {
                        "type": "string",
                        "description": "ISO code of the language, e.g. 'bn', 'ta'.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Voice name, e.g. 'Ishita'. Omit for the default.",
                    },
                    "style": {
                        "type": "string",
                        "description": "Speaking style, e.g. 'news'. Omit for neutral.",
                    },
                    "out_path": {
                        "type": "string",
                        "description": "Where to write the WAV file. Defaults to a temp file.",
                    },
                },
                "required": ["text", "language"],
            },
        },
        {
            "name": "ocr_document",
            "description": (
                "Extract reading-ordered text from a page image (PNG/JPEG) or "
                "a PDF with Bodhan Indic-OCR. PDFs are rendered to page "
                "images first, one OCR call per page. Returns Markdown, "
                "per-page text, block count and cost in INR. Tables can be "
                "returned as HTML (default in the API) or Markdown."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to a PNG/JPEG image or a PDF."},
                    "table_format": {
                        "type": "string",
                        "enum": ["html", "markdown"],
                        "default": "markdown",
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "inspect_media",
            "description": (
                "Describe a file for planning: kind (audio/image/document/text/"
                "video), format, size, audio duration, image dimensions or PDF "
                "page count. Local only, no model call, no cost. Call it on "
                "every input file before choosing other tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Path to the file to describe."},
                },
                "required": ["file"],
            },
        },
        {
            "name": "identify_language",
            "description": (
                "Identify the language of text or of an audio clip. For text "
                "this is deterministic script analysis (no model call) and "
                "returns the script plus candidate languages; when several "
                "languages share a script the answer is ambiguous and says "
                "so. For audio the clip is transcribed first, then analysed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to analyse."},
                    "audio_file": {
                        "type": "string",
                        "description": "Path to an audio file to transcribe and analyse.",
                    },
                },
            },
        },
    ]
