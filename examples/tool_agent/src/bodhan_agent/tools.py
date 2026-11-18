from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .bodhan import ocr_document, synthesize_speech, transcribe_audio, translate_text
from .errors import ToolError
from .settings import Settings

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "transcribe_audio", "description": "Transcribe an audio file using Bodhan Indic-Transcribe. Supports audio files up to 30 seconds per model call; longer audio is chunked internally. The API returns text without timestamps, so sentence timings are estimated and marked as such.", "parameters": {"type": "object", "properties": {"file_ref": {"type": "string", "description": "Local path to the audio file."}, "language": {"type": "string", "description": "Optional ISO language code, e.g. hi, ta, en."}}, "required": ["file_ref"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "identify_language_from_script", "description": "Identify a likely language by the writing system in Bodhan-produced text. Supply candidate language codes. If multiple candidates share the detected script or the text is mixed/too short, return an ambiguous result rather than guessing. This is script identification, not acoustic language recognition.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "candidate_languages": {"type": "array", "items": {"type": "string"}, "description": "Language codes to choose among, e.g. ['hi', 'kn']."}}, "required": ["text", "candidate_languages"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "translate_text", "description": "Translate text with Bodhan Indic-Translate. Input is chunked internally at paragraph boundaries when needed. Use this for every Indian-language translation; do not write target-language text yourself.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "target_language": {"type": "string", "description": "Bodhan language code, e.g. hi, ta, bn."}, "script": {"type": "string", "enum": ["native", "roman", "codemix"]}}, "required": ["text", "target_language"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "translate_segments", "description": "Translate timestamped subtitle segments with Bodhan Indic-Translate while retaining each segment's start and end time.", "parameters": {"type": "object", "properties": {"segments": {"type": "array", "items": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "timing_estimated": {"type": "boolean"}}, "required": ["start", "end", "text"], "additionalProperties": True}}, "target_language": {"type": "string"}, "script": {"type": "string", "enum": ["native", "roman", "codemix"]}}, "required": ["segments", "target_language"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_srt", "description": "Write SubRip subtitles from timestamped segments. Deterministic formatting only; this tool does not translate or invent text.", "parameters": {"type": "object", "properties": {"segments": {"type": "array", "items": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}}, "required": ["start", "end", "text"], "additionalProperties": True}}, "output_path": {"type": "string"}}, "required": ["segments", "output_path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "synthesize_speech", "description": "Generate AI speech using Bodhan Indic-Speak. Short inputs are sent directly; long inputs are split at sentence/paragraph boundaries and joined. Output is WAV and must be labelled AI-generated.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "language": {"type": "string"}, "voice": {"type": "string", "description": "Supported Bodhan voice name for the language."}, "output_path": {"type": "string", "description": "Destination WAV path inside the task output artifacts folder."}, "style": {"type": "string"}}, "required": ["text", "language", "voice", "output_path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "ocr_document", "description": "Extract text with Bodhan Indic-OCR from an image, a folder of page images, or a scanned PDF. PDFs are rendered one page at a time. Returns page-labelled text, page count and cost.", "parameters": {"type": "object", "properties": {"file_ref": {"type": "string", "description": "Path to a page image, a PDF, or a folder of page images."}}, "required": ["file_ref"], "additionalProperties": False}}},
]


def _jsonable(value: Any) -> dict[str, Any]:
    return value


_SCRIPT_LANGUAGES = {
    "Devanagari": (0x0900, 0x097F, {"hi", "mr", "ne", "sa", "mai", "kok", "brx", "doi", "ks-Deva"}),
    "Bengali": (0x0980, 0x09FF, {"bn", "as", "mni-Beng"}),
    "Gujarati": (0x0A80, 0x0AFF, {"gu"}),
    "Gurmukhi": (0x0A00, 0x0A7F, {"pa"}),
    "Odia": (0x0B00, 0x0B7F, {"or"}),
    "Tamil": (0x0B80, 0x0BFF, {"ta"}),
    "Telugu": (0x0C00, 0x0C7F, {"te"}),
    "Kannada": (0x0C80, 0x0CFF, {"kn"}),
    "Malayalam": (0x0D00, 0x0D7F, {"ml"}),
    "Meetei Mayek": (0xABC0, 0xABFF, {"mni"}),
    "Ol Chiki": (0x1C50, 0x1C7F, {"sat"}),
    "Latin": (0x0041, 0x024F, {"en"}),
}


def identify_language_from_script(text: str, candidate_languages: list[str]) -> dict[str, Any]:
    from .bodhan import LANGS

    candidates = list(dict.fromkeys(candidate_languages))
    invalid = sorted(set(candidates) - LANGS)
    if not text.strip():
        raise ToolError("text must not be empty.", "invalid_input")
    if not candidates or invalid:
        raise ToolError(
            f"Provide supported candidate language codes; invalid codes: {', '.join(invalid) or 'none' }.",
            "invalid_input",
        )
    counts = {
        name: sum(start <= ord(char) <= end for char in text)
        for name, (start, end, _) in _SCRIPT_LANGUAGES.items()
    }
    script, count = max(counts.items(), key=lambda item: item[1])
    total = sum(counts.values())
    if count < 3 or total == 0 or count / total < 0.6:
        return {"status": "ambiguous", "language_code": None, "script": None, "candidates": candidates, "script_counts": counts}
    likely = sorted(set(candidates) & _SCRIPT_LANGUAGES[script][2])
    if len(likely) != 1:
        return {"status": "ambiguous", "language_code": None, "script": script, "candidates": likely, "script_counts": counts}
    return {"status": "identified", "language_code": likely[0], "script": script, "candidates": likely, "script_counts": counts}


class ToolExecutor:
    def __init__(self, settings: Settings, artifacts_dir: Path):
        self.settings = settings
        self.artifacts_dir = artifacts_dir.resolve()
        recipe_root = Path(__file__).resolve().parents[2]
        self.cache_dir = recipe_root / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db = self.cache_dir / "tool_cache.sqlite3"
        with sqlite3.connect(self.cache_db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS results (cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL)")

    def _cache_key(self, name: str, arguments: dict[str, Any]) -> str:
        canonical = dict(arguments)
        canonical.pop("output_path", None)
        ref = canonical.get("file_ref")
        if ref:
            path = Path(str(ref)).expanduser().resolve()
            if path.is_file():
                canonical["file_ref"] = self._digest_file(path)
            elif path.is_dir():
                children = [(p.name, self._digest_file(p)) for p in sorted(path.iterdir()) if p.is_file()]
                canonical["file_ref"] = children
        cache_name = f"{name}:v2" if name in {"translate_text", "translate_segments"} else name
        raw = json.dumps([cache_name, canonical], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _digest_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _cached(self, key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.cache_db) as db:
            row = db.execute("SELECT result_json FROM results WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _store(self, key: str, result: dict[str, Any], name: str) -> None:
        record = dict(result)
        if name == "synthesize_speech" and record.get("artifact_ref"):
            source = Path(record["artifact_ref"])
            cached_audio = self.cache_dir / f"{key}.wav"
            shutil.copyfile(source, cached_audio)
            record["cache_audio"] = str(cached_audio)
        with sqlite3.connect(self.cache_db) as db:
            db.execute("INSERT OR REPLACE INTO results(cache_key, result_json) VALUES (?, ?)", (key, json.dumps(record, ensure_ascii=False)))

    def _estimate_cost(self, name: str, arguments: dict[str, Any]) -> float:
        from .bodhan import PRICES, _duration_seconds
        if name == "translate_text":
            return max(1, len(arguments.get("text", ""))) * PRICES["translate_per_10k_tokens"] / 10000
        if name == "translate_segments":
            chars = sum(len(segment.get("text", "")) for segment in arguments.get("segments", []))
            return max(1, chars) * PRICES["translate_per_10k_tokens"] / 10000
        if name == "synthesize_speech":
            chars = len(arguments.get("text", ""))
            estimated_minutes = chars / 10 / 60
            return chars * PRICES["speak_per_10k_chars"] / 10000 + estimated_minutes * PRICES["transcribe_per_minute"]
        if name == "ocr_document":
            path = Path(arguments["file_ref"]).expanduser().resolve()
            if path.is_dir():
                pages = sum(p.suffix.lower() in {".png", ".jpg", ".jpeg"} for p in path.iterdir())
            elif path.suffix.lower() == ".pdf":
                import subprocess
                meta = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True, timeout=20).stdout
                pages = int(next(line for line in meta.splitlines() if line.startswith("Pages:")).split(":", 1)[1])
            else:
                pages = 1
            return pages * PRICES["ocr_per_page"]
        if name == "transcribe_audio":
            return _duration_seconds(Path(arguments["file_ref"]).expanduser().resolve()) / 60 * PRICES["transcribe_per_minute"]
        return 0.0

    def _safe_artifact_path(self, path: str) -> str:
        dest = Path(path).expanduser().resolve()
        try:
            dest.relative_to(self.artifacts_dir)
        except ValueError as exc:
            raise ToolError("output_path must be inside this run's artifacts directory.", "unsafe_output_path") from exc
        return str(dest)

    def call(self, name: str, arguments: dict[str, Any], *, max_cost_inr: float, spent_inr: float) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            cacheable = name in {"transcribe_audio", "translate_text", "translate_segments", "synthesize_speech", "ocr_document"}
            key = self._cache_key(name, arguments) if cacheable else ""
            cached = self._cached(key) if cacheable else None
            if cached is not None:
                if name == "transcribe_audio":
                    cached["artifact_ref"] = str(Path(arguments["file_ref"]).expanduser().resolve())
                if name == "ocr_document":
                    source = Path(arguments["file_ref"]).expanduser().resolve()
                    cached["artifact_ref"] = str(source)
                    page_paths = sorted((p for p in source.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}), key=lambda p: p.name.casefold()) if source.is_dir() else []
                    for index, page in enumerate(cached.get("page_results", []), 1):
                        page["artifact_ref"] = str(page_paths[index - 1]) if source.is_dir() and index <= len(page_paths) else f"{source}#page={index}"
                if name == "synthesize_speech":
                    target = Path(self._safe_artifact_path(arguments["output_path"]))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(cached["cache_audio"], target)
                    cached["artifact_ref"] = str(target)
                cached["cost_inr"] = 0.0
                cached["cached"] = True
                cached["latency_seconds"] = round(time.perf_counter() - start, 3)
                return cached
            estimate = self._estimate_cost(name, arguments)
            if spent_inr + estimate > max_cost_inr + 1e-9:
                raise ToolError(f"Estimated call cost ₹{estimate:.4f} would exceed remaining budget ₹{max(0, max_cost_inr - spent_inr):.4f}.", "budget_exceeded")
            if name == "transcribe_audio":
                result = transcribe_audio(arguments["file_ref"], self.settings, language=arguments.get("language"))
            elif name == "identify_language_from_script":
                result = identify_language_from_script(arguments["text"], arguments["candidate_languages"])
            elif name == "translate_text":
                result = translate_text(arguments["text"], arguments["target_language"], self.settings, script=arguments.get("script", "native"))
            elif name == "translate_segments":
                translated_segments = []
                total_cost = 0.0
                by_text: dict[str, dict[str, Any]] = {}
                for segment in arguments["segments"]:
                    cache_key = segment["text"]
                    translated = by_text.get(cache_key)
                    if translated is None:
                        translated = translate_text(segment["text"], arguments["target_language"], self.settings, script=arguments.get("script", "native"))
                        by_text[cache_key] = translated
                        total_cost += translated["cost_inr"]
                    translated_segments.append({"start": segment["start"], "end": segment["end"], "text": translated["translation"], "source_text": segment["text"], "timing_estimated": segment.get("timing_estimated", False), "verification": translated["verification"]})
                result = {"segments": translated_segments, "target_language": arguments["target_language"], "cost_inr": total_cost}
            elif name == "write_srt":
                output_path = Path(self._safe_artifact_path(arguments["output_path"]))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(_format_srt(arguments["segments"]), encoding="utf-8")
                result = {"artifact_ref": str(output_path), "segments": len(arguments["segments"]), "cost_inr": 0.0}
            elif name == "synthesize_speech":
                output_path = self._safe_artifact_path(arguments["output_path"])
                result = synthesize_speech(arguments["text"], arguments["language"], arguments["voice"], output_path, self.settings, style=arguments.get("style"))
            elif name == "ocr_document":
                result = ocr_document(arguments["file_ref"], self.settings)
            else:
                raise ToolError(f"Unknown tool '{name}'.", "unknown_tool")
            result["latency_seconds"] = round(time.perf_counter() - start, 3)
            if cacheable:
                self._store(key, result, name)
            return _jsonable(result)
        except ToolError:
            raise
        except (KeyError, TypeError) as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc}", "invalid_arguments") from exc
        except Exception as exc:
            raise ToolError(f"Tool {name} failed unexpectedly: {exc}", "internal_tool_error") from exc


def tool_result_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _format_srt(segments: list[dict[str, Any]]) -> str:
    def stamp(value: float) -> str:
        millis = max(0, round(float(value) * 1000))
        hours, rem = divmod(millis, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, rem = divmod(rem, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{rem:03}"

    chunks: list[str] = []
    previous_end = 0.0
    for index, segment in enumerate(segments, 1):
        start, end = float(segment["start"]), float(segment["end"])
        text = str(segment["text"]).strip().replace("\r", "")
        if start < 0 or end <= start or start < previous_end or not text:
            raise ToolError(f"Subtitle segment {index} has invalid times or empty text.", "invalid_segments")
        chunks.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}")
        previous_end = end
    if not chunks:
        raise ToolError("No subtitle segments were provided.", "invalid_segments")
    return "\n\n".join(chunks) + "\n"
