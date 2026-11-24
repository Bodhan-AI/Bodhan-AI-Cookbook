"""Indic-OCR transport and a run-local cache for exact page requests."""

from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from ..config import BackendMode
from ..inference import Inference, ProviderError, message_text, post_json, release_cuda_cache
from ..models import Usage

MODEL = "indic-ocr"
OCR_URL = "https://api.bodhan.ai/v1/chat/completions"
OCR_USAGE = Usage(1, "image", 0.20, 0.20, "estimated", "One submitted image per request")
RESPONSE_FIELDS = frozenset({
    "id", "created", "model", "object", "choices", "finish_reason", "index",
    "message", "role", "content", "usage", "prompt_tokens", "completion_tokens",
    "total_tokens", "image", "width", "height", "markdown", "blocks", "order",
    "label", "type", "bbox_xyxy", "conf", "text",
})


@dataclass(frozen=True)
class OCROptions:
    table_format: str = "html"
    max_tokens: int = 2048


@dataclass(frozen=True)
class OCRResponse:
    markdown: str
    raw: dict
    image_digest: str
    attempt_ids: tuple[str, ...]


class OCRResponseError(ProviderError):
    def __init__(self, reason: str, raw: dict):
        super().__init__("response", reason)
        self.raw = raw


def _sanitize_response(value: object, secrets: tuple[str, ...]) -> object:
    """Retain documented response fields without request data or credentials."""
    if isinstance(value, dict):
        return {key: _sanitize_response(item, secrets)
                for key, item in value.items() if key in RESPONSE_FIELDS}
    if isinstance(value, (tuple, list)):
        return [_sanitize_response(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        return value
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    return None


class LocalOCR:
    def __init__(self):
        self.parser = None
        self.options: OCROptions | None = None

    def __call__(self, png: bytes, options: OCROptions) -> dict:
        if self.parser is None or self.options != options:
            self.close()
            try:
                from bodhan_genai.ocr import IndicOCR, RecognizerConfig, TableFormat
            except ImportError:
                raise ProviderError(
                    "local_setup", "Install the optional Bodhan OCR runtime documented in README.md."
                ) from None
            config = RecognizerConfig(
                table_format=TableFormat(options.table_format), max_tokens=options.max_tokens
            )
            self.parser = IndicOCR(recognizer_config=config)
            self.options = options

        with TemporaryDirectory(prefix="bodhan-ocr-") as directory:
            image_path = Path(directory) / "page.png"
            image_path.write_bytes(png)
            page = self.parser.parse(str(image_path))
        return {**page.as_record(), "markdown": page.markdown}

    def close(self) -> None:
        parser, self.parser = self.parser, None
        self.options = None
        if parser is not None:
            parser.close()

    def release_after_oom(self) -> None:
        try:
            self.close()
        finally:
            release_cuda_cache()


class IndicOCRProvider:
    def __init__(
        self, mode: BackendMode, inference: Inference, client: httpx.Client,
        env: Mapping[str, str], local: Callable[[bytes, OCROptions], dict],
        release_local: Callable[[], None],
    ):
        self.mode = mode
        self.inference = inference
        self.client = client
        self.env = env
        self.local = local
        self.release_local = release_local
        self.cache: dict[tuple[str, str, OCROptions], OCRResponse] = {}

    def ocr(self, page_id: str, png: bytes, options: OCROptions = OCROptions()) -> OCRResponse:
        digest = sha256(png).hexdigest()
        cache_key = (page_id, digest, options)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        secrets = tuple(value for name, value in self.env.items()
                        if value and (name == "LLM_API_KEY"
                                      or name.startswith("BODHAN_") and name.endswith("_KEY")))

        def safe_response(raw: object) -> dict:
            safe = _sanitize_response(raw, secrets)
            if not isinstance(safe, dict):
                raise ProviderError("response", "OCR response must be a JSON object.")
            return safe

        def local() -> tuple[str, dict]:
            raw = safe_response(self.local(png, options))
            markdown = raw.get("markdown")
            if not isinstance(markdown, str):
                raise OCRResponseError("Local OCR response is missing its Markdown.", raw)
            return markdown, raw

        def hosted() -> tuple[str, dict]:
            key = self.env.get("BODHAN_OCR_KEY")
            if not key:
                raise ProviderError("configuration", "Set BODHAN_OCR_KEY to use hosted Indic-OCR.")
            image_url = "data:image/png;base64," + b64encode(png).decode("ascii")
            raw = post_json(self.client, OCR_URL, key, {
                "model": MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}],
                "table_format": options.table_format,
                "max_tokens": options.max_tokens,
            })
            raw = safe_response(raw)
            try:
                return message_text(raw), raw
            except ProviderError as error:
                raise OCRResponseError(str(error), raw) from None

        attempts = self.inference.attempts.attempts
        first_attempt = len(attempts)
        markdown, raw = self.inference.run_indic(
            mode=self.mode, model=MODEL, entity_ids=(page_id,), local=local,
            release_local=self.release_local, hosted=hosted, hosted_usage=OCR_USAGE,
        )
        response = OCRResponse(markdown, raw, digest,
                               tuple(attempt.id for attempt in attempts[first_attempt:]))
        self.cache[cache_key] = response
        return response
