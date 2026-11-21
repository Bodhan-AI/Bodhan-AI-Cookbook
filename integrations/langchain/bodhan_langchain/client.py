"""Centralized HTTP client for Bodhan AI services."""

from __future__ import annotations

import base64
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx

from .exceptions import (
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    BodhanValidationError,
    sanitize_sensitive_text,
)
from .models import (
    SUPPORTED_TABLE_FORMATS,
    SUPPORTED_TRANSLATION_LANGUAGES,
    OCRBlock,
    OCRResponse,
    TranslationResponse,
    validate_translation_script,
)

TransportStrategy = Literal["auto", "chat_completions", "translate_endpoint"]
VALID_TRANSPORT_STRATEGIES = frozenset({"auto", "chat_completions", "translate_endpoint"})
logger = logging.getLogger(__name__)


def _validate_positive_int(value: Any, param_name: str) -> int:
    """Validate that value is a positive integer, strictly rejecting booleans and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BodhanValidationError(f"{param_name} must be a positive integer.", parameter=param_name)
    return value


def _validate_language_code(lang: str) -> None:
    """Validate that the language code follows Bodhan's plain ISO conventions."""
    if not lang or not isinstance(lang, str):
        raise BodhanValidationError("Target language must be a non-empty string.", parameter="target_language")

    normalized = lang.strip()
    if normalized.endswith("-IN") or normalized.startswith("en-") or normalized.startswith("hi-"):
        clean_code = normalized.split("-")[0]
        raise BodhanValidationError(
            f"Invalid language code '{lang}'. Bodhan uses plain ISO codes (e.g., '{clean_code}') "
            f"without country subtags like '-IN'.",
            parameter="target_language",
        )

    if normalized not in SUPPORTED_TRANSLATION_LANGUAGES:
        raise BodhanValidationError(
            f"Unsupported target language '{lang}'. Supported languages are: "
            f"{', '.join(sorted(SUPPORTED_TRANSLATION_LANGUAGES))}",
            parameter="target_language",
        )


def _detect_image_mime(data: bytes, source_desc: str = "image") -> str:
    """Inspect raw binary bytes and return 'image/png' or 'image/jpeg', or raise BodhanValidationError."""
    if not data:
        raise BodhanValidationError(f"{source_desc} cannot be empty (0 bytes).", parameter="image")

    # WebP check: RIFF....WEBP
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        raise BodhanValidationError(
            f"Unsupported image format 'image/webp' for {source_desc}. Bodhan indic-ocr accepts PNG and JPEG images only. "
            f"Please convert your image to PNG or JPEG before submission.",
            parameter="image",
        )

    # GIF check: GIF87a or GIF89a
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        raise BodhanValidationError(
            f"Unsupported image format 'image/gif' for {source_desc}. Bodhan indic-ocr accepts PNG and JPEG images only. "
            f"Please convert your image to PNG or JPEG before submission.",
            parameter="image",
        )

    # SVG check: XML / <svg
    stripped_start = data.lstrip()[:100].lower()
    if stripped_start.startswith(b"<?xml") or stripped_start.startswith(b"<svg") or b"<svg" in stripped_start:
        raise BodhanValidationError(
            f"Unsupported image format 'image/svg+xml' for {source_desc}. Bodhan indic-ocr accepts PNG and JPEG images only. "
            f"Please convert your image to PNG or JPEG before submission.",
            parameter="image",
        )

    # Valid PNG: 8-byte magic \x89PNG\r\n\x1a\n
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # Valid JPEG: 3-byte SOI marker \xff\xd8\xff
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    raise BodhanValidationError(
        f"Invalid or unrecognized image binary signature for {source_desc}. Bodhan indic-ocr accepts valid PNG and JPEG images only.",
        parameter="image",
    )


def _prepare_image_data_url(image: bytes | str | Path) -> str:
    """Convert raw image bytes, a file path, or a base64 string into a validated data URL for indic-ocr.

    Validates:
    - Supported formats: PNG and JPEG only.
    - Explicitly rejects WebP, GIF, SVG, and unsupported formats with descriptive errors.
    - Verifies declared MIME types against decoded binary signatures.
    - Rejects empty, corrupted, or malformed image inputs.
    - Never logs base64 payload contents.
    """
    if isinstance(image, bytes):
        mime = _detect_image_mime(image, "Image bytes")
        b64 = base64.b64encode(image).decode("ascii")
        return f"data:{mime};base64,{b64}"

    if isinstance(image, Path) or (
        isinstance(image, str) and not image.startswith("data:") and len(image) < 4096 and "\n" not in image
    ):
        path = Path(image)
        if path.exists():
            if not path.is_file():
                raise BodhanValidationError(f"Path is not a regular file: '{path}'", parameter="image")
            if path.stat().st_size == 0:
                raise BodhanValidationError(f"Image file '{path.name}' is empty (0 bytes).", parameter="image")

            ext = path.suffix.lower()
            if ext in (".webp", ".gif", ".svg"):
                raise BodhanValidationError(
                    f"Unsupported image format '{ext}' for file '{path.name}'. Bodhan indic-ocr accepts PNG and JPEG images only. "
                    f"Please convert your image to PNG or JPEG before submission.",
                    parameter="image",
                )

            data = path.read_bytes()
            mime = _detect_image_mime(data, f"file '{path.name}'")
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"

        common_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".pdf")
        if isinstance(image, Path) or any(image.endswith(ext) for ext in common_exts) or "/" in str(image) or "\\" in str(image):
            raise FileNotFoundError(f"Image file not found: '{path}'")

    if isinstance(image, str):
        trimmed = image.strip()
        if not trimmed:
            raise BodhanValidationError("Image string cannot be empty.", parameter="image")

        if trimmed.startswith("data:"):
            match = re.match(r"^data:([^;,]+);base64,(.*)$", trimmed, flags=re.DOTALL)
            if not match:
                raise BodhanValidationError(
                    "Malformed data URL for image. Expected format: 'data:<mime>;base64,<payload>'.",
                    parameter="image",
                )

            declared_mime = match.group(1).strip().lower()
            payload = match.group(2).strip()

            if not payload:
                raise BodhanValidationError("Image data URL contains an empty base64 payload.", parameter="image")

            if declared_mime == "image/webp":
                raise BodhanValidationError(
                    "Unsupported image format 'image/webp' for indic-ocr. Bodhan indic-ocr accepts PNG and JPEG images only. "
                    "Please convert your image to PNG or JPEG before submission.",
                    parameter="image",
                )
            if declared_mime == "image/gif":
                raise BodhanValidationError(
                    "Unsupported image format 'image/gif' for indic-ocr. Bodhan indic-ocr accepts PNG and JPEG images only. "
                    "Please convert your image to PNG or JPEG before submission.",
                    parameter="image",
                )
            if declared_mime in ("image/svg+xml", "image/svg"):
                raise BodhanValidationError(
                    "Unsupported image format 'image/svg+xml' for indic-ocr. Bodhan indic-ocr accepts PNG and JPEG images only. "
                    "Please convert your image to PNG or JPEG before submission.",
                    parameter="image",
                )
            if declared_mime not in ("image/png", "image/jpeg", "image/jpg"):
                raise BodhanValidationError(
                    f"Unsupported MIME type '{declared_mime}' for indic-ocr. Bodhan indic-ocr accepts PNG and JPEG images only.",
                    parameter="image",
                )

            try:
                decoded = base64.b64decode(payload, validate=True)
            except Exception as err:
                raise BodhanValidationError("Invalid base64 encoding in image data URL.", parameter="image") from err

            if not decoded:
                raise BodhanValidationError("Image data URL payload decoded to 0 bytes.", parameter="image")

            detected_mime = _detect_image_mime(decoded, "data URL payload")
            expected_mime = "image/jpeg" if declared_mime == "image/jpg" else declared_mime
            if detected_mime != expected_mime:
                raise BodhanValidationError(
                    f"MIME type mismatch: declared '{declared_mime}' but image signature indicates '{detected_mime}'.",
                    parameter="image",
                )

            return f"data:{detected_mime};base64,{payload}"

        # Bare base64 string
        try:
            decoded = base64.b64decode(trimmed, validate=True)
        except Exception as err:
            raise BodhanValidationError("Invalid base64 image data.", parameter="image") from err

        if not decoded:
            raise BodhanValidationError("Image base64 payload is empty (0 bytes).", parameter="image")

        detected_mime = _detect_image_mime(decoded, "base64 string")
        return f"data:{detected_mime};base64,{trimmed}"

    raise BodhanValidationError(
        f"Unsupported image input type '{type(image).__name__}'. Expected bytes, Path, or base64 str.",
        parameter="image",
    )


def _parse_error_response(response: httpx.Response) -> BodhanAPIError:
    """Extract structured error details from an HTTP error response."""
    status_code = response.status_code
    error_message = f"Bodhan API returned HTTP {status_code}"
    error_code = None
    request_id = response.headers.get("x-request-id")
    response_body: Any = None

    try:
        data = response.json()
        response_body = data
        if isinstance(data, dict):
            if "error" in data and isinstance(data["error"], dict):
                err = data["error"]
                error_message = err.get("message") or error_message
                error_code = err.get("code")
                request_id = err.get("request_id") or request_id
            elif "detail" in data:
                error_message = str(data["detail"])
            elif "message" in data:
                error_message = str(data["message"])
    except Exception:
        # Fall back to sanitized snippet if not valid JSON
        text = response.text.strip()
        if text:
            error_message = sanitize_sensitive_text(text, max_chars=150)
        response_body = text[:500] if text else None

    error_message = sanitize_sensitive_text(error_message)

    if status_code == 401:
        return BodhanAuthenticationError(
            message=error_message or "Authentication Error: Invalid or missing API key.",
            status_code=401,
            error_code=error_code or "AUTHENTICATION_ERROR",
            request_id=request_id,
            response_body=response_body,
        )

    if status_code == 429:
        retry_after: float | None = None
        raw_retry = response.headers.get("retry-after")
        if raw_retry:
            try:
                retry_after = float(raw_retry)
            except ValueError:
                pass
        limit_requests = response.headers.get("x-ratelimit-limit-requests")
        limit_parallel = response.headers.get("x-ratelimit-limit-parallel-requests")

        return BodhanRateLimitError(
            message=error_message or "Rate limit exceeded. Please back off before retrying.",
            status_code=429,
            error_code=error_code or "RATE_LIMIT_EXCEEDED",
            request_id=request_id,
            retry_after=retry_after,
            limit_requests=limit_requests,
            limit_parallel_requests=limit_parallel,
            response_body=response_body,
        )

    if status_code == 413:
        return BodhanAPIError(
            message="Payload Too Large: The supplied document/image exceeds the allowed request size.",
            status_code=413,
            error_code=error_code or "PAYLOAD_TOO_LARGE",
            request_id=request_id,
            response_body=response_body,
        )

    return BodhanAPIError(
        message=error_message,
        status_code=status_code,
        error_code=error_code,
        request_id=request_id,
        response_body=response_body,
    )


def _parse_ocr_blocks(raw_blocks: Any) -> list[OCRBlock]:
    """Parse raw layout blocks from OCR API response, validating metadata and preserving float precision."""
    if not isinstance(raw_blocks, list):
        return []

    parsed_blocks: list[OCRBlock] = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            continue

        raw_order = b.get("order")
        if isinstance(raw_order, bool):
            order = 0
        elif isinstance(raw_order, int):
            order = raw_order
        elif isinstance(raw_order, str) and raw_order.isdigit():
            order = int(raw_order)
        else:
            order = 0

        raw_label = b.get("label")
        label = str(raw_label) if raw_label is not None else "unknown"

        raw_type = b.get("type")
        block_type = str(raw_type) if raw_type is not None else None

        raw_bbox = b.get("bbox_xyxy")
        bbox: list[float | int] | None = None
        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
            valid = True
            coords: list[float | int] = []
            for coord in raw_bbox:
                if isinstance(coord, bool) or not isinstance(coord, (int, float)) or not math.isfinite(coord):
                    valid = False
                    break
                coords.append(coord)
            if valid:
                bbox = coords

        raw_conf = b.get("conf")
        conf: float | None = None
        if raw_conf is not None and not isinstance(raw_conf, bool) and isinstance(raw_conf, (int, float)):
            if math.isfinite(raw_conf) and 0.0 <= raw_conf <= 1.0:
                conf = float(raw_conf)

        raw_text = b.get("text")
        text_str = str(raw_text) if raw_text is not None else ""

        parsed_blocks.append(
            OCRBlock(
                order=order,
                label=label,
                type=block_type,
                bbox_xyxy=bbox,
                conf=conf,
                text=text_str,
                raw=b,
            )
        )
    return parsed_blocks


class BodhanClient:
    """Synchronous HTTP client for Bodhan AI services.

    Provides high-level methods for document OCR (`indic-ocr`) and translation
    (`indic-translate`) with robust error handling, input validation, and support
    for both OpenAI-compatible `/v1/chat/completions` and legacy `/translate` endpoints.

    Usage:
        ```python
        from bodhan_langchain.client import BodhanClient

        with BodhanClient() as client:
            # Document OCR
            ocr_res = client.ocr("page.png")
            print(ocr_res.text)

            # Translation
            trans_res = client.translate("Hello world", target_language="hi")
            print(trans_res.text)
        ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        ocr_api_key: str | None = None,
        translate_api_key: str | None = None,
        base_url: str = "https://api.bodhan.ai",
        timeout: float | httpx.Timeout = 60.0,
        transport_strategy: TransportStrategy = "auto",
        http_client: httpx.Client | None = None,
    ) -> None:
        # Resolve OCR key: explicit ocr_api_key -> BODHAN_OCR_API_KEY -> explicit api_key -> BODHAN_API_KEY
        resolved_ocr = (
            (ocr_api_key.strip() if ocr_api_key and ocr_api_key.strip() else None)
            or (os.environ.get("BODHAN_OCR_API_KEY", "").strip() or None)
            or (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
        )

        # Resolve Translate key: explicit translate_api_key -> BODHAN_TRANSLATE_API_KEY -> BODHAN_TRANSLATION_API_KEY -> explicit api_key -> BODHAN_API_KEY
        resolved_translate = (
            (translate_api_key.strip() if translate_api_key and translate_api_key.strip() else None)
            or (os.environ.get("BODHAN_TRANSLATE_API_KEY", "").strip() or None)
            or (os.environ.get("BODHAN_TRANSLATION_API_KEY", "").strip() or None)
            or (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
        )

        # Generic key for backward compatibility
        resolved_generic = (
            (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
            or resolved_ocr
            or resolved_translate
        )

        if not resolved_ocr and not resolved_translate:
            raise BodhanAuthenticationError(
                "Bodhan API key is required. Pass `api_key`, `ocr_api_key`, or `translate_api_key` to BodhanClient or set the "
                "`BODHAN_OCR_API_KEY`, `BODHAN_TRANSLATE_API_KEY`, or `BODHAN_API_KEY` environment variable.",
                status_code=401,
            )

        if transport_strategy not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport_strategy '{transport_strategy}'. Must be one of: "
                f"{', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport_strategy",
            )

        self._ocr_api_key = resolved_ocr
        self._translate_api_key = resolved_translate
        self._api_key = resolved_generic or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport_strategy = transport_strategy
        self._custom_client = http_client is not None

        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "User-Agent": "Bodhan-LangChain-Client/1.0",
                },
            )

    @property
    def ocr_api_key(self) -> str | None:
        """Return the configured API key for OCR operations."""
        return self._ocr_api_key

    @property
    def translate_api_key(self) -> str | None:
        """Return the configured API key for translation operations."""
        return self._translate_api_key

    def close(self) -> None:
        """Close the underlying HTTP client if managed by this instance."""
        if not self._custom_client and hasattr(self, "_client"):
            self._client.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> BodhanClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _headers(self, auth_key: str | None = None, additional: dict[str, str] | None = None) -> dict[str, str]:
        key = auth_key or self._api_key
        base = {
            "Authorization": f"Bearer {key}",
            "User-Agent": "Bodhan-LangChain-Client/1.0",
        }
        if additional:
            base.update(additional)
        return base

    def _send_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_key: str | None = None,
    ) -> dict[str, Any]:
        req_headers = self._headers(auth_key=auth_key, additional=headers)
        if json is not None:
            req_headers["Content-Type"] = "application/json"

        url = path if path.startswith("http") else f"{self.base_url}{path}"

        try:
            resp = self._client.request(
                method=method,
                url=url,
                json=json,
                headers=req_headers,
            )
        except httpx.TimeoutException:
            raise BodhanAPIError(f"Request to '{url}' timed out after {self.timeout} seconds.") from None
        except httpx.RequestError as exc:
            raise BodhanAPIError(f"Network request to '{url}' failed: {exc.__class__.__name__}") from None

        if not resp.is_success:
            raise _parse_error_response(resp)

        try:
            return resp.json()
        except Exception:
            safe_snippet = sanitize_sensitive_text(resp.text[:200])
            raise BodhanAPIError(
                f"Malformed JSON response received from Bodhan API at '{url}': {safe_snippet}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            ) from None

    def ocr(
        self,
        image: bytes | str | Path,
        table_format: str = "markdown",
        max_tokens: int | None = None,
    ) -> OCRResponse:
        """Perform document OCR on a single scanned page image.

        Args:
            image: Image bytes, a local file path, or a base64 data URL.
            table_format: Output format for tables ('markdown' or 'html'). Defaults to 'markdown'.
            max_tokens: Optional token limit for the response.

        Returns:
            OCRResponse containing extracted text, layout blocks, and metadata.

        Raises:
            BodhanValidationError: If inputs are invalid or empty.
            BodhanAPIError: If the API returns an error or malformed payload.
        """
        if not self._ocr_api_key:
            raise BodhanAuthenticationError(
                "Bodhan OCR API key is required. Pass `ocr_api_key` (or generic `api_key`) to BodhanClient or set the "
                "`BODHAN_OCR_API_KEY` (or `BODHAN_API_KEY`) environment variable.",
                status_code=401,
            )

        if table_format not in SUPPORTED_TABLE_FORMATS:
            raise BodhanValidationError(
                f"Invalid table_format '{table_format}'. Must be one of: {', '.join(sorted(SUPPORTED_TABLE_FORMATS))}",
                parameter="table_format",
            )
        if max_tokens is not None:
            _validate_positive_int(max_tokens, "max_tokens")

        data_url = _prepare_image_data_url(image)

        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        data = self._send_request("POST", "/v1/chat/completions", json=body, auth_key=self._ocr_api_key)

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise BodhanAPIError(
                "OCR response is missing expected 'choices' array.",
                status_code=200,
                response_body=data,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            raise BodhanAPIError(
                "OCR response choices[0] is not a dictionary.",
                status_code=200,
                response_body=data,
            )

        message = choice.get("message")
        if not isinstance(message, dict):
            raise BodhanAPIError(
                "OCR response choices[0].message is missing or not a dictionary.",
                status_code=200,
                response_body=data,
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise BodhanAPIError(
                "OCR response choices[0].message is missing valid string 'content'.",
                status_code=200,
                response_body=data,
            )

        finish_reason = choice.get("finish_reason")
        truncated = bool(finish_reason == "length")

        parsed_blocks = _parse_ocr_blocks(data.get("blocks", []))

        return OCRResponse(
            text=content,
            blocks=parsed_blocks,
            model=data.get("model", "indic-ocr"),
            truncated=truncated,
            finish_reason=finish_reason,
            usage=data.get("usage"),
            raw_response=data,
        )

    def _translate_chat_completions(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        max_output_tokens: int | None = None,
    ) -> TranslationResponse:
        """Execute translation via POST /v1/chat/completions route."""
        body: dict[str, Any] = {
            "model": "indic-translate",
            "target_language": target_language,
            "messages": [
                {
                    "role": "user",
                    "content": text,
                }
            ],
        }
        if script is not None:
            body["script"] = script
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        data = self._send_request("POST", "/v1/chat/completions", json=body, auth_key=self._translate_api_key)

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise BodhanAPIError(
                "Translation response is missing expected 'choices' array.",
                status_code=200,
                response_body=data,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            raise BodhanAPIError(
                "Translation response choices[0] is not a dictionary.",
                status_code=200,
                response_body=data,
            )

        message = choice.get("message")
        if not isinstance(message, dict):
            raise BodhanAPIError(
                "Translation response choices[0].message is missing or not a dictionary.",
                status_code=200,
                response_body=data,
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise BodhanAPIError(
                "Translation response choices[0].message is missing valid string 'content'.",
                status_code=200,
                response_body=data,
            )

        finish_reason = choice.get("finish_reason")

        return TranslationResponse(
            text=content,
            target_language=target_language,
            source_language=data.get("source_language"),
            script=script,
            truncated=bool(finish_reason == "length"),
            usage=data.get("usage"),
            raw_response=data,
        )

    def _translate_plain_json(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        max_output_tokens: int | None = None,
    ) -> TranslationResponse:
        """Execute translation via POST /translate route."""
        body: dict[str, Any] = {
            "text": text,
            "target_language": target_language,
        }
        if script is not None:
            body["script"] = script
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        data = self._send_request("POST", "/translate", json=body, auth_key=self._translate_api_key)

        translation = data.get("translation")
        if not isinstance(translation, str):
            raise BodhanAPIError(
                "Translation response is missing valid string 'translation' field.",
                status_code=200,
                response_body=data,
            )

        return TranslationResponse(
            text=translation,
            target_language=data.get("target_language", target_language),
            source_language=data.get("source_language"),
            script=data.get("script", script),
            truncated=bool(data.get("truncated", False)),
            usage=data.get("usage"),
            raw_response=data,
        )

    def translate(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        transport: TransportStrategy | None = None,
        max_output_tokens: int | None = None,
        max_tokens: int | None = None,
    ) -> TranslationResponse:
        """Translate text to an Indian language or English using Bodhan AI.

        Reconciles the authoritative POST /translate endpoint with the active
        /v1/chat/completions transport (documented in GitHub Issue #34).

        Args:
            text: Input text to translate.
            target_language: ISO language code (e.g. 'hi', 'ta', 'bn', 'en').
            script: Optional script variant ('native', 'roman', 'codemix').
            transport: Endpoint transport strategy ('auto', 'chat_completions', 'translate_endpoint').
            max_output_tokens: Documented limit on generated output tokens.
            max_tokens: Backward-compatible alias for max_output_tokens.

        Returns:
            TranslationResponse containing translated text and metadata.

        Raises:
            BodhanValidationError: If language or inputs fail validation.
            BodhanAPIError: If all attempted transports fail.
        """
        if not self._translate_api_key:
            raise BodhanAuthenticationError(
                "Bodhan Translate API key is required. Pass `translate_api_key` (or generic `api_key`) to BodhanClient or set the "
                "`BODHAN_TRANSLATE_API_KEY` (or `BODHAN_API_KEY`) environment variable.",
                status_code=401,
            )

        if not text or not isinstance(text, str) or not text.strip():
            raise BodhanValidationError("Text to translate cannot be empty or whitespace.", parameter="text")

        _validate_language_code(target_language)

        effective_script = validate_translation_script(target_language, script)

        if max_output_tokens is not None:
            _validate_positive_int(max_output_tokens, "max_output_tokens")
        if max_tokens is not None:
            _validate_positive_int(max_tokens, "max_tokens")
        token_limit = max_output_tokens if max_output_tokens is not None else max_tokens

        if transport is not None and transport not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport '{transport}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport",
            )

        strategy = transport or self.transport_strategy
        if strategy not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport strategy '{strategy}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport",
            )

        if strategy == "translate_endpoint":
            return self._translate_plain_json(text, target_language, effective_script, token_limit)

        if strategy == "chat_completions":
            return self._translate_chat_completions(text, target_language, effective_script, token_limit)

        # 'auto' strategy:
        # Try the authoritative official route (POST /translate) first.
        # If the server responds with 404 (endpoint not routed / Issue #34), fall back to /v1/chat/completions.
        # Do NOT fall back on 401 (auth), 400 (validation), 429 (rate limit), or 5xx (server error).
        try:
            return self._translate_plain_json(text, target_language, effective_script, token_limit)
        except BodhanAPIError as primary_err:
            if primary_err.status_code == 404:
                logger.info("POST /translate returned 404; falling back to POST /v1/chat/completions (Issue #34).")
                try:
                    return self._translate_chat_completions(text, target_language, effective_script, token_limit)
                except BodhanAPIError as fallback_err:
                    raise BodhanAPIError(
                        f"Translation failed on both routes. Primary POST /translate: {primary_err.message}. "
                        f"Fallback POST /v1/chat/completions: {fallback_err.message}.",
                        status_code=fallback_err.status_code or primary_err.status_code,
                        request_id=fallback_err.request_id or primary_err.request_id,
                        response_body={
                            "primary_error": primary_err.response_body,
                            "fallback_error": fallback_err.response_body,
                        },
                    ) from None
            raise


class BodhanAsyncClient:
    """Asynchronous HTTP client for Bodhan AI services.

    Provides coroutine methods for asynchronous document OCR and translation.

    Usage:
        ```python
        from bodhan_langchain.client import BodhanAsyncClient

        async with BodhanAsyncClient() as client:
            ocr_res = await client.ocr("page.png")
            trans_res = await client.translate("Hello", target_language="hi")
        ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        ocr_api_key: str | None = None,
        translate_api_key: str | None = None,
        base_url: str = "https://api.bodhan.ai",
        timeout: float | httpx.Timeout = 60.0,
        transport_strategy: TransportStrategy = "auto",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # Resolve OCR key: explicit ocr_api_key -> BODHAN_OCR_API_KEY -> explicit api_key -> BODHAN_API_KEY
        resolved_ocr = (
            (ocr_api_key.strip() if ocr_api_key and ocr_api_key.strip() else None)
            or (os.environ.get("BODHAN_OCR_API_KEY", "").strip() or None)
            or (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
        )

        # Resolve Translate key: explicit translate_api_key -> BODHAN_TRANSLATE_API_KEY -> BODHAN_TRANSLATION_API_KEY -> explicit api_key -> BODHAN_API_KEY
        resolved_translate = (
            (translate_api_key.strip() if translate_api_key and translate_api_key.strip() else None)
            or (os.environ.get("BODHAN_TRANSLATE_API_KEY", "").strip() or None)
            or (os.environ.get("BODHAN_TRANSLATION_API_KEY", "").strip() or None)
            or (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
        )

        # Generic key for backward compatibility
        resolved_generic = (
            (api_key.strip() if api_key and api_key.strip() else None)
            or (os.environ.get("BODHAN_API_KEY", "").strip() or None)
            or resolved_ocr
            or resolved_translate
        )

        if not resolved_ocr and not resolved_translate:
            raise BodhanAuthenticationError(
                "Bodhan API key is required. Pass `api_key`, `ocr_api_key`, or `translate_api_key` to BodhanAsyncClient or set the "
                "`BODHAN_OCR_API_KEY`, `BODHAN_TRANSLATE_API_KEY`, or `BODHAN_API_KEY` environment variable.",
                status_code=401,
            )

        if transport_strategy not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport_strategy '{transport_strategy}'. Must be one of: "
                f"{', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport_strategy",
            )

        self._ocr_api_key = resolved_ocr
        self._translate_api_key = resolved_translate
        self._api_key = resolved_generic or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport_strategy = transport_strategy
        self._custom_client = http_client is not None

        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "User-Agent": "Bodhan-LangChain-AsyncClient/1.0",
                },
            )

    @property
    def ocr_api_key(self) -> str | None:
        """Return the configured API key for OCR operations."""
        return self._ocr_api_key

    @property
    def translate_api_key(self) -> str | None:
        """Return the configured API key for translation operations."""
        return self._translate_api_key

    async def aclose(self) -> None:
        """Close the underlying HTTP client if managed by this instance."""
        if not self._custom_client and hasattr(self, "_client"):
            await self._client.aclose()

    async def __aenter__(self) -> BodhanAsyncClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    def _headers(self, auth_key: str | None = None, additional: dict[str, str] | None = None) -> dict[str, str]:
        key = auth_key or self._api_key
        base = {
            "Authorization": f"Bearer {key}",
            "User-Agent": "Bodhan-LangChain-AsyncClient/1.0",
        }
        if additional:
            base.update(additional)
        return base

    async def _send_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_key: str | None = None,
    ) -> dict[str, Any]:
        req_headers = self._headers(auth_key=auth_key, additional=headers)
        if json is not None:
            req_headers["Content-Type"] = "application/json"

        url = path if path.startswith("http") else f"{self.base_url}{path}"

        try:
            resp = await self._client.request(
                method=method,
                url=url,
                json=json,
                headers=req_headers,
            )
        except httpx.TimeoutException:
            raise BodhanAPIError(f"Request to '{url}' timed out after {self.timeout} seconds.") from None
        except httpx.RequestError as exc:
            raise BodhanAPIError(f"Network request to '{url}' failed: {exc.__class__.__name__}") from None

        if not resp.is_success:
            raise _parse_error_response(resp)

        try:
            return resp.json()
        except Exception:
            safe_snippet = sanitize_sensitive_text(resp.text[:200])
            raise BodhanAPIError(
                f"Malformed JSON response received from Bodhan API at '{url}': {safe_snippet}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            ) from None

    async def ocr(
        self,
        image: bytes | str | Path,
        table_format: str = "markdown",
        max_tokens: int | None = None,
    ) -> OCRResponse:
        """Asynchronously perform document OCR on a single scanned page image."""
        if not self._ocr_api_key:
            raise BodhanAuthenticationError(
                "Bodhan OCR API key is required. Pass `ocr_api_key` (or generic `api_key`) to BodhanAsyncClient or set the "
                "`BODHAN_OCR_API_KEY` (or `BODHAN_API_KEY`) environment variable.",
                status_code=401,
            )

        if table_format not in SUPPORTED_TABLE_FORMATS:
            raise BodhanValidationError(
                f"Invalid table_format '{table_format}'. Must be one of: {', '.join(sorted(SUPPORTED_TABLE_FORMATS))}",
                parameter="table_format",
            )
        if max_tokens is not None:
            _validate_positive_int(max_tokens, "max_tokens")

        data_url = _prepare_image_data_url(image)

        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        data = await self._send_request("POST", "/v1/chat/completions", json=body, auth_key=self._ocr_api_key)

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise BodhanAPIError(
                "OCR response is missing expected 'choices' array.",
                status_code=200,
                response_body=data,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            raise BodhanAPIError(
                "OCR response choices[0] is not a dictionary.",
                status_code=200,
                response_body=data,
            )

        message = choice.get("message")
        if not isinstance(message, dict):
            raise BodhanAPIError(
                "OCR response choices[0].message is missing or not a dictionary.",
                status_code=200,
                response_body=data,
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise BodhanAPIError(
                "OCR response choices[0].message is missing valid string 'content'.",
                status_code=200,
                response_body=data,
            )

        finish_reason = choice.get("finish_reason")
        truncated = bool(finish_reason == "length")

        parsed_blocks = _parse_ocr_blocks(data.get("blocks", []))

        return OCRResponse(
            text=content,
            blocks=parsed_blocks,
            model=data.get("model", "indic-ocr"),
            truncated=truncated,
            finish_reason=finish_reason,
            usage=data.get("usage"),
            raw_response=data,
        )

    async def _translate_chat_completions(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        max_output_tokens: int | None = None,
    ) -> TranslationResponse:
        body: dict[str, Any] = {
            "model": "indic-translate",
            "target_language": target_language,
            "messages": [
                {
                    "role": "user",
                    "content": text,
                }
            ],
        }
        if script is not None:
            body["script"] = script
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        data = await self._send_request("POST", "/v1/chat/completions", json=body, auth_key=self._translate_api_key)

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise BodhanAPIError(
                "Translation response is missing expected 'choices' array.",
                status_code=200,
                response_body=data,
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            raise BodhanAPIError(
                "Translation response choices[0] is not a dictionary.",
                status_code=200,
                response_body=data,
            )

        message = choice.get("message")
        if not isinstance(message, dict):
            raise BodhanAPIError(
                "Translation response choices[0].message is missing or not a dictionary.",
                status_code=200,
                response_body=data,
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise BodhanAPIError(
                "Translation response choices[0].message is missing valid string 'content'.",
                status_code=200,
                response_body=data,
            )

        finish_reason = choice.get("finish_reason")

        return TranslationResponse(
            text=content,
            target_language=target_language,
            source_language=data.get("source_language"),
            script=script,
            truncated=bool(finish_reason == "length"),
            usage=data.get("usage"),
            raw_response=data,
        )

    async def _translate_plain_json(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        max_output_tokens: int | None = None,
    ) -> TranslationResponse:
        body: dict[str, Any] = {
            "text": text,
            "target_language": target_language,
        }
        if script is not None:
            body["script"] = script
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        data = await self._send_request("POST", "/translate", json=body, auth_key=self._translate_api_key)

        translation = data.get("translation")
        if not isinstance(translation, str):
            raise BodhanAPIError(
                "Translation response is missing valid string 'translation' field.",
                status_code=200,
                response_body=data,
            )

        return TranslationResponse(
            text=translation,
            target_language=data.get("target_language", target_language),
            source_language=data.get("source_language"),
            script=data.get("script", script),
            truncated=bool(data.get("truncated", False)),
            usage=data.get("usage"),
            raw_response=data,
        )

    async def translate(
        self,
        text: str,
        target_language: str,
        script: str | None = None,
        transport: TransportStrategy | None = None,
        max_output_tokens: int | None = None,
        max_tokens: int | None = None,
    ) -> TranslationResponse:
        """Asynchronously translate text to an Indian language or English."""
        if not self._translate_api_key:
            raise BodhanAuthenticationError(
                "Bodhan Translate API key is required. Pass `translate_api_key` (or generic `api_key`) to BodhanAsyncClient or set the "
                "`BODHAN_TRANSLATE_API_KEY` (or `BODHAN_API_KEY`) environment variable.",
                status_code=401,
            )

        if not text or not isinstance(text, str) or not text.strip():
            raise BodhanValidationError("Text to translate cannot be empty or whitespace.", parameter="text")

        _validate_language_code(target_language)

        effective_script = validate_translation_script(target_language, script)

        if max_output_tokens is not None:
            _validate_positive_int(max_output_tokens, "max_output_tokens")
        if max_tokens is not None:
            _validate_positive_int(max_tokens, "max_tokens")
        token_limit = max_output_tokens if max_output_tokens is not None else max_tokens

        if transport is not None and transport not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport '{transport}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport",
            )

        strategy = transport or self.transport_strategy
        if strategy not in VALID_TRANSPORT_STRATEGIES:
            raise BodhanValidationError(
                f"Invalid transport strategy '{strategy}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_STRATEGIES))}",
                parameter="transport",
            )

        if strategy == "translate_endpoint":
            return await self._translate_plain_json(text, target_language, effective_script, token_limit)

        if strategy == "chat_completions":
            return await self._translate_chat_completions(text, target_language, effective_script, token_limit)

        # 'auto' strategy:
        # Try the authoritative official route (POST /translate) first.
        # If the server responds with 404 (endpoint not routed / Issue #34), fall back to /v1/chat/completions.
        # Do NOT fall back on 401 (auth), 400 (validation), 429 (rate limit), or 5xx (server error).
        try:
            return await self._translate_plain_json(text, target_language, effective_script, token_limit)
        except BodhanAPIError as primary_err:
            if primary_err.status_code == 404:
                logger.info("POST /translate returned 404; falling back to POST /v1/chat/completions (Issue #34).")
                try:
                    return await self._translate_chat_completions(text, target_language, effective_script, token_limit)
                except BodhanAPIError as fallback_err:
                    raise BodhanAPIError(
                        f"Translation failed on both routes. Primary POST /translate: {primary_err.message}. "
                        f"Fallback POST /v1/chat/completions: {fallback_err.message}.",
                        status_code=fallback_err.status_code or primary_err.status_code,
                        request_id=fallback_err.request_id or primary_err.request_id,
                        response_body={
                            "primary_error": primary_err.response_body,
                            "fallback_error": fallback_err.response_body,
                        },
                    ) from None
            raise
