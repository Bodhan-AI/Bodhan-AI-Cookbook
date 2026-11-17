from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium
from PIL import Image
from diskcache import Cache

ROOT = Path(__file__).resolve().parents[3]
RULES = json.loads((ROOT / "scripts" / "bodhan_api_rules.json").read_text())
API = RULES["base_url"]
MODELS = {
    "transcribe": ("indic-transcribe", "BODHAN_TRANSCRIBE_KEY"),
    "translate": ("indic-translate", "BODHAN_TRANSLATE_KEY"),
    "speak": ("indic-speak", "BODHAN_SPEAK_KEY"),
    "ocr": ("indic-ocr", "BODHAN_OCR_KEY"),
}
LANGUAGES = {model: set(RULES["models"][model]["languages"]) for model, _ in MODELS.values()
             if "languages" in RULES["models"][model]}
VOICES = RULES["models"]["indic-speak"]["voices"]
SCRIPT_RANGES = {
    "as": (0x0980, 0x09FF), "bn": (0x0980, 0x09FF),
    "hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F), "sa": (0x0900, 0x097F),
    "brx": (0x0900, 0x097F), "doi": (0x0900, 0x097F), "mai": (0x0900, 0x097F),
    "ne": (0x0900, 0x097F), "ks-Deva": (0x0900, 0x097F), "sd": (0x0900, 0x097F),
    "pa": (0x0A00, 0x0A7F), "gu": (0x0A80, 0x0AFF), "or": (0x0B00, 0x0B7F),
    "ta": (0x0B80, 0x0BFF), "te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF),
    "ml": (0x0D00, 0x0D7F), "mni": (0xABC0, 0xABFF), "sat": (0x1C50, 0x1C7F),
    "ks": (0x0600, 0x06FF), "sd-Arab": (0x0600, 0x06FF), "ur": (0x0600, 0x06FF),
    "mni-Beng": (0x0980, 0x09FF),
}
EXCLUDED_DIRS = {"API", ".git", ".aws", ".codex", ".agents", ".venv", "venv", "__pycache__",
                 ".cache", ".pytest_cache", ".ruff_cache", ".bodhan-cache", "node_modules", "out", "outputs"}
EXCLUDED_FILE_PREFIXES = ("bodhan-api-key", "bodhan_api_key", "api-key", "api_key")


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        result = {"error": self.message, "code": self.code}
        if self.hint:
            result["hint"] = self.hint
        return result


def has_non_latin_letters(text: str) -> bool:
    return any(
        unicodedata.category(char).startswith("L")
        and not unicodedata.name(char, "").startswith("LATIN ")
        for char in text
    )


def safe_name(value: str, fallback: str = "artifact") -> str:
    path = Path(value)
    name = path.stem if path.suffix.lower() in {
        ".txt", ".md", ".srt", ".vtt", ".wav", ".mp3", ".flac", ".ogg", ".m4a",
        ".mp4", ".mov", ".mkv", ".pdf", ".png", ".jpg", ".jpeg",
    } else path.name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (stem or fallback)[:80]


def chunks(text: str, limit: int) -> list[str]:
    if limit <= 0:
        raise ValueError("chunk limit must be positive")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    result: list[str] = []
    for paragraph in paragraphs or [text.strip()]:
        current = ""
        for sentence in re.split(r"(?<=[.!?।])\s+", paragraph):
            while len(sentence) > limit:
                if current:
                    result.append(current)
                    current = ""
                cut = sentence.rfind(" ", 0, limit)
                cut = cut if cut > limit // 2 else limit
                result.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if len(current) + len(sentence) + 1 > limit and current:
                result.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            result.append(current)
    return result


def script_matches(text: str, language: str, script: str = "native") -> bool:
    if script == "roman":
        return not has_non_latin_letters(text)
    if script == "codemix":
        return bool(text.strip())
    if language == "en":
        return not has_non_latin_letters(text)
    bounds = SCRIPT_RANGES.get(language)
    if not bounds:
        return bool(text.strip())
    low, high = bounds
    letters = [char for char in text if unicodedata.category(char).startswith("L")]
    return bool(letters) and sum(low <= ord(char) <= high for char in letters) / len(letters) >= 0.5


def guess_script_language(text: str) -> str | None:
    counts = {bounds: sum(candidate == bounds for candidate in SCRIPT_RANGES.values())
              for bounds in SCRIPT_RANGES.values()}
    for language, (low, high) in SCRIPT_RANGES.items():
        if counts[(low, high)] == 1 and any(low <= ord(char) <= high for char in text):
            return language
    return None


class Run:
    RECOVERABLE_TOOL_ERRORS = {
        "unsupported_language", "invalid_input", "file_not_found", "unsupported_media",
        "empty_input", "empty_transcription",
    }

    def __init__(
        self,
        input_paths: list[str | Path],
        out_dir: str | Path,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        base_url: str = API,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.out_dir = Path(out_dir).resolve()
        self.artifact_dir = self.out_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.out_dir / "trace.jsonl"
        self.report_path = self.out_dir / "report.json"
        self.max_steps, self.max_cost = max_steps, max_cost_inr
        if max_steps <= 0 or max_cost_inr < 0:
            raise ToolError("invalid_budget", "Step limit must be positive and cost limit cannot be negative.")
        self.started_at = time.monotonic()
        self.steps, self.tool_successes, self.spent, self.reserved, self.cache_hits = 0, 0, 0.0, 0.0, 0
        self.agent_turns = 0
        self.stop_reason: str | None = None
        self.stop_message = ""
        self.request_attempts = self.request_failures = self.rate_limited_responses = 0
        self.cost_by_model = {model: 0.0 for model, _ in MODELS.values()}
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(transport=transport, timeout=90.0, follow_redirects=False)
        cache_dir = Path(os.getenv("BODHAN_CACHE_DIR", str(self.out_dir / ".bodhan-cache")))
        cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            cache_dir.chmod(0o700)
        except OSError:
            pass
        self.cache = Cache(str(cache_dir), size_limit=2_000_000_000)
        self.semaphore = asyncio.Semaphore(2)
        self._budget_lock = asyncio.Lock()
        self._cache_locks: dict[str, asyncio.Lock] = {}
        self._tool_results: dict[str, dict[str, Any]] = {}
        self._tool_locks: dict[str, asyncio.Lock] = {}
        self._artifact_sequence = 0
        self._artifact_paths: set[Path] = set()
        self._artifact_by_id: dict[str, dict[str, Any]] = {}
        self.allowed_roots: list[Path] = []
        self.explicit_files: set[Path] = set()
        self.files: dict[str, Path] = {}
        self.artifacts: list[dict[str, Any]] = []
        self.verification: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.repair_attempts = 0
        for raw in input_paths:
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                raise ToolError("file_not_found", f"Input does not exist: {raw}")
            if path.is_dir():
                self.allowed_roots.append(path)
            else:
                self.explicit_files.add(path)
        self.allowed_roots = list(dict.fromkeys(self.allowed_roots))
        self._refresh_files()

    async def close(self) -> None:
        await self.client.aclose()
        self.cache.close()

    def event(self, event_type: str, **details: Any) -> None:
        record = {"time": round(time.time(), 3), "event": event_type, **details}
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _refresh_files(self) -> None:
        paths: set[Path] = set()
        allowed = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4", ".mov", ".mkv",
                   ".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md", ".srt", ".vtt"}
        paths.update(path for path in self.explicit_files
                     if path.is_file() and path.suffix.lower() in allowed
                     and not self._excluded_path(path) and not path.is_relative_to(self.out_dir))
        for root in self.allowed_roots:
            if self._excluded_path(root):
                continue
            if root.is_file():
                candidates = [root]
            elif root.is_dir():
                candidates = []
                for directory, subdirs, filenames in os.walk(root):
                    subdirs[:] = [name for name in subdirs if name not in EXCLUDED_DIRS]
                    candidates.extend(Path(directory) / name for name in filenames)
            else:
                continue
            for path in candidates:
                if path.is_file() and path.suffix.lower() in allowed:
                    resolved = path.resolve()
                    if self._excluded_path(resolved) or resolved.is_relative_to(self.out_dir):
                        continue
                    if root.is_dir() and not resolved.is_relative_to(root):
                        continue
                    paths.add(resolved)
        self.files = {f"file_{i:03d}": path for i, path in enumerate(sorted(paths), 1)}

    def file_path(self, file_ref: str) -> Path:
        if file_ref in self.files:
            return self.files[file_ref]
        candidate = Path(file_ref).expanduser().resolve()
        if self._excluded_path(candidate) or candidate.is_relative_to(self.out_dir):
            raise ToolError("file_not_found", "Unknown input reference.", "Use a file_id from list_files.")
        if candidate.is_file() and (
            candidate in self.explicit_files or any(candidate.is_relative_to(root) for root in self.allowed_roots)
        ):
            return candidate
        for path in self.files.values():
            if file_ref == path.name or file_ref == str(path):
                return path
        raise ToolError("file_not_found", "Unknown input reference.", "Use a file_id from list_files.")

    @staticmethod
    def _excluded_path(path: Path) -> bool:
        return any(part in EXCLUDED_DIRS or part.lower().startswith(EXCLUDED_FILE_PREFIXES)
                   for part in path.parts)

    def _file_id(self, path: Path) -> str:
        return next((file_id for file_id, candidate in self.files.items() if candidate == path), "input")

    @staticmethod
    def _display_name(file_id: str, path: Path) -> str:
        if has_non_latin_letters(path.name):
            return f"{file_id}.{path.suffix.lstrip('.').lower()}" if path.suffix else file_id
        return path.name

    def list_files(self) -> list[dict[str, str]]:
        self._refresh_files()
        return [{"file_id": key, "name": self._display_name(key, path), "type": path.suffix.lstrip(".").lower(),
                 "size_bytes": str(path.stat().st_size)} for key, path in self.files.items()]

    async def list_files_tool(self) -> dict[str, Any]:
        return {"files": self.list_files()}

    def artifact(self, name: str, suffix: str) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        base = safe_name(name)
        candidate = self.artifact_dir / f"{base}{suffix}"
        while candidate.exists() or candidate in self._artifact_paths:
            self._artifact_sequence += 1
            candidate = self.artifact_dir / f"{base}-{self._artifact_sequence:03d}{suffix}"
        self._artifact_paths.add(candidate)
        return candidate

    def add_artifact(self, path: Path, kind: str, **metadata: Any) -> dict[str, Any]:
        artifact_id = f"artifact_{len(self.artifacts) + 1:03d}"
        item = {"artifact_id": artifact_id, "path": str(path.resolve()), "name": path.name,
                "kind": kind, **metadata}
        self.artifacts.append(item)
        self._artifact_by_id[artifact_id] = item
        self.event("artifact", name=path.name, kind=kind)
        return item

    async def invoke(self, name: str, function, *args, **kwargs):
        try:
            bound = inspect.signature(function).bind_partial(*args, **kwargs).arguments
        except (TypeError, ValueError):
            bound = {"args": args, "kwargs": kwargs}
        if isinstance(bound.get("file_ref"), str):
            try:
                bound["file_ref"] = str(self.file_path(bound["file_ref"]))
            except ToolError:
                pass
        signature = hashlib.sha256(json.dumps(
            [name, bound], sort_keys=True, ensure_ascii=False, default=str,
        ).encode()).hexdigest()
        lock = self._tool_locks.setdefault(signature, asyncio.Lock())
        async with lock:
            if signature in self._tool_results:
                self.event("tool", name=name, status="deduplicated", signature=signature[:16])
                return copy.deepcopy(self._tool_results[signature])
            return await self._invoke_once(signature, name, function, args, kwargs)

    async def _invoke_once(self, signature, name, function, args, kwargs):
        if self.steps >= self.max_steps:
            error = ToolError("max_steps", "The tool-call step budget is exhausted.")
            self.errors.append({"code": error.code, "message": error.message})
            self.stop_reason, self.stop_message = error.code, error.message
            self.event("tool", name=name, status="error", code=error.code, steps=self.steps)
            result = error.as_dict()
            self._tool_results[signature] = result
            return copy.deepcopy(result)
        self.steps += 1
        started = time.monotonic()
        cost_before = self.spent
        arguments = self._argument_summary(function, args, kwargs)
        try:
            result = await function(*args, **kwargs)
            self.tool_successes += 1
            scope = self._tool_error_scope(name, arguments)
            for error in self.errors:
                if (error.get("tool") == name and error.get("code") in self.RECOVERABLE_TOOL_ERRORS
                        and self._tool_error_scope(name, error.get("arguments", {})) == scope):
                    error["recovered"] = True
            summary = self._result_summary(result)
            self.event("tool", name=name, status="ok", seconds=round(time.monotonic() - started, 2),
                       arguments=arguments, summary=summary,
                       cost_estimate_inr=round(self.spent - cost_before, 4),
                       cost_total_inr=round(self.spent, 4))
            public_result = self._public_result(name, result)
            self._tool_results[signature] = copy.deepcopy(public_result)
            return public_result
        except ToolError as exc:
            self.errors.append({"code": exc.code, "message": exc.message, "tool": name,
                                "arguments": arguments})
            if exc.code in {"budget_exceeded", "missing_api_key", "rate_limited", "upstream_error",
                            "upstream_timeout",
                            "network_error", "request_timeout", "invalid_response", "auth_error", "max_steps"}:
                self.stop_reason, self.stop_message = exc.code, exc.message
            self.event("tool", name=name, status="error", code=exc.code,
                       seconds=round(time.monotonic() - started, 2),
                       arguments=arguments,
                       cost_estimate_inr=round(self.spent - cost_before, 4),
                       cost_total_inr=round(self.spent, 4))
            result = exc.as_dict()
            self._tool_results[signature] = copy.deepcopy(result)
            return result
        except Exception as exc:
            message = f"{name} failed ({type(exc).__name__})."
            self.errors.append({"code": "tool_error", "message": message, "tool": name})
            self.stop_reason, self.stop_message = "tool_error", message
            self.event("tool", name=name, status="error", code="tool_error",
                       detail=type(exc).__name__, seconds=round(time.monotonic() - started, 2),
                       arguments=arguments,
                       cost_estimate_inr=round(self.spent - cost_before, 4),
                       cost_total_inr=round(self.spent, 4))
            result = {"error": message, "code": "tool_error"}
            self._tool_results[signature] = copy.deepcopy(result)
            return result

    @staticmethod
    def _tool_error_scope(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        if name == "translate_and_synthesize":
            return "language", str(arguments.get("language", ""))
        for key in ("file_ref", "text", "input", "filename"):
            if key in arguments:
                value = json.dumps(arguments[key], sort_keys=True, ensure_ascii=False, default=str)
                return key, value
        return "tool", ""

    @staticmethod
    def _result_summary(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"type": type(result).__name__}
        return {key: value for key, value in result.items()
                if key in {"artifact", "page_count", "segments_count", "language", "detected_language"}}

    @staticmethod
    def _argument_summary(function, args: tuple, kwargs: dict) -> dict[str, Any]:
        try:
            bound = inspect.signature(function).bind_partial(*args, **kwargs).arguments
        except (TypeError, ValueError):
            bound = kwargs
        result = {}
        for key, value in bound.items():
            if any(word in key.lower() for word in ("key", "token", "password", "secret")):
                continue
            if key in {"text", "input"} and isinstance(value, str):
                result[key] = {"characters": len(value),
                               "sha256": hashlib.sha256(value.encode()).hexdigest()[:16]}
            elif key == "file_ref" and isinstance(value, str):
                name = Path(value).name
                result[key] = value if value.startswith("file_") else (
                    f"input{Path(name).suffix}" if has_non_latin_letters(name) else name
                )
            elif isinstance(value, (str, int, float, bool)) or value is None:
                result[key] = value
        return result

    @staticmethod
    def _public_result(name: str, result: Any) -> Any:
        if not isinstance(result, dict) or result.get("error"):
            return result
        if name == "transcribe_audio" and has_non_latin_letters(str(result.get("text", ""))):
            return {key: result[key] for key in
                    ("artifact", "artifact_id", "language", "detected_language", "segments_count")
                    if key in result}
        if name == "transcribe_audio":
            return {**{key: result[key] for key in
                       ("artifact", "artifact_id", "language", "detected_language", "segments_count")
                       if key in result},
                    "text": str(result.get("text", ""))[:6000]}
        if name == "ocr_document":
            metadata = {key: result[key] for key in
                        ("artifact", "artifact_id", "page_count", "total_chars", "truncated") if key in result}
            if has_non_latin_letters(str(result.get("markdown", ""))):
                return metadata
            return {**metadata,
                    "markdown": str(result.get("markdown", ""))[:6000]}
        if name in {"translate_text", "translate_file", "translate_transcription"} \
                and result.get("target_language") != "en":
            return {key: result[key] for key in
                    ("artifact", "artifact_id", "target_language", "truncated") if key in result}
        if name in {"translate_text", "translate_file", "translate_transcription"}:
            return {**{key: result[key] for key in
                       ("artifact", "artifact_id", "target_language", "truncated") if key in result},
                    "translation": str(result.get("translation", ""))[:8000]}
        if name in {"translate_and_synthesize", "speak_file", "synthesize_speech"}:
            return {key: value for key, value in result.items() if key != "translation"}
        return result

    async def _request(
        self,
        model_key: str,
        endpoint: str,
        estimate: float,
        *,
        body: dict | None = None,
        form: dict | None = None,
        file: Path | None = None,
        binary: bool = False,
    ):
        model_name, key_env = MODELS[model_key]
        key = os.getenv(key_env)
        if not key:
            raise ToolError("missing_api_key", f"Set {key_env} in .env.")
        file_hash = ""
        if file:
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            file_hash = digest.hexdigest()
        cache_key = hashlib.sha256(json.dumps(
            [model_name, endpoint, body, form, file_hash, hashlib.sha256(key.encode()).hexdigest()],
            sort_keys=True, ensure_ascii=False, default=str,
        ).encode()).hexdigest()
        lock = self._cache_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.cache_hits += 1
                return cached
            return await self._send_request(model_key, endpoint, estimate, cache_key,
                                            {"Authorization": f"Bearer {key}"}, body, form, file, binary)

    async def _send_request(self, model_key, endpoint, estimate, cache_key, headers,
                            body, form, file, binary):
        model_name = MODELS[model_key][0]
        for attempt in range(2):
            async with self._budget_lock:
                if self.spent + self.reserved + estimate > self.max_cost:
                    raise ToolError("budget_exceeded",
                                    f"Estimated Bodhan spend would exceed ₹{self.max_cost:.2f}.",
                                    "Raise --max-cost-inr or stop with the partial outputs.")
                self.reserved += estimate
                self.request_attempts += 1
            dispatched = False
            try:
                async with self.semaphore:
                    if file:
                        mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                        with file.open("rb") as stream:
                            dispatched = True
                            response = await self.client.post(
                                f"{self.base_url}{endpoint}", headers=headers, data=form or {},
                                files={"file": (file.name, stream, mime)},
                            )
                    else:
                        dispatched = True
                        response = await self.client.post(
                            f"{self.base_url}{endpoint}", headers=headers, json=body, data=form,
                        )
            except asyncio.CancelledError:
                await self._settle_request_cost(model_name, estimate, charge=dispatched)
                self.request_failures += 1
                raise
            except httpx.TimeoutException as exc:
                await self._settle_request_cost(model_name, estimate, charge=True)
                self.request_failures += 1
                raise ToolError("request_timeout",
                                "The request timed out after dispatch; it was not repeated automatically.") from exc
            except httpx.HTTPError as exc:
                await self._settle_request_cost(model_name, estimate, charge=True)
                self.request_failures += 1
                raise ToolError("network_error",
                                f"Could not complete the Bodhan request ({type(exc).__name__}).") from exc
            except OSError as exc:
                await self._settle_request_cost(model_name, estimate, charge=False)
                self.request_failures += 1
                raise ToolError("file_error", "Could not read the media file for the Bodhan request.") from exc
            except Exception as exc:
                await self._settle_request_cost(model_name, estimate, charge=True)
                self.request_failures += 1
                raise ToolError("network_error",
                                f"Could not complete the Bodhan request ({type(exc).__name__}).") from exc
            if response.is_error:
                self.request_failures += 1
                if response.status_code == 429:
                    self.rate_limited_responses += 1
                uncertain = response.status_code == 408 or response.status_code >= 500
                await self._settle_request_cost(model_name, estimate, charge=uncertain)
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    delay = self._retry_delay(response, attempt)
                    if delay is not None:
                        await asyncio.sleep(delay)
                        continue
                try:
                    payload = response.json()
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    error = error if isinstance(error, dict) else {}
                except ValueError:
                    error = {}
                code = "rate_limited" if response.status_code == 429 else (
                    "upstream_timeout" if response.status_code == 408 else (
                    "upstream_error" if response.status_code >= 500 else (
                        "auth_error" if response.status_code in {401, 403} else f"http_{response.status_code}"
                    ))
                )
                message = "Bodhan rate limit persisted after one bounded retry." if code == "rate_limited" \
                    else "Bodhan returned an upstream timeout; the request was not repeated." \
                    if code == "upstream_timeout" \
                    else str(error.get("message") or f"Bodhan returned HTTP {response.status_code}.")
                raise ToolError(code, message)
            await self._settle_request_cost(model_name, estimate, charge=True)
            try:
                value = {"bytes": response.content, "content_type": response.headers.get("content-type", "")} \
                    if binary else response.json()
            except ValueError as exc:
                self.request_failures += 1
                raise ToolError("invalid_response", "Bodhan returned a response that was not valid JSON.") from exc
            self.cache.set(cache_key, value, expire=60 * 60 * 24 * 30)
            return value
        raise ToolError("upstream_error", "Bodhan remained unavailable after one bounded retry.")

    async def _settle_request_cost(self, model_name: str, estimate: float, *, charge: bool) -> None:
        async with self._budget_lock:
            self.reserved = max(0.0, self.reserved - estimate)
            if charge:
                self.spent += estimate
                self.cost_by_model[model_name] += estimate

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float | None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 1 + random.random()
            return max(0.0, delay) if delay <= 5 else None
        return min((2 ** attempt) + random.random(), 5.0)

    async def inspect_media(self, file_ref: str) -> dict[str, Any]:
        path = self.file_path(file_ref)
        file_id = self._file_id(path)
        result: dict[str, Any] = {"file_id": file_id, "name": self._display_name(file_id, path),
                                  "extension": path.suffix.lower(), "size_bytes": path.stat().st_size}
        if path.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4", ".mov", ".mkv"}:
            result["duration_seconds"] = self._duration(path)
            result["kind"] = "video" if path.suffix.lower() in {".mp4", ".mov", ".mkv"} else "audio"
        elif path.suffix.lower() == ".pdf":
            doc = pdfium.PdfDocument(str(path))
            result.update(kind="pdf", pages=len(doc))
            doc.close()
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            result["kind"] = "image"
        else:
            result["kind"] = "text"
        return result

    async def read_text(self, file_ref: str, offset: int = 0, limit: int = 6000) -> dict[str, Any]:
        artifact = self._artifact_by_id.get(file_ref)
        if artifact is not None:
            if artifact["kind"] not in {"ocr", "translation", "english_text"} \
                    or artifact.get("language", "en") != "en":
                raise ToolError("artifact_not_readable", "Only English text artifacts can be read by the planner.")
            path = Path(artifact["path"])
        else:
            path = self.file_path(file_ref)
        if path.suffix.lower() not in {".txt", ".md", ".srt", ".vtt"}:
            raise ToolError("unsupported_media", "read_text accepts .txt, .md, .srt, and .vtt files.")
        text = path.read_text(encoding="utf-8", errors="replace")
        if has_non_latin_letters(text):
            raise ToolError("indic_text_hidden", "Indic text stays inside Bodhan tools; use translate_file instead.")
        if offset < 0 or limit <= 0:
            raise ToolError("invalid_input", "offset must be non-negative and limit must be positive.")
        snippet = text[offset:offset + min(limit, 8000)]
        return {"file_id": file_ref, "offset": offset, "total_chars": len(text),
                "text": f"<untrusted-input>{snippet}</untrusted-input>"}

    @staticmethod
    def _duration(path: Path) -> float:
        probe = shutil.which("ffprobe")
        if not probe:
            raise ToolError("missing_dependency", "Install ffmpeg and ffprobe to inspect audio/video.")
        result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ToolError("invalid_media", f"Could not read media duration for {path.name}.")
        return float(result.stdout.strip())

    async def _command(self, *args: str, timeout: int = 180) -> None:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise ToolError("media_timeout", f"Media conversion timed out after {timeout}s.") from exc
        if proc.returncode:
            detail = stderr.decode(errors="replace")[-500:]
            raise ToolError("media_error", f"Media conversion failed: {detail}")

    async def _chunks_audio(self, source: Path, work: Path) -> list[tuple[Path, float]]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ToolError("missing_dependency", "Install ffmpeg to process long audio and video.")
        audio = source
        if source.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            audio = work / "audio.wav"
            await self._command(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                                 "-vn", "-ac", "1", "-ar", "16000", str(audio))
        pattern = str(work / "part-%03d.wav")
        await self._command(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
                             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                             "-f", "segment", "-segment_time", "25", "-reset_timestamps", "1", pattern)
        paths = sorted(work.glob("part-*.wav"))
        if not paths:
            raise ToolError("invalid_media", f"No audio stream found in {source.name}.")
        return [(path, self._duration(path)) for path in paths]

    async def _transcribe_path(self, source: Path, language: str | None = None) -> list[dict[str, Any]]:
        if language and language not in LANGUAGES["indic-transcribe"]:
            raise ToolError("unsupported_language", f"Transcription language {language!r} is not supported.")
        with tempfile.TemporaryDirectory(prefix="bodhan-asr-") as temp:
            parts = await self._chunks_audio(source, Path(temp))
            segments, offset = [], 0.0
            for path, duration in parts:
                form = {"model": "indic-transcribe"}
                if language:
                    form["language"] = language
                body = await self._request(
                    "transcribe", "/v1/audio/transcriptions", max(0.001, duration / 60 * 0.10),
                    form=form, file=path,
                )
                segments.append({"start": round(offset, 2), "end": round(offset + duration, 2),
                                 "text": str(body.get("text", "")).strip()})
                offset += duration
            return segments

    async def transcribe_audio(self, file_ref: str, language: str | None = None) -> dict[str, Any]:
        path = self.file_path(file_ref)
        if path.suffix.lower() not in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4", ".mov", ".mkv"}:
            raise ToolError("unsupported_media", "transcribe_audio requires audio or a video with an audio track.")
        segments = await self._transcribe_path(path, language)
        text = " ".join(part["text"] for part in segments if part["text"]).strip()
        if not text:
            raise ToolError("empty_transcription", f"No speech was detected in {path.name}.")
        detected = guess_script_language(text)
        artifact = self.artifact(f"{path.stem}.transcript", ".txt")
        artifact.write_text(text + "\n", encoding="utf-8")
        record = self.add_artifact(artifact, "transcript", language=language,
                                   detected_language=detected, source=file_ref)
        return {"text": text, "segments": segments, "segments_count": len(segments),
                "language": language, "detected_language": detected, "artifact": str(artifact),
                "artifact_id": record["artifact_id"]}

    async def _translate(self, text: str, target_language: str, script: str = "native") -> tuple[str, bool]:
        if not text.strip():
            raise ToolError("empty_input", "There is no text to translate.")
        if target_language not in LANGUAGES["indic-translate"]:
            raise ToolError("unsupported_language", f"Translation into {target_language!r} is not supported.",
                            "Use list_languages_and_voices for the current support list.")
        if script not in {"native", "roman", "codemix"}:
            raise ToolError("invalid_input", "script must be native, roman, or codemix.")
        if target_language == "en" and script == "roman":
            raise ToolError("invalid_input", "English does not accept script=roman.")

        async def one(part: str, retry: bool = True) -> tuple[str, bool]:
            body = await self._request(
                "translate", "/v1/chat/completions",
                max(0.0001, len(part) * 1.5 * 0.20 / 10000),
                body={"model": "indic-translate", "messages": [{"role": "user", "content": part}],
                      "target_language_code": target_language, "target_script": script},
            )
            choice = (body.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "")
            if isinstance(content, list):
                content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
            truncated = choice.get("finish_reason") == "length"
            if truncated and retry and len(part) > 300:
                halves = [part[:len(part) // 2], part[len(part) // 2:]]
                results = [await one(chunk, False) for chunk in halves]
                return " ".join(value for value, _ in results), any(flag for _, flag in results)
            if not str(content).strip() or truncated:
                raise ToolError("truncated_output", "Translation was empty or truncated after one retry.")
            return str(content).strip(), False

        outputs, truncated = [], False
        for part in chunks(text, 4000):
            value, was_truncated = await one(part)
            outputs.append(value)
            truncated = truncated or was_truncated
        return "\n\n".join(outputs), truncated

    async def translate_text(
        self, text: str, target_language: str, script: str = "native", filename: str = "translation.md"
    ) -> dict[str, Any]:
        if not text.strip():
            raise ToolError("invalid_input", "There is no text to translate.")
        if has_non_latin_letters(text):
            raise ToolError("indic_text_hidden", "Use translate_file or translate_transcription for Indic input.")
        translation, truncated = await self._translate(text, target_language, script)
        artifact = self.artifact(filename, ".md")
        artifact.write_text(translation + "\n", encoding="utf-8")
        record = self.add_artifact(artifact, "translation", language=target_language, script=script,
                                   source_chars=len(text))
        return {"translation": translation, "truncated": truncated, "target_language": target_language,
                "script": script,
                "artifact": str(artifact), "artifact_id": record["artifact_id"]}

    async def _save_translation(self, text: str, target_language: str, filename: str) -> dict[str, Any]:
        translation, truncated = await self._translate(text, target_language)
        artifact = self.artifact(filename, ".md")
        artifact.write_text(translation + "\n", encoding="utf-8")
        record = self.add_artifact(artifact, "translation", language=target_language, script="native",
                                   source_chars=len(text))
        return {"translation": translation, "truncated": truncated,
                "target_language": target_language, "artifact": str(artifact),
                "artifact_id": record["artifact_id"]}

    async def translate_file(self, file_ref: str, target_language: str,
                             table_format: str = "markdown") -> dict[str, Any]:
        if target_language not in LANGUAGES["indic-translate"]:
            raise ToolError("unsupported_language", f"Translation into {target_language!r} is not supported.")
        source = self.file_path(file_ref)
        if source.suffix.lower() in {".txt", ".md", ".srt", ".vtt"}:
            text = source.read_text(encoding="utf-8", errors="replace")
        elif source.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
            text = (await self.ocr_document(file_ref, table_format))["markdown"]
        else:
            raise ToolError("unsupported_media", "translate_file accepts text, PDF, PNG, or JPEG input.")
        if not text.strip():
            raise ToolError("empty_input", f"No readable text was found in {source.name}.")
        return await self._save_translation(text, target_language, f"{source.stem}.{target_language}")

    async def translate_transcription(self, file_ref: str, target_language: str,
                                      source_language: str | None = None) -> dict[str, Any]:
        if target_language not in LANGUAGES["indic-translate"]:
            raise ToolError("unsupported_language", f"Translation into {target_language!r} is not supported.")
        source = self.file_path(file_ref)
        segments = await self._transcribe_path(source, source_language)
        text = " ".join(part["text"] for part in segments if part["text"]).strip()
        if not text:
            raise ToolError("empty_transcription", f"No speech was detected in {source.name}.")
        result = await self._save_translation(text, target_language, f"{source.stem}.{target_language}")
        result["segments_count"] = len(segments)
        return result

    async def _render_pdf(self, path: Path, temp: Path) -> list[Path]:
        def render() -> list[Path]:
            document = pdfium.PdfDocument(str(path))
            images = []
            for index, page in enumerate(document):
                image = page.render(scale=2).to_pil().convert("RGB")
                target = temp / f"page-{index + 1:04d}.jpg"
                image.save(target, quality=90)
                images.append(target)
            document.close()
            return images
        return render()

    async def ocr_document(self, file_ref: str, table_format: str = "markdown") -> dict[str, Any]:
        source = self.file_path(file_ref)
        if table_format not in {"markdown", "html"}:
            raise ToolError("invalid_input", "table_format must be markdown or html.")
        with tempfile.TemporaryDirectory(prefix="bodhan-ocr-") as temp_name:
            temp = Path(temp_name)
            if source.suffix.lower() == ".pdf":
                images = await self._render_pdf(source, temp)
            elif source.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                images = [source]
            else:
                raise ToolError("unsupported_media", "ocr_document accepts PDF, PNG, or JPEG input.")
            if not images:
                raise ToolError("empty_document", f"No pages were found in {source.name}.")

            async def ocr_region(region: Image.Image, prefix: str, depth: int = 0) -> str:
                region_path = temp / f"{prefix}.png"
                region.save(region_path)
                region_data = base64.b64encode(region_path.read_bytes()).decode("ascii")
                region_body = {"model": "indic-ocr", "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{region_data}"}}
                ]}], "table_format": table_format, "max_tokens": 4096}
                try:
                    result = await self._request("ocr", "/v1/chat/completions", 0.20,
                                                 body=region_body)
                    choice = (result.get("choices") or [{}])[0]
                    return str((choice.get("message") or {}).get("content", ""))
                except ToolError as exc:
                    if exc.code != "upstream_error" or "truncated" not in exc.message.lower() or depth >= 3:
                        raise
                    width, height = region.size
                    midpoint, overlap = height // 2, max(1, height // 40)
                    top = region.crop((0, 0, width, midpoint + overlap))
                    bottom = region.crop((0, midpoint - overlap, width, height))
                    first = await ocr_region(top, f"{prefix}-top", depth + 1)
                    second = await ocr_region(bottom, f"{prefix}-bottom", depth + 1)
                    return "\n\n".join(part for part in (first, second) if part.strip())

            async def split_ocr_page(image_path: Path) -> dict[str, Any]:
                with Image.open(image_path) as page:
                    page = page.convert("RGB")
                    width, height = page.size
                    midpoint, overlap = height // 2, max(1, height // 40)
                    crops = (page.crop((0, 0, width, midpoint + overlap)),
                             page.crop((0, midpoint - overlap, width, height)))
                    parts = [await ocr_region(crop, f"{image_path.stem}-fallback-{index}")
                             for index, crop in enumerate(crops, start=1)]
                return {"choices": [{"message": {"content": "\n\n".join(parts)}}]}

            pages = []
            for image in images:
                mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                split_hint = "ocr-split:" + hashlib.sha256(
                    image.read_bytes() + table_format.encode()).hexdigest()
                request_body = {"model": "indic-ocr", "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
                ]}], "table_format": table_format, "max_tokens": 2048}
                if self.cache.get(split_hint):
                    body = await split_ocr_page(image)
                else:
                    try:
                        body = await self._request("ocr", "/v1/chat/completions", 0.20, body=request_body)
                    except ToolError as exc:
                        if exc.code != "upstream_error" or "truncated" not in exc.message.lower():
                            raise
                        request_body["max_tokens"] = 8192
                        try:
                            body = await self._request("ocr", "/v1/chat/completions", 0.20,
                                                       body=request_body)
                        except ToolError as retry_exc:
                            if retry_exc.code != "upstream_error" or "truncated" not in retry_exc.message.lower():
                                raise
                            self.cache.set(split_hint, True, expire=60 * 60 * 24 * 30)
                            body = await split_ocr_page(image)
                choice = (body.get("choices") or [{}])[0]
                pages.append(str((choice.get("message") or {}).get("content", "")))
            markdown = "\n\n".join(pages).strip()
        artifact = self.artifact(f"{source.stem}.ocr", ".md")
        artifact.write_text(markdown + "\n", encoding="utf-8")
        record = self.add_artifact(artifact, "ocr", source=file_ref, pages=len(pages))
        return {"markdown": markdown, "page_count": len(pages), "artifact": str(artifact),
                "artifact_id": record["artifact_id"], "total_chars": len(markdown),
                "truncated": len(markdown) > 6000}

    async def synthesize_speech(
        self, text: str, language: str, voice: str | None = None, style: str | None = None,
        filename: str = "speech",
    ) -> dict[str, Any]:
        if language not in VOICES:
            raise ToolError("unsupported_language", f"Speech in {language!r} is not supported.")
        if has_non_latin_letters(text):
            raise ToolError("indic_text_hidden", "LLM-provided Indic text is not accepted. Use Bodhan translation tools.")
        if language != "en":
            raise ToolError("translation_required", "Translate English with translate_and_synthesize or use speak_file.")
        return await self._synthesize_speech(text, language, voice, style, filename)

    def _validate_speech_options(self, language: str, voice: str | None, style: str | None) -> str:
        if language not in VOICES:
            raise ToolError("unsupported_language", f"Speech in {language!r} is not supported.")
        available = [name for group in VOICES[language].values() for name in group]
        voice = voice or next(iter(VOICES[language].values()))[0]
        if voice not in available:
            raise ToolError("invalid_input", f"Voice {voice!r} is not listed for language {language}.")
        if style and style not in RULES["models"]["indic-speak"].get("styles", []):
            raise ToolError("invalid_input", "style is not listed in the Bodhan Indic-Speak voice library.")
        return voice

    async def _synthesize_speech(
        self, text: str, language: str, voice: str | None = None, style: str | None = None,
        filename: str = "speech",
    ) -> dict[str, Any]:
        voice = self._validate_speech_options(language, voice, style)
        speech_chunks = chunks(text, 350)
        if not speech_chunks:
            raise ToolError("invalid_input", "There is no text to synthesize.")
        with tempfile.TemporaryDirectory(prefix="bodhan-tts-") as temp_name:
            temp = Path(temp_name)
            paths, extension = [], None
            for index, part in enumerate(speech_chunks):
                body = await self._request(
                    "speak", "/v1/audio/speech", len(part) * 6 / 10000,
                    body={"model": "indic-speak", "input": part, "voice": voice,
                          "instructions": json.dumps({"lang": language, **({"style": style} if style else {})})},
                    binary=True,
                )
                content_type = body["content_type"].split(";")[0]
                suffix = ".mp3" if content_type in {"audio/mpeg", "audio/mp3"} else ".wav"
                if extension and extension != suffix:
                    raise ToolError("upstream_error", "Bodhan changed audio format between speech chunks.")
                extension = suffix
                path = temp / f"speech-{index:04d}{suffix}"
                path.write_bytes(body["bytes"])
                paths.append(path)
            output = self.artifact(filename, extension or ".wav")
            if len(paths) == 1:
                shutil.copyfile(paths[0], output)
            else:
                concat_file = temp / "concat.txt"
                concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths))
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    raise ToolError("missing_dependency", "Install ffmpeg to join speech chunks.")
                codec = "libmp3lame" if extension == ".mp3" else "pcm_s16le"
                await self._command(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                                    "-safe", "0", "-i", str(concat_file), "-c:a", codec, str(output))
        self.add_artifact(output, "audio", language=language, voice=voice, text=text)
        return {"artifact": str(output), "language": language, "voice": voice,
                "chunks": len(speech_chunks), "ai_generated": True}

    async def translate_and_synthesize(self, text: str, language: str, voice: str | None = None,
                                       style: str | None = None, filename: str = "speech") -> dict[str, Any]:
        if not text.strip() or has_non_latin_letters(text):
            raise ToolError("invalid_input", "Provide English text; Bodhan will translate and synthesize it.")
        voice = self._validate_speech_options(language, voice, style)
        translated = await self._save_translation(text, language, f"{Path(filename).stem}.translation")
        speech = await self._synthesize_speech(translated["translation"], language, voice, style, filename)
        return {"artifact": speech["artifact"], "translation_artifact": translated["artifact"],
                "language": language, "voice": speech["voice"], "chunks": speech["chunks"],
                "ai_generated": True}

    async def speak_file(self, file_ref: str, language: str, voice: str | None = None,
                         style: str | None = None) -> dict[str, Any]:
        source = self.file_path(file_ref)
        if source.suffix.lower() not in {".txt", ".md"}:
            raise ToolError("unsupported_media", "speak_file accepts .txt or .md source files.")
        text = source.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise ToolError("empty_input", f"No readable text was found in {source.name}.")
        detected = guess_script_language(text)
        if detected == language or (language == "en" and not has_non_latin_letters(text)):
            result = await self._synthesize_speech(text, language, voice, style, source.stem)
        elif not has_non_latin_letters(text):
            return await self.translate_and_synthesize(text, language, voice, style, source.stem)
        else:
            translated = await self._save_translation(text, language, f"{source.stem}.{language}")
            result = await self._synthesize_speech(translated["translation"], language, voice, style, source.stem)
            result["translation_artifact"] = translated["artifact"]
        return result

    async def make_subtitles(
        self, file_ref: str, target_language: str = "en", source_language: str | None = None
    ) -> dict[str, Any]:
        if target_language not in LANGUAGES["indic-translate"]:
            raise ToolError("unsupported_language", f"Translation into {target_language!r} is not supported.")
        source = self.file_path(file_ref)
        segments = await self._transcribe_path(source, source_language)
        segments = [segment for segment in segments if segment["text"].strip()]
        if not segments:
            raise ToolError("empty_transcription", f"No speech was detected in {source.name}.")
        lines = []
        for index, segment in enumerate(segments, 1):
            translated, _ = await self._translate(segment["text"], target_language)
            lines.extend([str(index), f"{self._srt_time(segment['start'])} --> {self._srt_time(segment['end'])}",
                          translated, ""])
        artifact = self.artifact(f"{source.stem}.{target_language}", ".srt")
        artifact.write_text("\n".join(lines), encoding="utf-8")
        self.add_artifact(artifact, "subtitles", language=target_language, source=file_ref,
                          timing="approximate 25-second audio windows")
        return {"artifact": str(artifact), "segments": len(segments), "language": target_language,
                "timing": "approximate"}

    @staticmethod
    def _srt_time(seconds: float) -> str:
        ms = round(seconds * 1000)
        hours, ms = divmod(ms, 3_600_000)
        minutes, ms = divmod(ms, 60_000)
        secs, ms = divmod(ms, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"

    async def write_english(self, text: str, filename: str = "summary.txt") -> dict[str, str]:
        if not text.strip() or has_non_latin_letters(text):
            raise ToolError("english_only", "This tool accepts English prose only. Use Bodhan tools for Indic text.")
        artifact = self.artifact(filename, ".txt")
        artifact.write_text(text.strip() + "\n", encoding="utf-8")
        record = self.add_artifact(artifact, "english_text", language="en")
        return {"artifact": str(artifact), "artifact_id": record["artifact_id"],
                "characters": str(len(text))}

    async def list_languages_and_voices(self) -> dict[str, Any]:
        return {"languages": {model: sorted(values) for model, values in LANGUAGES.items()},
                "voices": VOICES}

    async def verify_artifacts(self) -> list[dict[str, Any]]:
        checks = []
        for item in self.artifacts:
            path = Path(item["path"])
            signature = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
            if item.get("_signature") == signature and item.get("_check"):
                checks.append(item["_check"])
                continue
            ok = path.is_file() and path.stat().st_size > 0
            detail = "exists and is non-empty" if ok else "missing or empty"
            if ok and item["kind"] == "subtitles":
                content = path.read_text(encoding="utf-8", errors="replace")
                cues = [block.splitlines() for block in re.split(r"\n\s*\n", content.strip()) if block.strip()]
                timestamp = re.compile(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}")
                ok = bool(cues) and all(len(cue) >= 3 and cue[0].isdigit()
                                        and timestamp.fullmatch(cue[1]) for cue in cues)
                if ok and item.get("language"):
                    subtitle_text = "\n".join(line for cue in cues for line in cue[2:])
                    ok = script_matches(subtitle_text, item["language"])
                    detail = "SRT cues and target script" if ok else "SRT script does not match target language"
                else:
                    detail = "SRT cue structure" if ok else "no valid SRT cues"
            if ok and item["kind"] == "translation" and item.get("language"):
                ok = script_matches(path.read_text(encoding="utf-8"), item["language"], item.get("script", "native"))
                detail = "target script check" if ok else "translation script does not match requested language"
            if ok and item["kind"] == "audio":
                if self.stop_reason:
                    ok, detail = False, f"verification skipped: run stopped because {self.stop_reason}"
                else:
                    try:
                        text = unicodedata.normalize("NFKC", str(item.get("text", ""))).casefold()
                        segments = await self._transcribe_path(path, item.get("language"))
                        transcript = unicodedata.normalize(
                            "NFKC", " ".join(part["text"] for part in segments),
                        ).casefold()

                        def normalize(value: str) -> str:
                            return re.sub(r"[^\w]", "", value, flags=re.UNICODE)

                        ratio = SequenceMatcher(None, normalize(text), normalize(transcript), autojunk=False).ratio()
                        ok = ratio >= 0.45
                        detail = f"back-transcription similarity {ratio:.2f}"
                    except ToolError as exc:
                        ok, detail = False, f"audio check unavailable: {exc.code}"
            item["_signature"] = signature
            item["_check"] = {"artifact": path.name, "ok": ok, "detail": detail}
            checks.append(item["_check"])
        self.verification = checks
        return checks

    def write_outputs(self, final_text: str, status: str) -> None:
        if has_non_latin_letters(final_text):
            final_text = "The task finished with language output in the requested artifacts. See the report for checks."
        artifacts = "\n".join(
            f"- [{item['name']}](artifacts/{item['name']}) ({item['kind']})" for item in self.artifacts
        ) or "- None"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "result.md").write_text(
            f"# Task result\n\nStatus: {status}\n\n{final_text.strip()}\n\n## Artifacts\n\n{artifacts}\n",
            encoding="utf-8",
        )
        self.report_path.write_text(json.dumps({
            "status": status, "steps": self.steps, "successful_tool_calls": self.tool_successes,
            "estimated_cost_inr": round(self.spent, 4),
            "estimated_cost_inr_by_model": {key: round(value, 4)
                                             for key, value in self.cost_by_model.items()},
            "request_attempts": self.request_attempts,
            "request_failures": self.request_failures,
            "rate_limited_responses": self.rate_limited_responses,
            "duration_seconds": round(time.monotonic() - self.started_at, 2),
            "max_cost_inr": self.max_cost, "cache_hits": self.cache_hits,
            "stop_reason": self.stop_reason,
            "reserved_cost_inr": round(self.reserved, 4),
            "errors": self.errors,
            "artifacts": [{k: v for k, v in item.items()
                           if k not in {"text", "_signature", "_check"}}
                          for item in self.artifacts],
            "verification": self.verification,
            "quality_targets": {
                "task_completion": {"met": status == "completed", "status": status},
                "artifact_verification": {"met": bool(self.verification)
                                                and all(check["ok"] for check in self.verification),
                                          "checks": self.verification},
                "budget": {"met": self.spent <= self.max_cost, "limit_inr": self.max_cost},
                "latency_under_five_minutes": {"met": time.monotonic() - self.started_at < 300},
                "translation_quality": {"met": None,
                                        "reason": "Script checks do not measure semantic translation quality."},
                "subtitle_timing": {"met": not any(item["kind"] == "subtitles" for item in self.artifacts),
                                    "reason": "Subtitle times use approximate 25-second audio windows."},
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
