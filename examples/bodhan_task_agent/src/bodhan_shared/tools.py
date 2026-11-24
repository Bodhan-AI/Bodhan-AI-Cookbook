"""Typed MCP tools, shared by independent stdio server and task agent.

Only Bodhan APIs may perform OCR, transcription, translation or speech synthesis.
The orchestration LLM never receives audio/image base64 and must not generate Indic text.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .errors import TaskError
from .gateway import Budget, Gateway
from .languages import FEMALE_VOICES, LANGUAGES, MALE_VOICES, infer_script_lang, normalize_lang
from .media import audio_chunks, combine_wavs, command, duration, make_srt, render_pdf, split_speech
from .store import Workspace


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def prop(t: str, desc: str, **more: Any) -> dict:
    return {"type": t, "description": desc, **more}


TOOL_SCHEMAS: list[dict] = [
    {"name": "inspect_files", "description": "List available input and generated files (paths, sizes, media types). Use this before planning file operations.",
     "inputSchema": obj({"folder": prop("string", "Relative folder within the workspace, default inputs")})},
    {"name": "read_text", "description": "Read English/source text, previous Bodhan output, JSON, or Markdown via a file reference; never read binary media.",
     "inputSchema": obj({"path": prop("string", "Existing text/JSON/Markdown file reference"),
                          "max_chars": prop("integer", "Preview length (default 16000)", minimum=100, maximum=30000)}, ["path"])},
    {"name": "ocr_document", "description": "Bodhan Indic-OCR: extract printed text and Markdown layout from image or scanned PDF. Renders PDF to images; never uses embedded PDF text. Saves raw blocks and extracted Markdown to file refs.",
     "inputSchema": obj({"input_path": prop("string", "PNG/JPEG image, PDF or folder of page images"),
                          "output_path": prop("string", "Optional Markdown output file reference; defaults to artifacts/<name>.ocr.md")}, ["input_path"])},
    {"name": "transcribe_audio", "description": "Bodhan Indic-Transcribe: transcribe audio/video by <=25-second windows; returns transcript, coarse chunk timestamps and language hints. Saves segment JSON and text. This is not word-level alignment.",
     "inputSchema": obj({"input_path": prop("string", "Audio or video file"),
                          "language_hint": prop("string", "Known input language ISO code; omit to auto-detect")}, ["input_path"])},
    {"name": "translate_text", "description": "Bodhan Indic-Translate ONLY. Translate text or an existing file to a supported Indian language or English. Preserves Markdown table syntax, numbers and LaTeX where possible. Writes translated Markdown and a QA report. For Indian-to-Indian, use an English pivot when needed.",
     "inputSchema": obj({"target_language": prop("string", "ISO language code, e.g. ta, hi, bn, mr, en"),
                          "text": prop("string", "Text to translate, if no input_path"),
                          "input_path": prop("string", "Existing source file; preferred for long content"),
                          "output_path": prop("string", "Optional output file, default artifacts/translated.<lang>.md")}, ["target_language"])},
    {"name": "translate_segments", "description": "Translate time-stamped Bodhan transcription segments into another language using Indic-Translate and save segment JSON preserving timestamps. Useful for subtitles.",
     "inputSchema": obj({"segments_path": prop("string", "JSON file containing segments with start/end/text"),
                          "target_language": prop("string", "Target ISO language code")}, ["segments_path", "target_language"])},
    {"name": "speak_text", "description": "Bodhan Indic-Speak: generate AI-labelled speech from existing translated text file, or direct English text. Long content is split into short segments; speech is checked by Bodhan back-transcription. Choose gender or a valid library voice; produces WAV/MP3.",
     "inputSchema": obj({"language": prop("string", "ISO language code"),
                          "input_path": prop("string", "Text/Markdown file (required for Indic-script input)"),
                          "text": prop("string", "English/plain text; do not generate Indian-language text with the LLM"),
                          "gender": prop("string", "Voice gender, female or male", enum=["male", "female"]),
                          "voice": prop("string", "Valid fixed-library voice, overrides gender"),
                          "style": prop("string", "Optional published Indic-Speak style"),
                          "output_path": prop("string", "Destination WAV/MP3 file")}, ["language"])},
    {"name": "create_subtitles", "description": "Create a standard SRT file from time-stamped segment JSON. No model calls; preserves chunk timestamps and translated text.",
     "inputSchema": obj({"segments_path": prop("string", "Existing segments JSON file"),
                          "output_path": prop("string", "Destination .srt file, defaults artifacts/subtitles.srt")}, ["segments_path"])},
    {"name": "save_english_text", "description": "Write an English-only summary, English notes or planning text to a Markdown/TXT file. NEVER create or rewrite Indic-language content with this tool; use Indic-Translate instead.",
     "inputSchema": obj({"path": prop("string", "Output .md or .txt file reference"),
                          "text": prop("string", "LLM-written ENGLISH text only")}, ["path", "text"])},
]

SCRIPT_PATTERN = re.compile(r"[\u0900-\u0D7F\u0F00-\u0FFF]")
PROTECTED = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]|(?<!\w)\d+(?:[.,:/-]\d+)*(?!\w)")
MARKDOWN_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*))")


def extract_protected(text: str) -> list[str]:
    return PROTECTED.findall(text)


def canonical_numbers(text: str) -> list[str]:
    return ["".join(str(unicodedata.digit(ch)) if ch.isdigit() and ch.isdecimal() else ch
                    for ch in v) for v in extract_protected(text) if v[:1].isdecimal()]


def has_indic_script(text: str) -> bool:
    return SCRIPT_PATTERN.search(text) is not None


def ratio(a: str, b: str) -> float:
    norm = lambda x: " ".join(unicodedata.normalize("NFKC", x).casefold().split())
    return round(SequenceMatcher(None, norm(a), norm(b)).ratio(), 3)


class ToolHub:
    def __init__(self, root: str | Path, maximum_cost_inr: float = 20.0,
                 gateway: Gateway | None = None):
        self.store = Workspace(root)
        self.budget = Budget(maximum_cost_inr)
        self.gateway = gateway or Gateway(self.store, self.budget)
        self.history: list[dict] = []

    @property
    def names(self) -> set[str]:
        return {schema["name"] for schema in TOOL_SCHEMAS}

    def call(self, name: str, args: dict) -> dict:
        started = time.monotonic()
        if name not in self.names:
            return {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": name, "retryable": False}}
        schema = next(x["inputSchema"] for x in TOOL_SCHEMAS if x["name"] == name)
        try:
            if not isinstance(args, dict):
                raise TaskError("INVALID_ARGUMENTS", "Tool arguments must be a JSON object")
            for key in schema["required"]:
                if key not in args:
                    raise TaskError("INVALID_ARGUMENTS", f"Missing required argument: {key}")
            for key, value in args.items():
                expected = schema["properties"].get(key)
                if expected is None:
                    raise TaskError("INVALID_ARGUMENTS", f"Unexpected argument: {key}")
                if not isinstance(value, {"string": str, "integer": int}[expected["type"]]):
                    raise TaskError("INVALID_ARGUMENTS", f"{key}: expected {expected['type']}")
                if "enum" in expected and value not in expected["enum"]:
                    raise TaskError("INVALID_ARGUMENTS", f"{key}: allowed values {expected['enum']}")
            result = getattr(self, name)(**args)
            output = {"ok": True, **result}
        except TaskError as exc:
            output = {"ok": False, "error": exc.as_dict()}
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            output = {"ok": False, "error": TaskError("INVALID_INPUT", str(exc)[:300]).as_dict()}
        except Exception:
            output = {"ok": False, "error": TaskError("INTERNAL_ERROR", "Unexpected tool failure; inspect server logs").as_dict()}
        elapsed = round((time.monotonic() - started) * 1000)
        self.history.append({"tool": name, "latency_ms": elapsed, "ok": output["ok"],
                             "estimated_total_inr": round(self.budget.spent, 4)})
        return output

    def inspect_files(self, folder: str = "inputs") -> dict:
        path = self.store.path(folder)
        if path.is_file():
            files = [path]
        else:
            files = sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink())[:100]
        return {"files": [{"path": str(f.relative_to(self.store.root)),
                           "bytes": f.stat().st_size, "extension": f.suffix.lower()}
                          for f in files], "count": len(files)}

    def read_text(self, path: str, max_chars: int = 16000) -> dict:
        if not 100 <= max_chars <= 30000:
            raise TaskError("INVALID_ARGUMENTS", "max_chars must be within 100–30000")
        p = self.store.path(path)
        if p.suffix.lower() not in {".txt", ".md", ".json", ".jsonl", ".srt", ".csv"}:
            raise TaskError("UNSUPPORTED_FILE", "Only text files can be read; use model tools for media")
        body = p.read_text(encoding="utf-8")
        return {"text": body[:max_chars], "truncated": len(body) > max_chars,
                "path": str(p.relative_to(self.store.root)), "characters": len(body)}

    def ocr_document(self, input_path: str, output_path: str = "") -> dict:
        source = self.store.path(input_path)
        if not output_path:
            output_path = f"artifacts/{source.stem}.ocr.md"
        dest = self.store.output_path(output_path)
        if source.is_dir():
            pages = sorted(p for p in source.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
        elif source.suffix.lower() == ".pdf":
            pages = render_pdf(source, dest.parent / "_rendered_pages")
        elif source.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            pages = [source]
        else:
            raise TaskError("UNSUPPORTED_FILE", "OCR accepts PDF or PNG/JPEG pages")
        if not pages or len(pages) > 100:
            raise TaskError("INVALID_DOCUMENT", "Expected 1–100 pages")
        results, failures = [], []
        for i, page in enumerate(pages, 1):
            try:
                result, hit = self.gateway.ocr(page)
                text = result["choices"][0]["message"]["content"]
                blocks = result.get("blocks", [])
                page_meta = {"page": i, "source_file": str(page), "text": text,
                             "blocks": blocks, "cached": hit,
                             "low_confidence_blocks": [b for b in blocks
                                                       if float(b.get("conf", 1)) < 0.65]}
                results.append(page_meta)
            except TaskError as exc:
                failures.append({"page": i, "error": exc.as_dict()})
                if exc.code in {"CONFIG_MISSING", "BUDGET_EXCEEDED"}:
                    break
        output = "\n\n---\n\n".join(p["text"] for p in results)
        md = self.store.write(str(dest), output)
        meta = self.store.write(str(dest.with_suffix(".pages.json")),
                                json.dumps({"pages": results, "failures": failures}, ensure_ascii=False, indent=2))
        if not results:
            raise TaskError("OCR_FAILED", "No pages were successfully extracted")
        return {"text": output[:16000], "truncated": len(output) > 16000,
                "output_path": md, "pages_path": meta,
                "pages_ok": len(results), "pages_failed": len(failures), "failures": failures,
                "needs_review": sum(len(x["low_confidence_blocks"]) for x in results) > 0}

    def transcribe_audio(self, input_path: str, language_hint: str = "") -> dict:
        source = self.store.path(input_path)
        if not source.is_file():
            raise TaskError("INVALID_INPUT", "Transcription requires one file")
        if language_hint:
            language_hint = normalize_lang(language_hint)
        file_id = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:7]
        out_dir = self.store.root / "artifacts" / (source.stem + "_asr_" + file_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        chunks = audio_chunks(source, out_dir)
        segments = []
        for part in chunks:
            resp, cached = self.gateway.transcribe(Path(part["path"]), language_hint or None,
                                                   part["seconds"])
            text = resp.get("text", "").strip()
            segments.append({"start": part["start"], "end": part["end"], "text": text,
                             "language": resp.get("language") or infer_script_lang(text), "cached": cached})
        transcript = " ".join(seg["text"] for seg in segments if seg["text"])
        segments_path = self.store.write(f"artifacts/{source.stem}.{file_id}.segments.json",
                                         json.dumps({"segments": segments}, ensure_ascii=False, indent=2))
        text_path = self.store.write(f"artifacts/{source.stem}.{file_id}.transcript.txt", transcript)
        detected = next((s["language"] for s in segments if s.get("language")), None)
        return {"transcript": transcript[:14000], "truncated": len(transcript) > 14000,
                "segments_path": segments_path, "text_path": text_path,
                "language": language_hint or detected,
                "language_confidence": "script-based heuristic or provider metadata" if detected else "unknown",
                "total_seconds": round(duration(source), 2), "segment_count": len(segments)}

    def _translate_one(self, source: str, lang: str) -> str:
        translated, _ = self.gateway.translate(source, lang)
        # Fail closed on non-empty input with empty response.
        if source.strip() and not translated.strip():
            raise TaskError("EMPTY_TRANSLATION", "Translation returned empty text")
        return translated

    @staticmethod
    def _document_structure_matches(original: str, result: str) -> bool:
        """Guard the fast path against silently losing tables, math, or quantities.

        No external LLM is used to repair target-language text. A failed guard
        triggers conservative segment translation with Indic-Translate instead.
        """
        if not result.strip() or extract_protected(original) != extract_protected(result):
            return False

        def rows(text: str) -> list[tuple[int, bool]]:
            found = []
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("|") and line.endswith("|"):
                    count = len(line.strip("|").split("|"))
                    separator = bool(re.fullmatch(r"[|\s:-]+", line))
                    found.append((count, separator))
            return found

        if rows(original) != rows(result):
            return False
        # The model can reflow prose, but cannot drop list items or headings.
        def markers(text: str) -> tuple[int, int]:
            lines = text.splitlines()
            headings = sum(bool(re.match(r"^\s*#{1,6}\s+", line)) for line in lines)
            bullets = sum(bool(re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", line))
                          for line in lines)
            return headings, bullets

        return markers(original) == markers(result)

    def _translate_structured(self, source: str, lang: str) -> tuple[str, list[dict]]:
        problems: list[dict] = []
        # One Bodhan request for a whole short document avoids one request per
        # line/cell. Accept it ONLY if structural invariants survive intact.
        # If not, use the fine-grained safety path below.
        if len(source) >= 200 and len(source) <= 4500:
            candidate = self._translate_one(source, lang)
            if self._document_structure_matches(source, candidate):
                return candidate, problems
            problems.append({"type": "document-batch-rejected",
                             "message": "Full-document translation changed protected structure; "
                                        "retrying with conservative segments"})
        output = []
        fence = False
        for n, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("```"):
                fence = not fence
                output.append(line)
                continue
            if fence or not stripped or re.fullmatch(r"[\s|:-]+", stripped):
                output.append(line)
                continue
            if stripped.startswith("$") and stripped.endswith("$"):
                output.append(line)
                continue
            if "|" in line and line.count("|") >= 2:
                cells = line.split("|")
                translated_cells = []
                for cell in cells:
                    translated_cells.append(self._translate_protected(cell, lang, n, problems))
                output.append("|".join(translated_cells))
                continue
            prefix = MARKDOWN_PREFIX.match(line)
            head = prefix.group(1) if prefix else ""
            body = line[len(head):]
            output.append(head + self._translate_protected(body, lang, n, problems))
        return "\n".join(output) + ("\n" if source.endswith("\n") else ""), problems

    def _translate_protected(self, body: str, lang: str, line: int, problems: list[dict]) -> str:
        if not body.strip():
            return body
        # Translate natural text as one unit first; preserve terms/units/equations by check.
        lead = body[:len(body) - len(body.lstrip())]
        tail = body[len(body.rstrip()):]
        clean = body.strip()
        out = self._translate_one(clean, lang)
        if extract_protected(clean) != extract_protected(out):
            # Safety fallback: translate only spans between protected tokens, copying tokens verbatim.
            # This can hurt fluency but never invents a number or mathematical formula.
            pieces = []
            pos = 0
            for match in PROTECTED.finditer(clean):
                part = clean[pos:match.start()]
                pieces.append(self._translate_one(part, lang) if part.strip() else part)
                pieces.append(match.group(0))
                pos = match.end()
            last = clean[pos:]
            pieces.append(self._translate_one(last, lang) if last.strip() else last)
            out = "".join(pieces)
            problems.append({"line": line, "type": "protected-token-regenerated",
                             "message": "Original translation altered a number or math token; fallback copied it"})
        return lead + out + tail

    def translate_text(self, target_language: str, text: str = "", input_path: str = "",
                       output_path: str = "") -> dict:
        lang = normalize_lang(target_language)
        if bool(text) == bool(input_path):
            raise TaskError("INVALID_ARGUMENTS", "Provide exactly one of text or input_path")
        source = self.read_text(input_path, max_chars=30000)["text"] if input_path else text
        if input_path:
            source = self.store.path(input_path).read_text(encoding="utf-8")
        if not source.strip():
            raise TaskError("EMPTY_TEXT", "Nothing to translate")
        if len(source) > 100_000:
            raise TaskError("INPUT_TOO_LONG", "Input exceeds 100K characters; split first")
        # Indian-to-Indian translation is English-pivot according to take-home instructions.
        original = source
        pivoted = False
        if has_indic_script(source) and lang != "en":
            source, _ = self._translate_structured(source, "en")
            pivoted = True
        result, warnings = self._translate_structured(source, lang)
        input_tag = Path(input_path).stem if input_path else hashlib.sha256(source.encode()).hexdigest()[:7]
        final_path = output_path or f"artifacts/translated.{input_tag}.{lang}.md"
        saved = self.store.write(final_path, result)
        number_match = canonical_numbers(original) == canonical_numbers(result)
        source_math = [x for x in extract_protected(original) if x.startswith("$") or x.startswith("\\")]
        target_math = [x for x in extract_protected(result) if x.startswith("$") or x.startswith("\\")]
        math_match = source_math == target_math
        # Round-trip is a diagnostic, not a ground-truth judgment.
        backtranslation = ""
        if lang != "en" and len(result) <= 4500:
            try:
                backtranslation, _ = self.gateway.translate(result, "en")
            except TaskError as exc:
                warnings.append({"type": "backtranslation-failed", "error": exc.code})
        summary = {"numbers_preserved": number_match, "math_preserved": math_match,
                   "roundtrip_similarity": ratio(source, backtranslation) if backtranslation else None,
                   "warnings": warnings, "english_pivot": pivoted}
        self.store.write(str(Path(saved).with_suffix(".qa.json")),
                         json.dumps(summary, ensure_ascii=False, indent=2))
        return {"output_path": saved, "translation": result[:14000],
                "truncated": len(result) > 14000, "target_language": lang, "qa": summary}

    def translate_segments(self, segments_path: str, target_language: str) -> dict:
        lang = normalize_lang(target_language)
        segments_data = json.loads(self.store.path(segments_path).read_text(encoding="utf-8"))
        segments = segments_data.get("segments", []) if isinstance(segments_data, dict) else segments_data
        if not isinstance(segments, list) or len(segments) > 1000:
            raise TaskError("INVALID_SEGMENTS", "Expected an array of at most 1000 segments")
        translated = []
        for part in segments:
            text = str(part.get("text", ""))
            target = self._translate_one(text, lang) if text.strip() else ""
            translated.append({"start": part["start"], "end": part["end"], "text": target,
                               "source_text": text})
        identifier = Path(segments_path).stem
        dest = self.store.write(f"artifacts/{identifier}.{lang}.segments.json",
                                json.dumps({"segments": translated}, ensure_ascii=False, indent=2))
        return {"segments_path": dest, "segments": len(translated), "language": lang,
                "warnings": ["Timing is at coarse ASR chunk boundaries, not word-level"]}

    def speak_text(self, language: str, input_path: str = "", text: str = "", gender: str = "female",
                   voice: str = "", style: str = "", output_path: str = "") -> dict:
        lang = normalize_lang(language)
        if bool(text) == bool(input_path):
            raise TaskError("INVALID_ARGUMENTS", "Provide exactly one of text or input_path")
        if input_path:
            source_path = self.store.path(input_path)
            if source_path.suffix.lower() not in {".txt", ".md"}:
                raise TaskError("UNSAFE_TEXT_SOURCE", "Speech input_path must be a .txt or .md file")
            source = source_path.read_text(encoding="utf-8")
        else:
            source = text
            if has_indic_script(source):
                raise TaskError("MODEL_POLICY", "Indian-language speech must use text from an existing Bodhan-generated file")
        # Remove Markdown control formatting, but do not translate or rewrite Indic words.
        source = re.sub(r"[`*#>|]", " ", source).strip()
        if not source:
            raise TaskError("EMPTY_TEXT", "Nothing to synthesize")
        if len(source) > 30_000:
            raise TaskError("INPUT_TOO_LONG", "TTS input exceeds 30K characters")
        if gender not in {"male", "female"}:
            raise TaskError("INVALID_VOICE", "gender must be male or female")
        allowed_voices = {FEMALE_VOICES[lang], MALE_VOICES[lang]}
        voice = voice or (FEMALE_VOICES if gender == "female" else MALE_VOICES)[lang]
        if voice not in allowed_voices:
            raise TaskError("INVALID_VOICE", f"Valid {lang} voices: {', '.join(sorted(allowed_voices))}")
        parts = split_speech(source, max_chars=210)
        # Leave room for verification calls; ASR is estimated at <=30s per TTS chunk.
        expected_speak = len(source) * 6.0 / 10_000
        expected_qa = len(parts) * 0.10 * 0.5
        remaining = self.budget.maximum - self.budget.spent
        if remaining + 1e-8 < expected_speak + expected_qa:
            raise TaskError("BUDGET_EXCEEDED", "Insufficient remaining estimated budget for TTS and ASR QA")
        source_tag = hashlib.sha256(source.encode("utf-8")).hexdigest()[:7]
        out = output_path or f"artifacts/speech.{lang}.{source_tag}.wav"
        dest = self.store.output_path(out)
        if dest.suffix.lower() not in {".wav", ".mp3"}:
            raise TaskError("INVALID_AUDIO_FORMAT", "Output must end with .wav or .mp3")
        temp = dest.parent / f"{dest.stem}_parts"
        temp.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, chunk in enumerate(parts):
            blob, _ = self.gateway.speak(chunk, voice=voice, lang=lang, style=style)
            part = temp / f"part_{i:04d}.wav"
            part.write_bytes(blob)
            paths.append(part)
        target_wav = dest if dest.suffix.lower() == ".wav" else dest.with_suffix(".wav")
        combine_wavs(paths, target_wav)
        if dest.suffix.lower() == ".mp3":
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(target_wav),
                     "-codec:a", "libmp3lame", "-qscale:a", "3", str(dest)])
        transcript_path = self.store.write(f"artifacts/speech.{lang}.{source_tag}.script.txt", source)
        # Verify by Bodhan ASR. This is a diagnostic and may itself be inaccurate.
        verified = False
        qa: dict[str, Any] = {"checked": False, "reason": "not attempted"}
        try:
            back = self.transcribe_audio(str(target_wav), language_hint=lang)
            qa = {"checked": True, "character_similarity": ratio(source, back["transcript"]),
                  "reference": transcript_path, "asr_segments_path": back["segments_path"]}
            verified = True
        except TaskError as exc:
            qa = {"checked": False, "reason": exc.code}
        self.store.write(str(dest) + ".qa.json", json.dumps(qa, indent=2))
        return {"output_path": str(dest), "script_path": transcript_path,
                "voice": voice, "language": lang, "duration_seconds": round(duration(dest), 2),
                "parts": len(parts), "ai_generated": True, "verified": verified, "qa": qa}

    def create_subtitles(self, segments_path: str, output_path: str = "artifacts/subtitles.srt") -> dict:
        raw = json.loads(self.store.path(segments_path).read_text(encoding="utf-8"))
        segs = raw.get("segments", []) if isinstance(raw, dict) else raw
        srt = make_srt(segs)
        dest = self.store.write(output_path, srt)
        return {"output_path": dest, "cue_count": len([s for s in segs if s.get("text", "").strip()]),
                "timing_precision": "audio-window-level"}

    def save_english_text(self, path: str, text: str) -> dict:
        if Path(path).suffix.lower() not in {".txt", ".md"}:
            raise TaskError("INVALID_OUTPUT", "English text output must be a .txt or .md file")
        if has_indic_script(text):
            raise TaskError("MODEL_POLICY", "LLM must never author Indian-language output")
        dest = self.store.write(path, text)
        return {"output_path": dest, "characters": len(text)}

# MCP tools have both typed inputs and a common typed success/error output envelope.
# Model-specific result fields remain separately described in each tool's description.
for _tool in TOOL_SCHEMAS:
    _tool["outputSchema"] = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"},
                       "error": {"type": "object", "properties": {
                           "code": {"type": "string"}, "message": {"type": "string"},
                           "retryable": {"type": "boolean"}}, "required": ["code", "message", "retryable"]},
                       "output_path": {"type": "string"},
                       "qa": {"type": "object"}},
        "required": ["ok"], "additionalProperties": True,
    }
