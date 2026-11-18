from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import wave
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .errors import ToolError
from .settings import Settings

LANGS = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva",
    "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "sd-Arab", "ta", "te", "ur",
}
TRANSCRIBE_LANGS = {"as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur"}
VOICES = {
    "as": {"female": "Prastuti", "male": "Ankur"}, "bn": {"female": "Ishita", "male": "Sourav"},
    "brx": {"female": "Gwrbw", "male": "Sansuma"}, "doi": {"female": "Preeti", "male": "Sham"},
    "gu": {"female": "Dhara", "male": "Parth"}, "hi": {"female": "Kavya", "male": "Amit"},
    "kn": {"female": "Deepika", "male": "Adarsh"}, "kok": {"female": "Anjali", "male": "Sandeep"},
    "ks": {"female": "Zoon", "male": "Ishfaq"}, "mai": {"female": "Vaidehi", "male": "Madhukar"},
    "ml": {"female": "Lakshmi", "male": "Kiran"}, "mni": {"female": "Thoibi", "male": "Chaoba"},
    "mr": {"female": "Anagha", "male": "Chinmay"}, "ne": {"female": "Srijana", "male": "Sagar"},
    "or": {"female": "Itishree", "male": "Akash"}, "pa": {"female": "Kaur", "male": "Manpreet"},
    "sa": {"female": "Bharati", "male": "Aryaman"}, "sat": {"female": "Phulmani", "male": "Sibu"},
    "sd": {"female": "Moomal", "male": "Rano"}, "ta": {"female": "Anitha", "male": "Arun"},
    "te": {"female": "Sravani", "male": "Vamsi"}, "ur": {"female": "Saba", "male": "Zaid"},
}
PRICES = {"transcribe_per_minute": 0.10, "translate_per_10k_tokens": 0.20, "speak_per_10k_chars": 6.0, "ocr_per_page": 0.20}
MAX_TRANSLATE_CHARS = 9000
MAX_TTS_CHARS = 400
_API_SLOTS = threading.BoundedSemaphore(2)
_TRANSCRIBE_CACHE: dict[str, dict[str, Any]] = {}
ALL_VOICES = {voice for choices in VOICES.values() for voice in choices.values()}


def _client(key: str, settings: Settings) -> OpenAI:
    return OpenAI(api_key=key, base_url=f"{settings.api_base_url}/v1", timeout=90.0, max_retries=0)


def _path_ref(path: str, *, allow_dir: bool = False) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.exists() or (not p.is_file() and not (allow_dir and p.is_dir())):
        raise ToolError(f"File not found: {path}", "file_not_found")
    return p


def _validate_lang(lang: str, supported: set[str], model: str) -> None:
    if lang not in supported:
        raise ToolError(f"Unsupported language code '{lang}' for {model}.", "unsupported_language")


def _natural_path_key(path: Path) -> list[Any]:
    import re
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def _retry_sdk(call, *, attempts: int = 3):
    for index in range(attempts):
        try:
            with _API_SLOTS:
                return call()
        except APIStatusError as exc:
            status = exc.status_code
            retry_after = exc.response.headers.get("retry-after") if exc.response else None
            retryable = status in (500, 502, 503, 504) or (status == 429 and retry_after is not None)
            if not retryable or index == attempts - 1:
                raise ToolError(f"Bodhan API returned HTTP {status}: {exc.message}", "bodhan_api_error") from exc
            try:
                delay = min(float(retry_after), 20.0) if retry_after else min(2 ** index, 8)
            except ValueError:
                delay = min(2 ** index, 8)
            time.sleep(delay)
        except (APITimeoutError, APIConnectionError) as exc:
            if index == attempts - 1:
                raise ToolError(f"Bodhan API request failed after {attempts} attempts: {exc}", "network_error") from exc
            time.sleep(min(2 ** index, 8))


def _translation_chunks(text: str, limit: int = MAX_TRANSLATE_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    import re
    # Keep blank-line-delimited Markdown blocks together whenever possible.
    blocks = re.split(r"(\n\s*\n)", text)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) <= limit:
            current += block
            continue
        if current.strip():
            chunks.append(current)
            current = ""
        if len(block) <= limit:
            current = block
            continue
        # A single oversized block is cut at sentence/whitespace boundaries.
        rest = block
        while len(rest) > limit:
            markers = (". ", "? ", "! ", "; ", " ")
            position, marker = max(((rest.rfind(mark, 0, limit - len(mark) + 1), mark) for mark in markers), key=lambda item: item[0])
            split_at = position + len(marker) if position >= limit // 2 else limit
            chunks.append(rest[:split_at])
            rest = rest[split_at:]
        current = rest
    if current:
        chunks.append(current)
    return [part for part in chunks if part]


def translate_text(text: str, target_language: str, settings: Settings, *, script: str = "native") -> dict[str, Any]:
    _validate_lang(target_language, LANGS, "Indic-Translate")
    if not text.strip():
        raise ToolError("text must not be empty.", "invalid_input")
    if script not in {"native", "roman", "codemix"}:
        raise ToolError("script must be native, roman, or codemix.", "invalid_input")
    if target_language == "en" and script == "roman":
        raise ToolError("Indic-Translate does not accept roman script for English.", "invalid_input")
    if target_language in {"en", "ks-Deva", "sd-Arab"} and script == "codemix":
        raise ToolError(f"Indic-Translate does not support codemix output for {target_language}.", "invalid_input")
    client = _client(settings.translate_key, settings)
    translations: list[str] = []
    total_tokens = 0
    translated_pieces: dict[str, str] = {}
    source_chunks = _translation_chunks(text)
    for piece in source_chunks:
        if piece in translated_pieces:
            translations.append(translated_pieces[piece])
            continue
        response = _retry_sdk(lambda: client.chat.completions.create(
            model="indic-translate",
            messages=[{"role": "user", "content": piece}],
            extra_body={"target_language_code": target_language, "script": script},
        ))
        translated = response.choices[0].message.content
        if not isinstance(translated, str) or not translated.strip():
            raise ToolError("Translation API returned no translation text.", "invalid_response")
        response_data = response.model_dump() if hasattr(response, "model_dump") else {}
        truncated = bool(response_data.get("truncated")) or response.choices[0].finish_reason == "length"
        if truncated:
            raise ToolError("Translation was truncated by the API. Split the input into smaller sections and retry.", "translation_truncated")
        translations.append(translated)
        translated_pieces[piece] = translated
        usage = response.usage
        total_tokens += int((usage.completion_tokens if usage else None) or max(1, len(translated) // 3))
    cost = total_tokens / 10000 * PRICES["translate_per_10k_tokens"]
    joined = "\n\n".join(translations)
    import re
    numeric_source = re.sub(r"\bnoon\b", "12", text, flags=re.IGNORECASE)
    source_numbers = re.findall(r"\d+(?:[.,]\d+)*", numeric_source)
    translated_numbers = re.findall(r"\d+(?:[.,]\d+)*", joined)
    def normalize_digits(token: str) -> str:
        return "".join(
            str(unicodedata.digit(char)) if char.isdecimal() else char for char in token
        )

    html_table_tags = ("table", "thead", "tbody", "tr", "th", "td")
    source_html_structure = [
        (tag, len(re.findall(fr"</?{tag}\b", text, flags=re.IGNORECASE)))
        for tag in html_table_tags
    ]
    translated_html_structure = [
        (tag, len(re.findall(fr"</?{tag}\b", joined, flags=re.IGNORECASE)))
        for tag in html_table_tags
    ]

    checks = {
        "nonempty": bool(joined.strip()),
        "numbers_preserved": [normalize_digits(value) for value in source_numbers]
        == [normalize_digits(value) for value in translated_numbers],
        "markdown_heading_count_preserved": len(re.findall(r"(?m)^#{1,6} ", text)) == len(re.findall(r"(?m)^#{1,6} ", joined)),
        "table_row_count_preserved": sum("|" in line for line in text.splitlines()) == sum("|" in line for line in joined.splitlines()),
        "html_table_structure_preserved": source_html_structure == translated_html_structure,
        "latex_delimiter_count_preserved": sum(text.count(token) for token in ("$$", "$", r"\(", r"\)", r"\[", r"\]")) == sum(joined.count(token) for token in ("$$", "$", r"\(", r"\)", r"\[", r"\]")),
    }
    return {"translation": joined, "target_language": target_language, "script": script, "chunks": len(translations), "output_tokens": total_tokens, "cost_inr": cost, "verification_pairs": [{"source": source_piece, "translation": translated_piece} for source_piece, translated_piece in zip(source_chunks, translations)], "verification": {"checks": checks, "passed": all(checks.values()), "semantic_coverage_review_required": True}}


def ocr_image(file_ref: str, settings: Settings) -> dict[str, Any]:
    path = _path_ref(file_ref)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ToolError("Indic-OCR accepts PNG or JPEG page images. Convert PDFs to PNG pages first.", "unsupported_file_type")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    client = _client(settings.ocr_key, settings)
    try:
        result = _retry_sdk(lambda: client.chat.completions.create(
            model="indic-ocr",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]}],
        ))
    except Exception as exc:
        raise ToolError(f"Indic-OCR request failed: {exc}", "bodhan_api_error") from exc
    content = result.choices[0].message.content or ""
    if not content.strip():
        raise ToolError("Indic-OCR returned no text.", "empty_result")
    response_data = result.model_dump() if hasattr(result, "model_dump") else {}
    blocks = response_data.get("blocks", []) if isinstance(response_data, dict) else []
    return {"text": content, "blocks": blocks, "artifact_ref": str(path), "pages": 1, "cost_inr": PRICES["ocr_per_page"]}


def ocr_document(file_ref: str, settings: Settings) -> dict[str, Any]:
    """OCR one image, a folder of page images, or each page of a PDF."""
    path = _path_ref(file_ref, allow_dir=True)
    if path.is_dir():
        page_paths = sorted((p for p in path.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}), key=_natural_path_key)
        if not page_paths:
            raise ToolError("The folder contains no supported page images.", "empty_document")
        page_results = [ocr_image(str(page), settings) for page in page_paths]
        return {"text": "\n\n".join(f"<!-- Page {i + 1} -->\n{page['text']}" for i, page in enumerate(page_results)), "pages": len(page_results), "page_results": page_results, "artifact_ref": str(path), "cost_inr": sum(page["cost_inr"] for page in page_results)}
    if path.suffix.lower() == ".pdf":
        if not shutil_which("pdfinfo") or not shutil_which("pdftoppm"):
            raise ToolError("PDF OCR requires Poppler tools pdfinfo and pdftoppm. Install Poppler and retry.", "media_dependency_missing")
        try:
            meta = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True, timeout=20).stdout
            pages_line = next((line for line in meta.splitlines() if line.startswith("Pages:")), "")
            page_count = int(pages_line.split(":", 1)[1].strip())
        except (subprocess.SubprocessError, ValueError, StopIteration) as exc:
            raise ToolError("Could not read PDF page count with pdfinfo.", "invalid_document") from exc
        if page_count > 200:
            raise ToolError(f"PDF has {page_count} pages; the current tool limit is 200 pages per call.", "document_too_large")
        with tempfile.TemporaryDirectory(prefix="bodhan-ocr-") as temp:
            prefix = str(Path(temp) / "page")
            try:
                subprocess.run(["pdftoppm", "-png", "-r", "150", str(path), prefix], capture_output=True, check=True, timeout=max(60, page_count * 8))
            except (subprocess.SubprocessError, OSError) as exc:
                raise ToolError(f"Could not render PDF pages: {exc}", "pdf_render_error") from exc
            pages = sorted(Path(temp).glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
            page_results = [ocr_image(str(page), settings) for page in pages]
    else:
        return ocr_image(str(path), settings)
    return {"text": "\n\n".join(f"<!-- Page {i + 1} -->\n{page['text']}" for i, page in enumerate(page_results)), "pages": len(page_results), "page_results": page_results, "artifact_ref": str(path), "cost_inr": sum(page["cost_inr"] for page in page_results)}


def _duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            sample_rate = audio.getframerate()
            if sample_rate > 0:
                return audio.getnframes() / sample_rate
    except (wave.Error, OSError, EOFError):
        pass
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, check=True, timeout=20)
        return float(proc.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        raise ToolError("Could not read audio duration. Install ffmpeg/ffprobe and ensure it is on PATH.", "media_dependency_missing") from exc


def _audio_chunks(path: Path, chunk_seconds: int = 27) -> list[tuple[Path, float]]:
    duration = _duration_seconds(path)
    if duration <= 30:
        return [(path, 0.0)]
    temp_dir = Path(tempfile.mkdtemp(prefix="bodhan-audio-"))
    if path.suffix.lower() == ".wav":
        try:
            parts: list[tuple[Path, float]] = []
            with wave.open(str(path), "rb") as source:
                params = source.getparams()
                if params.comptype != "NONE":
                    raise wave.Error("compressed WAV is not supported by the standard-library splitter")
                frames_per_chunk = max(1, int(chunk_seconds * params.framerate))
                start_frame = 0
                index = 0
                while True:
                    frames = source.readframes(frames_per_chunk)
                    if not frames:
                        break
                    part = temp_dir / f"part-{index:04d}.wav"
                    with wave.open(str(part), "wb") as output:
                        output.setparams(params)
                        output.writeframes(frames)
                    parts.append((part, start_frame / params.framerate))
                    start_frame += len(frames) // (params.nchannels * params.sampwidth)
                    index += 1
            return parts
        except (wave.Error, OSError, EOFError):
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir = Path(tempfile.mkdtemp(prefix="bodhan-audio-"))
    if not shutil_which("ffmpeg"):
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise ToolError("Audio exceeds 30 seconds; install ffmpeg to chunk this format before transcription.", "media_dependency_missing")
    parts: list[tuple[Path, float]] = []
    start = 0.0
    index = 0
    while start < duration:
        part = temp_dir / f"part-{index:04d}.wav"
        length = min(float(chunk_seconds), duration - start)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(length), "-i", str(path), "-ac", "1", "-ar", "24000", "-y", str(part)], check=True, capture_output=True, timeout=90)
        parts.append((part, start))
        start += chunk_seconds
        index += 1
    return parts


def shutil_which(command: str) -> str | None:
    import shutil
    return shutil.which(command)


def _estimated_segments(text: str, start: float, end: float) -> list[dict[str, Any]]:
    import re
    sentences = [part.strip() for part in re.split(r"(?<=[.!?।])\s+|\n+", text) if part.strip()]
    if not sentences:
        return []
    weights = [max(1, len(sentence)) for sentence in sentences]
    total_weight = sum(weights)
    cursor = start
    result: list[dict[str, Any]] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        segment_end = end if index == len(sentences) - 1 else cursor + (end - start) * weight / total_weight
        result.append({"start": round(cursor, 3), "end": round(segment_end, 3), "text": sentence, "timing_estimated": True})
        cursor = segment_end
    return result


def transcribe_audio(file_ref: str, settings: Settings, *, language: str | None = None) -> dict[str, Any]:
    path = _path_ref(file_ref)
    if language:
        _validate_lang(language, TRANSCRIBE_LANGS, "Indic-Transcribe")
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    cache_key = hashlib.sha256(f"indic-transcribe:{language or 'auto'}:{digest.hexdigest()}".encode()).hexdigest()
    if cache_key in _TRANSCRIBE_CACHE:
        cached = dict(_TRANSCRIBE_CACHE[cache_key])
        cached["cost_inr"] = 0.0
        cached["cached"] = True
        cached["artifact_ref"] = str(path)
        return cached
    chunks = _audio_chunks(path)
    full_text: list[str] = []
    all_segments: list[dict[str, Any]] = []
    duration = _duration_seconds(path)
    client = _client(settings.transcribe_key, settings)
    for chunk_path, offset in chunks:
        chunk_duration = _duration_seconds(chunk_path)
        try:
            with chunk_path.open("rb") as audio:
                def transcribe():
                    audio.seek(0)
                    return client.audio.transcriptions.create(model="indic-transcribe", file=audio, language=language)
                result = _retry_sdk(transcribe)
            payload = result.model_dump() if hasattr(result, "model_dump") else result
        except Exception as exc:
            raise ToolError(f"Indic-Transcribe failed for audio chunk at {offset:.2f}s: {exc}", "bodhan_api_error") from exc
        text = payload.get("text", "") if isinstance(payload, dict) else getattr(result, "text", "")
        if text:
            full_text.append(text)
        segments = payload.get("segments", []) if isinstance(payload, dict) else []
        if segments:
            for segment in segments:
                all_segments.append({"start": float(segment["start"]) + offset, "end": float(segment["end"]) + offset, "text": segment["text"], "timing_estimated": False})
        elif text:
            all_segments.extend(_estimated_segments(text, offset, min(offset + chunk_duration, duration)))
    temporary_dirs = {chunk_path.parent for chunk_path, _ in chunks if chunk_path.name.startswith("part-") and chunk_path.parent.name.startswith("bodhan-audio-")}
    for temp_dir in temporary_dirs:
        shutil.rmtree(temp_dir, ignore_errors=True)
    cost = duration / 60 * PRICES["transcribe_per_minute"]
    result = {"text": " ".join(full_text).strip(), "segments": all_segments, "duration_seconds": duration, "artifact_ref": str(path), "cost_inr": cost}
    _TRANSCRIBE_CACHE[cache_key] = dict(result)
    return result


def _split_text(text: str, max_chars: int = MAX_TTS_CHARS) -> list[str]:
    import re
    clean = text.strip()
    if not clean:
        raise ToolError("text must not be empty.", "invalid_input")
    chunks: list[str] = []
    while len(clean) > max_chars:
        markers = (". ", "? ", "! ", "\n", " ")
        position, marker = max(((clean.rfind(mark, 0, max_chars - len(mark) + 1), mark) for mark in markers), key=lambda item: item[0])
        cut = position + len(marker) if position >= max_chars // 2 else max_chars
        chunks.append(clean[:cut].strip())
        clean = clean[cut:].strip()
    chunks.append(clean)
    return [re.sub(r"\s+", " ", chunk) for chunk in chunks if chunk]


def _normalized_for_cer(text: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFC", text).casefold() if not ch.isspace() and not unicodedata.category(ch).startswith("P"))


def _character_error_rate(reference: str, hypothesis: str) -> float:
    left, right = _normalized_for_cer(reference), _normalized_for_cer(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] / len(left)


def _join_wav_parts(audio_parts: list[bytes], output_path: Path) -> None:
    if not audio_parts:
        raise ToolError("Indic-Speak returned no audio chunks.", "empty_result")
    reference_format = None
    try:
        with wave.open(str(output_path), "wb") as merged:
            for part in audio_parts:
                with wave.open(io.BytesIO(part), "rb") as source:
                    params = source.getparams()
                    audio_format = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
                    if reference_format is None:
                        reference_format = audio_format
                        merged.setparams(params)
                    elif audio_format != reference_format:
                        raise ToolError("Indic-Speak returned WAV chunks with incompatible audio formats.", "invalid_response")
                    merged.writeframes(source.readframes(source.getnframes()))
    except (wave.Error, OSError, EOFError) as exc:
        output_path.unlink(missing_ok=True)
        raise ToolError("Indic-Speak returned invalid WAV audio.", "invalid_response") from exc
    except ToolError:
        output_path.unlink(missing_ok=True)
        raise


def synthesize_speech(text: str, language: str, voice: str, output_path: str, settings: Settings, *, style: str | None = None) -> dict[str, Any]:
    _validate_lang(language, set(VOICES), "Indic-Speak")
    if voice not in ALL_VOICES:
        raise ToolError(f"Voice '{voice}' is not available. Choose one of {', '.join(sorted(ALL_VOICES))}.", "unsupported_voice")
    chunks = _split_text(text)
    dest = Path(output_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    client = _client(settings.speak_key, settings)
    audio_parts: list[bytes] = []
    synthesized_parts: dict[str, bytes] = {}
    billed_characters = 0
    for part in chunks:
        part_key = hashlib.sha256(json.dumps([part, language, voice, style], ensure_ascii=False).encode()).hexdigest()
        if part_key in synthesized_parts:
            audio_parts.append(synthesized_parts[part_key])
            continue
        instructions = json.dumps({"lang": language, **({"style": style} if style else {})}, ensure_ascii=False)
        try:
            response = _retry_sdk(lambda: client.audio.speech.create(model="indic-speak", voice=voice, input=part, instructions=instructions, response_format="wav"))
            part_audio = response.read()
            audio_parts.append(part_audio)
            synthesized_parts[part_key] = part_audio
            billed_characters += len(part)
        except Exception as exc:
            raise ToolError(f"Indic-Speak failed: {exc}", "bodhan_api_error") from exc
    _join_wav_parts(audio_parts, dest)
    speak_cost = billed_characters / 10000 * PRICES["speak_per_10k_chars"]
    backcheck = transcribe_audio(str(dest), settings, language=language)
    cer = _character_error_rate(text, backcheck["text"])
    return {"artifact_ref": str(dest), "chunks": len(chunks), "characters": len(text), "tts_cost_inr": speak_cost, "verification_cost_inr": backcheck["cost_inr"], "cost_inr": speak_cost + backcheck["cost_inr"], "ai_generated": True, "verification": {"method": "Indic-Transcribe back-transcription", "transcript": backcheck["text"], "character_error_rate": round(cer, 4), "passed": cer <= 0.25}}
