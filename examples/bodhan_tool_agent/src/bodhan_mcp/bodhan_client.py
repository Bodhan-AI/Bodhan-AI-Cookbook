from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from bodhan_mcp.media_utils import (
    MAX_AUDIO_CHUNK_SECONDS,
    chunk_audio_file,
    ensure_dir,
    get_audio_duration_seconds,
    pdf_to_page_images,
    stitch_wav_files,
    validate_existing_file,
)

load_dotenv()

DEFAULT_BASE_URL = "https://api.bodhan.ai"

# Rough INR cost estimates for budget tracking and reporting
COST_PER_SECOND_ASR_INR = 0.02
COST_PER_1K_TRANSLATE_CHARS_INR = 0.15
COST_PER_1K_TTS_CHARS_INR = 0.25
COST_PER_OCR_PAGE_INR = 0.50


@dataclass
class CostLedger:
    total_inr: float = 0.0
    by_tool: dict[str, float] = field(default_factory=dict)
    call_count: int = 0
    cache_hits: int = 0

    def record(self, tool_name: str, amount_inr: float, cached: bool = False) -> None:
        self.call_count += 1
        if cached:
            self.cache_hits += 1
            return
        rounded = round(max(0.0, amount_inr), 6)
        self.total_inr = round(self.total_inr + rounded, 6)
        self.by_tool[tool_name] = round(self.by_tool.get(tool_name, 0.0) + rounded, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_inr": round(self.total_inr, 4),
            "by_tool": self.by_tool,
            "call_count": self.call_count,
            "cache_hits": self.cache_hits,
        }


class BodhanClient:
    def __init__(
        self,
        base_url: str | None = None,
        cache_dir: str | Path | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        timeout_seconds: float = 90.0,
        ledger: CostLedger | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("BODHAN_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        raw_cache = cache_dir or os.environ.get("BODHAN_CACHE_DIR") or ".cache/bodhan"
        self.cache_dir = ensure_dir(raw_cache)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout_seconds = timeout_seconds
        self.ledger = ledger if ledger is not None else CostLedger()

    def _resolve_api_key(self, capability: str) -> str:
        specific_map = {
            "transcribe": "BODHAN_TRANSCRIBE_KEY",
            "translate": "BODHAN_TRANSLATE_KEY",
            "speak": "BODHAN_SPEAK_KEY",
            "ocr": "BODHAN_OCR_KEY",
        }
        env_key = specific_map.get(capability, "")
        val = (os.environ.get(env_key) or "").strip() if env_key else ""
        if not val:
            val = (os.environ.get("BODHAN_API_KEY") or "").strip()
        if not val:
            raise RuntimeError(
                f"Missing Bodhan API key for '{capability}'. Set {env_key} or BODHAN_API_KEY in your environment."
            )
        return val

    def _headers_json(self, capability: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_api_key(capability)}",
            "Content-Type": "application/json",
        }

    def _headers_auth_only(self, capability: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_api_key(capability)}",
        }

    def _cache_path(self, namespace: str, payload_key: str, ext: str = ".json") -> Path:
        digest = hashlib.sha256(f"{namespace}:{payload_key}".encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}{ext}"

    def _read_json_cache(self, path: Path) -> dict[str, Any] | None:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _write_json_cache(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _request_with_backoff(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=json_body,
                        files=files,
                        data=data,
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < self.max_retries:
                        retry_after = resp.headers.get("Retry-After")
                        try:
                            sleep_for = (
                                float(retry_after)
                                if retry_after
                                else self.backoff_base * (2**attempt)
                            )
                        except ValueError:
                            sleep_for = self.backoff_base * (2**attempt)
                        time.sleep(min(sleep_for, 15.0))
                        continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(min(self.backoff_base * (2**attempt), 15.0))
                    continue
                raise RuntimeError(f"Bodhan API request failed ({method} {url}): {exc}") from exc

        raise RuntimeError(
            f"Bodhan API request exhausted retries ({method} {url}): {last_exc}"
        )

    def transcribe_audio(
        self,
        audio_path: str | Path,
        language: str | None = None,
        work_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        src = validate_existing_file(audio_path)
        file_bytes = src.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        cache_file = self._cache_path(
            "transcribe", f"{file_hash}:{language or 'auto'}", ".json"
        )
        cached = self._read_json_cache(cache_file)
        if cached is not None:
            self.ledger.record("indic_transcribe", 0.0, cached=True)
            return cached

        chunk_work_dir = ensure_dir(work_dir or (self.cache_dir / "chunks"))
        chunks = chunk_audio_file(
            src, chunk_work_dir, chunk_seconds=MAX_AUDIO_CHUNK_SECONDS
        )

        transcript_parts: list[str] = []
        total_duration = 0.0

        for chunk in chunks:
            c_path = Path(chunk["path"])
            c_dur = float(
                chunk.get("duration_seconds") or get_audio_duration_seconds(c_path)
            )
            total_duration += c_dur

            form_data: dict[str, Any] = {"model": "indic-transcribe"}
            if language:
                form_data["language"] = language

            with c_path.open("rb") as fh:
                resp = self._request_with_backoff(
                    "POST",
                    f"{self.base_url}/v1/audio/transcriptions",
                    self._headers_auth_only("transcribe"),
                    files={"file": (c_path.name, fh, "application/octet-stream")},
                    data=form_data,
                )
            payload = resp.json()
            text = (payload.get("text") or "").strip()
            if text:
                transcript_parts.append(text)

        full_text = " ".join(transcript_parts).strip()
        estimated_cost = total_duration * COST_PER_SECOND_ASR_INR
        self.ledger.record("indic_transcribe", estimated_cost, cached=False)

        result = {
            "text": full_text,
            "language": language or "auto",
            "chunks_processed": len(chunks),
            "duration_seconds": round(total_duration, 3),
            "estimated_cost_inr": round(estimated_cost, 4),
        }
        self._write_json_cache(cache_file, result)
        return result

    def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
    ) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot translate empty text.")

        cache_key = f"{cleaned}:{source_language or 'auto'}:{target_language}:{script}"
        cache_file = self._cache_path("translate", cache_key, ".json")
        cached = self._read_json_cache(cache_file)
        if cached is not None:
            self.ledger.record("indic_translate", 0.0, cached=True)
            return cached

        body: dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": cleaned}],
            "target_language_code": target_language,
            "script": script,
        }
        if source_language:
            body["source_language_code"] = source_language

        resp = self._request_with_backoff(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            self._headers_json("translate"),
            json_body=body,
        )
        raw = resp.json()

        translated = ""
        choices = raw.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            translated = msg.get("content") or ""

        if not translated:
            translated = (
                raw.get("translated_text")
                or raw.get("translation")
                or raw.get("output")
                or raw.get("text")
                or ""
            )
        if not translated and isinstance(raw.get("data"), list) and raw["data"]:
            translated = (
                raw["data"][0].get("translated_text") or raw["data"][0].get("text") or ""
            )

        estimated_cost = (len(translated) / 1000.0) * COST_PER_1K_TRANSLATE_CHARS_INR
        self.ledger.record("indic_translate", estimated_cost, cached=False)

        result = {
            "translated_text": str(translated).strip(),
            "source_language": raw.get("source_language") or source_language or "auto",
            "target_language": target_language,
            "script": script,
            "estimated_cost_inr": round(estimated_cost, 4),
        }
        self._write_json_cache(cache_file, result)
        return result

    def synthesize_speech(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: str | Path,
        style: str | None = None,
        work_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot synthesize empty text.")

        out_file = Path(output_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)

        cache_key = f"{cleaned}:{language}:{voice}:{style or ''}"
        cache_audio = self._cache_path("speak_wav", cache_key, ".wav")
        if cache_audio.exists():
            out_file.write_bytes(cache_audio.read_bytes())
            self.ledger.record("indic_speak", 0.0, cached=True)
            return {
                "audio_path": str(out_file),
                "language": language,
                "voice": voice,
                "style": style,
                "segments": 1,
                "estimated_cost_inr": 0.0,
            }

        segments: list[str] = []
        current = ""
        for sentence in cleaned.replace("\n", " ").split(". "):
            part = sentence.strip()
            if not part:
                continue
            if not part.endswith((".", "!", "?", "।")):
                part = f"{part}."
            if len(current) + len(part) + 1 <= 260:
                current = f"{current} {part}".strip()
            else:
                if current:
                    segments.append(current)
                current = part
        if current:
            segments.append(current)
        if not segments:
            segments = [cleaned[:260]]

        instructions_obj: dict[str, Any] = {"lang": language}
        if style:
            instructions_obj["style"] = style
        instructions_str = json.dumps(instructions_obj, ensure_ascii=False)

        seg_dir = ensure_dir(work_dir or (self.cache_dir / "tts_segments"))
        segment_files: list[Path] = []

        for idx, seg in enumerate(segments):
            payload = {
                "model": "indic-speak",
                "input": seg,
                "voice": voice,
                "instructions": instructions_str,
                "response_format": "wav",
            }
            resp = self._request_with_backoff(
                "POST",
                f"{self.base_url}/v1/audio/speech",
                self._headers_json("speak"),
                json_body=payload,
            )
            seg_path = seg_dir / f"seg_{idx:03d}.wav"
            seg_path.write_bytes(resp.content)
            segment_files.append(seg_path)

        if len(segment_files) == 1:
            out_file.write_bytes(segment_files[0].read_bytes())
        else:
            stitch_wav_files(segment_files, out_file)

        cache_audio.parent.mkdir(parents=True, exist_ok=True)
        cache_audio.write_bytes(out_file.read_bytes())

        estimated_cost = (len(cleaned) / 1000.0) * COST_PER_1K_TTS_CHARS_INR
        self.ledger.record("indic_speak", estimated_cost, cached=False)

        return {
            "audio_path": str(out_file),
            "language": language,
            "voice": voice,
            "style": style,
            "segments": len(segment_files),
            "estimated_cost_inr": round(estimated_cost, 4),
        }

    def ocr_image_or_pdf(
        self,
        file_path: str | Path,
        table_format: str = "markdown",
        work_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        src = validate_existing_file(file_path)
        file_hash = hashlib.sha256(src.read_bytes()).hexdigest()
        cache_file = self._cache_path("ocr", f"{file_hash}:{table_format}", ".json")
        cached = self._read_json_cache(cache_file)
        if cached is not None:
            self.ledger.record("indic_ocr", 0.0, cached=True)
            return cached

        if src.suffix.lower() == ".pdf":
            page_dir = ensure_dir(work_dir or (self.cache_dir / "pdf_pages"))
            image_paths = [Path(p) for p in pdf_to_page_images(src, page_dir, scale=2.0)]
        else:
            image_paths = [src]

        pages_output: list[dict[str, Any]] = []
        markdown_parts: list[str] = []

        for page_idx, img_path in enumerate(image_paths, start=1):
            img_bytes = img_path.read_bytes()
            b64_img = base64.b64encode(img_bytes).decode("ascii")
            mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            data_url = f"data:{mime};base64,{b64_img}"

            body = {
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Extract all text and layout from this page. Format tables as {table_format}.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                        ],
                    }
                ],
            }
            resp = self._request_with_backoff(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                self._headers_json("ocr"),
                json_body=body,
            )
            data = resp.json()
            content = ""
            choices = data.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content") or ""

            pages_output.append(
                {
                    "page": page_idx,
                    "source_image": str(img_path),
                    "markdown": content,
                    "blocks": data.get("blocks", []),
                }
            )
            if content:
                markdown_parts.append(f"--- Page {page_idx} ---\n{content}")

        estimated_cost = len(image_paths) * COST_PER_OCR_PAGE_INR
        self.ledger.record("indic_ocr", estimated_cost, cached=False)

        result = {
            "source_file": str(src),
            "page_count": len(image_paths),
            "markdown": "\n\n".join(markdown_parts).strip(),
            "pages": pages_output,
            "estimated_cost_inr": round(estimated_cost, 4),
        }
        self._write_json_cache(cache_file, result)
        return result