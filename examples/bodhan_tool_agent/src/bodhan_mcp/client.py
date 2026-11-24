from __future__ import annotations

import base64
import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import BodhanConfig
from bodhan_mcp.cost import CostTracker
from bodhan_mcp.languages import detect_language_from_text, select_voice


class BodhanAPIError(Exception):
    def __init__(
        self,
        message: str,
        model: str,
        status_code: Optional[int] = None,
        retryable: bool = False,
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.model = model
        self.status_code = status_code
        self.retryable = retryable
        self.suggestion = suggestion


class BodhanClient:
    def __init__(
        self,
        config: Optional[BodhanConfig] = None,
        cost_tracker: Optional[CostTracker] = None,
        cache: Optional[DiskCache] = None,
        http_transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.config = config or BodhanConfig.from_env()
        self.cost_tracker = cost_tracker or CostTracker()
        self.cache = cache or DiskCache(self.config.cache_dir)
        self._semaphore = threading.BoundedSemaphore(self.config.max_concurrency)
        self._transport = http_transport

    def _build_client(self, timeout: Optional[float] = None) -> httpx.Client:
        effective_timeout = timeout or self.config.request_timeout_seconds
        return httpx.Client(
            base_url=self.config.bodhan_base_url,
            timeout=httpx.Timeout(effective_timeout),
            transport=self._transport,
        )

    def _request_with_retry(
        self,
        method: str,
        url_path: str,
        model: str,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
        data_body: Optional[Dict[str, Any]] = None,
        files_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        api_key = self.config.get_key_for_model(model)
        is_placeholder = not api_key or api_key.startswith("your_")
        if is_placeholder and self._transport is None:
            env_var_map = {
                "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
                "indic-translate": "BODHAN_TRANSLATE_KEY",
                "indic-speak": "BODHAN_SPEAK_KEY",
                "indic-ocr": "BODHAN_OCR_KEY",
            }
            needed_var = env_var_map.get(model, "BODHAN_*_KEY")
            raise BodhanAPIError(
                message=f"Missing API key for model '{model}' (environment variable {needed_var} is not set).",
                model=model,
                status_code=401,
                retryable=False,
                suggestion=f"Set {needed_var} in your environment or .env file from https://console.bodhan.ai/.",
            )

        req_headers = {"Authorization": f"Bearer {api_key}", **headers}
        last_exc: Optional[Exception] = None

        with self._semaphore:
            for attempt in range(self.config.max_retries + 1):
                try:
                    with self._build_client(timeout=timeout) as client:
                        resp = client.request(
                            method=method,
                            url=url_path,
                            headers=req_headers,
                            json=json_body,
                            data=data_body,
                            files=files_body,
                        )
                    if resp.status_code < 400:
                        return resp

                    retryable = resp.status_code in (408, 429, 500, 502, 503, 504)
                    if retryable and attempt < self.config.max_retries:
                        backoff = (0.6 * (2**attempt)) + random.uniform(0.05, 0.25)
                        time.sleep(backoff)
                        continue

                    err_detail = resp.text[:400]
                    raise BodhanAPIError(
                        message=f"Bodhan API error ({resp.status_code}) on {model}: {err_detail}",
                        model=model,
                        status_code=resp.status_code,
                        retryable=retryable,
                        suggestion=(
                            "Verify API key, quota/credits at https://console.bodhan.ai/, and input parameters."
                        ),
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                    if attempt < self.config.max_retries:
                        backoff = (0.6 * (2**attempt)) + random.uniform(0.05, 0.25)
                        time.sleep(backoff)
                        continue
                    raise BodhanAPIError(
                        message=f"Network/timeout error calling {model} after {self.config.max_retries + 1} attempts: {exc}",
                        model=model,
                        status_code=None,
                        retryable=True,
                        suggestion="Check network connectivity to https://api.bodhan.ai/v1 or increase timeout.",
                    ) from exc

        raise BodhanAPIError(
            message=f"Exhausted retries calling {model}: {last_exc}",
            model=model,
            retryable=True,
        )

    def transcribe_chunk(
        self,
        wav_path: Path,
        duration_sec: float,
        language: Optional[str] = None,
        model_variant: str = "indic-transcribe",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        wav_bytes = Path(wav_path).read_bytes()
        lang_param = None if (not language or language == "auto") else language
        cache_key = DiskCache.compute_key(
            model=model_variant,
            operation="transcribe",
            payload={"language": lang_param or "auto"},
            raw_bytes=wav_bytes,
        )

        cached_val = self.cache.get_json(cache_key)
        if cached_val is not None:
            self.cost_tracker.record(
                model="indic-transcribe",
                operation="transcribe_chunk",
                units=round(duration_sec / 60.0, 4),
                unit_name="minutes",
                cost_inr=0.0,
                cached=True,
                latency_ms=0.5,
            )
            return {**cached_val, "cached": True, "cost_inr": 0.0, "latency_ms": 0.5}

        est_cost = CostTracker.estimate_transcribe_cost(duration_sec)
        self.cost_tracker.check_cost_budget(est_cost, description=f"indic-transcribe ({duration_sec:.1f}s audio)")

        t0 = time.perf_counter()
        form_data: Dict[str, Any] = {"model": model_variant}
        if lang_param:
            form_data["language"] = lang_param

        files = {"file": (Path(wav_path).name, wav_bytes, "audio/wav")}
        resp = self._request_with_retry(
            method="POST",
            url_path="/audio/transcriptions",
            model="indic-transcribe",
            headers={},
            data_body=form_data,
            files_body=files,
            timeout=timeout,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text.strip()}

        text = str(payload.get("text", "")).strip()
        detected = detect_language_from_text(text)
        resolved_lang = payload.get("language") or lang_param or str(detected["language_code"])
        segments = payload.get("segments") or []

        result_data = {
            "text": text,
            "language": resolved_lang,
            "detected_script": detected.get("script", "Unknown"),
            "segments": segments,
        }
        self.cache.set_json(cache_key, result_data)
        actual_cost = self.cost_tracker.record(
            model="indic-transcribe",
            operation="transcribe_chunk",
            units=round(duration_sec / 60.0, 4),
            unit_name="minutes",
            cost_inr=est_cost,
            cached=False,
            latency_ms=latency_ms,
        )
        return {**result_data, "cached": False, "cost_inr": actual_cost, "latency_ms": latency_ms}

    def translate_chunk(
        self,
        text: str,
        source_language: str,
        target_language: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not text.strip():
            return {"translated_text": text, "cached": True, "cost_inr": 0.0, "latency_ms": 0.0, "output_tokens": 0}

        if source_language == target_language:
            return {"translated_text": text, "cached": True, "cost_inr": 0.0, "latency_ms": 0.0, "output_tokens": 0}

        cache_key = DiskCache.compute_key(
            model="indic-translate",
            operation="translate",
            payload={
                "text": text,
                "source_language_code": source_language,
                "target_language_code": target_language,
            },
        )
        cached_val = self.cache.get_json(cache_key)
        if cached_val is not None:
            self.cost_tracker.record(
                model="indic-translate",
                operation="translate_chunk",
                units=float(cached_val.get("output_tokens", 0)),
                unit_name="tokens",
                cost_inr=0.0,
                cached=True,
                latency_ms=0.5,
            )
            return {**cached_val, "cached": True, "cost_inr": 0.0, "latency_ms": 0.5}

        est_tokens = max(10, int(len(text) / 3.0))
        est_cost = CostTracker.estimate_translate_cost(est_tokens)
        self.cost_tracker.check_cost_budget(
            est_cost, description=f"indic-translate ({source_language}->{target_language})"
        )

        t0 = time.perf_counter()
        body = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "source_language_code": source_language,
            "target_language_code": target_language,
        }
        resp = self._request_with_retry(
            method="POST",
            url_path="/chat/completions",
            model="indic-translate",
            headers={"Content-Type": "application/json"},
            json_body=body,
            timeout=timeout,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        data = resp.json()

        choices = data.get("choices") or []
        translated_text = ""
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            translated_text = str(msg.get("content", "")).strip()
        elif "translated_text" in data:
            translated_text = str(data["translated_text"]).strip()

        usage = data.get("usage") or {}
        output_tokens = int(usage.get("completion_tokens") or max(1, len(translated_text) // 3))
        actual_cost = CostTracker.estimate_translate_cost(output_tokens)

        result_data = {
            "translated_text": translated_text,
            "source_language": source_language,
            "target_language": target_language,
            "output_tokens": output_tokens,
        }
        self.cache.set_json(cache_key, result_data)
        recorded_cost = self.cost_tracker.record(
            model="indic-translate",
            operation="translate_chunk",
            units=float(output_tokens),
            unit_name="tokens",
            cost_inr=actual_cost,
            cached=False,
            latency_ms=latency_ms,
        )
        return {**result_data, "cached": False, "cost_inr": recorded_cost, "latency_ms": latency_ms}

    def synthesize_chunk(
        self,
        text: str,
        language: str,
        voice: Optional[str] = None,
        gender: Optional[str] = None,
        style: str = "narration",
        timeout: Optional[float] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        resolved_voice, resolved_gender = select_voice(language, voice=voice, gender=gender)
        valid_api_styles = {
            "news",
            "educational lecture",
            "children's stories",
            "customer care",
            "happy",
            "sad",
            "angry",
        }
        style_map = {
            "narration": "educational lecture",
            "explanatory": "educational lecture",
            "storytelling": "children's stories",
        }
        mapped_style = style_map.get(style.lower().strip(), style.lower().strip())
        instructions_obj: Dict[str, str] = {"lang": language}
        if mapped_style in valid_api_styles:
            instructions_obj["style"] = mapped_style
        instructions_str = json.dumps(instructions_obj, sort_keys=True)

        cache_key = DiskCache.compute_key(
            model="indic-speak",
            operation="speech",
            payload={
                "input": text,
                "voice": resolved_voice,
                "instructions": instructions_str,
            },
        )
        cached_bytes = self.cache.get_bytes(cache_key)
        if cached_bytes is not None:
            self.cost_tracker.record(
                model="indic-speak",
                operation="synthesize_chunk",
                units=float(len(text)),
                unit_name="characters",
                cost_inr=0.0,
                cached=True,
                latency_ms=0.5,
            )
            return cached_bytes, {
                "voice": resolved_voice,
                "gender": resolved_gender,
                "language": language,
                "style": style,
                "chars": len(text),
                "cached": True,
                "cost_inr": 0.0,
                "latency_ms": 0.5,
            }

        est_cost = CostTracker.estimate_speak_cost(len(text))
        self.cost_tracker.check_cost_budget(est_cost, description=f"indic-speak ({len(text)} chars in {language})")

        t0 = time.perf_counter()
        body: Dict[str, Any] = {
            "model": "indic-speak",
            "input": text,
            "voice": resolved_voice,
            "instructions": instructions_str,
        }
        try:
            resp = self._request_with_retry(
                method="POST",
                url_path="/audio/speech",
                model="indic-speak",
                headers={"Content-Type": "application/json"},
                json_body=body,
                timeout=timeout,
            )
        except BodhanAPIError:
            fallback_instructions = json.dumps({"lang": language})
            fallback_body = {
                "model": "indic-speak",
                "input": text,
                "voice": "Kavya" if resolved_gender == "female" else "Sourav",
                "instructions": fallback_instructions,
            }
            resp = self._request_with_retry(
                method="POST",
                url_path="/audio/speech",
                model="indic-speak",
                headers={"Content-Type": "application/json"},
                json_body=fallback_body,
                timeout=timeout,
            )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        audio_bytes = resp.content

        self.cache.set_bytes(cache_key, audio_bytes)
        actual_cost = self.cost_tracker.record(
            model="indic-speak",
            operation="synthesize_chunk",
            units=float(len(text)),
            unit_name="characters",
            cost_inr=est_cost,
            cached=False,
            latency_ms=latency_ms,
        )
        return audio_bytes, {
            "voice": resolved_voice,
            "gender": resolved_gender,
            "language": language,
            "style": style,
            "chars": len(text),
            "cached": False,
            "cost_inr": actual_cost,
            "latency_ms": latency_ms,
        }

    def ocr_image(
        self,
        image_path: Path,
        prompt: str = "Extract text and layout from this page, preserving headings, lists, Markdown tables, and LaTeX equations.",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        img_bytes = Path(image_path).read_bytes()
        cache_key = DiskCache.compute_key(
            model="indic-ocr",
            operation="ocr_page",
            payload={"prompt": prompt},
            raw_bytes=img_bytes,
        )
        cached_val = self.cache.get_json(cache_key)
        if cached_val is not None:
            self.cost_tracker.record(
                model="indic-ocr",
                operation="ocr_image",
                units=1.0,
                unit_name="images",
                cost_inr=0.0,
                cached=True,
                latency_ms=0.5,
            )
            return {**cached_val, "cached": True, "cost_inr": 0.0, "latency_ms": 0.5}

        est_cost = CostTracker.estimate_ocr_cost(1)
        self.cost_tracker.check_cost_budget(est_cost, description=f"indic-ocr ({Path(image_path).name})")

        ext = Path(image_path).suffix.lower().lstrip(".")
        mime = "image/png" if ext == "png" else "image/jpeg"
        b64_img = base64.b64encode(img_bytes).decode("utf-8")

        t0 = time.perf_counter()
        body = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_img}"},
                        },
                    ],
                }
            ],
        }
        resp = self._request_with_retry(
            method="POST",
            url_path="/chat/completions",
            model="indic-ocr",
            headers={"Content-Type": "application/json"},
            json_body=body,
            timeout=timeout,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        data = resp.json()

        choices = data.get("choices") or []
        markdown_text = ""
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            markdown_text = str(msg.get("content", "")).strip()
        elif "text" in data:
            markdown_text = str(data["text"]).strip()

        blocks = data.get("blocks") or []
        detected = detect_language_from_text(markdown_text)

        result_data = {
            "markdown": markdown_text,
            "detected_language": detected["language_code"],
            "detected_language_name": detected["language_name"],
            "detected_script": detected["script"],
            "blocks_count": len(blocks),
        }
        self.cache.set_json(cache_key, result_data)
        actual_cost = self.cost_tracker.record(
            model="indic-ocr",
            operation="ocr_image",
            units=1.0,
            unit_name="images",
            cost_inr=est_cost,
            cached=False,
            latency_ms=latency_ms,
        )
        return {**result_data, "cached": False, "cost_inr": actual_cost, "latency_ms": latency_ms}
