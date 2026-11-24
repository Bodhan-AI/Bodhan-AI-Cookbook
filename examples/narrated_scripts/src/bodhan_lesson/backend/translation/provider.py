"""Indic-Translate model calls and usage across local and hosted backends."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from ..config import LANGUAGES, BackendMode
from ..inference import CUDA_OOM, Inference, ProviderError, is_cuda_oom, message_text, post_json, release_cuda_cache
from ..models import Usage

MODEL = "indic-translate"
GGUF_MODEL = "indic-translate-q4"
GGUF_CONTEXT_LIMIT = 4096
TRANSLATE_URL = "https://api.bodhan.ai/v1/chat/completions"
RATE_PER_OUTPUT_TOKEN = 0.20 / 10_000
UNKNOWN_USAGE = Usage(None, "output_token", RATE_PER_OUTPUT_TOKEN, None, "unknown",
                      "The provider did not report completion tokens.")


@dataclass(frozen=True)
class ModelTranslation:
    text: str
    truncated: bool = False
    output_tokens: int | None = None


@dataclass(frozen=True)
class TranslationResponse:
    text: str | None
    attempt_ids: tuple[str, ...] = ()
    reason: str | None = None


Translate = Callable[[str, str, str, str], TranslationResponse]


def _hosted_usage(result: ModelTranslation) -> Usage:
    count = result.output_tokens
    if count is None:
        return UNKNOWN_USAGE
    return Usage(count, "output_token", RATE_PER_OUTPUT_TOKEN, count * RATE_PER_OUTPUT_TOKEN,
                 "measured")


def _target_name(target: str) -> str:
    language = LANGUAGES[target]
    return language.local_translation_target or language.name


def _output_budget(text: str) -> int:
    return max(256, 2 * len(text.encode("utf-8")))


def _chat_translation(raw: dict) -> ModelTranslation:
    text = message_text(raw)
    finish = raw["choices"][0].get("finish_reason")
    if finish not in ("stop", "length"):
        raise ProviderError("response", "Translation returned an unexpected finish reason.")
    usage = raw.get("usage")
    count = usage.get("completion_tokens") if isinstance(usage, dict) else None
    tokens = count if type(count) is int and count >= 0 else None
    return ModelTranslation(text, truncated=finish == "length", output_tokens=tokens)


def gguf_endpoint(url: str) -> httpx.URL:
    """Resolve an explicit llama-server loopback origin to its completion endpoint."""
    error = "BODHAN_TRANSLATE_GGUF_URL must be a loopback HTTP origin such as http://127.0.0.1:8080."
    try:
        origin = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError(error) from None
    if (origin.scheme != "http" or origin.host not in {"127.0.0.1", "::1"}
            or origin.userinfo or origin.path not in {"", "/"} or origin.query or origin.fragment):
        raise ValueError(error)
    return origin.copy_with(path="/v1/chat/completions")


class GGUFTranslate:
    """Call a local llama-server loaded with the Indic-Translate Q4 conversion."""

    def __init__(self, client: httpx.Client, endpoint: httpx.URL) -> None:
        self.client = client
        self.endpoint = endpoint

    def __call__(self, text: str, target: str) -> ModelTranslation:
        prompt = f"Translate the following text into {_target_name(target)}:\n\n{text}"
        try:
            response = self.client.post(self.endpoint, json={
                "model": GGUF_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": _output_budget(text),
                "stream": False,
            })
        except httpx.TimeoutException:
            raise ProviderError("timeout", "The local llama-server timed out.") from None
        except httpx.RequestError:
            raise ProviderError("local_setup", "Could not reach the local llama-server. Check that it is running.") from None

        try:
            raw = response.json()
        except ValueError:
            raw = None
        if not response.is_success:
            details = raw.get("error") if isinstance(raw, dict) else None
            reason = details.get("message") if isinstance(details, dict) else None
            if response.status_code >= 500 and isinstance(reason, str) and is_cuda_oom(RuntimeError(reason)):
                raise ProviderError("cuda_oom", CUDA_OOM)
            raise ProviderError("http", f"Local llama-server returned HTTP {response.status_code}.")
        if not isinstance(raw, dict) or raw.get("model") != GGUF_MODEL:
            raise ProviderError("response", f"Local llama-server must return the {GGUF_MODEL} model alias.")
        return _chat_translation(raw)


class LocalTranslate:
    def __init__(self) -> None:
        self.engine = None

    def __call__(self, text: str, target: str) -> ModelTranslation:
        if self.engine is None:
            try:
                from bodhan_genai.mt import IndicMTEngine
            except ImportError:
                raise ProviderError(
                    "local_setup", "Install the optional Bodhan translation runtime in README.md."
                ) from None
            # The in-process backend keeps CUDA allocation errors visible to the shared router.
            self.engine = IndicMTEngine(f"bodhan-ai/{MODEL}", backend="hf", device="cuda")
        result = self.engine.translate(
            text, tgt_lang=_target_name(target), max_new_tokens=_output_budget(text),
        )
        if not result.ok:
            # The SDK puts exceptions from generation in MTResult.error. Keep that
            # message available to the router's CUDA OOM check, which sanitizes it.
            raise RuntimeError(result.error)
        return ModelTranslation(result.text)

    def close(self) -> None:
        engine, self.engine = self.engine, None
        if engine is not None:
            engine.close()

    def release_after_oom(self) -> None:
        try:
            self.close()
        finally:
            release_cuda_cache()


class IndicTranslateProvider:
    def __init__(
        self, mode: BackendMode, inference: Inference, client: httpx.Client,
        env: Mapping[str, str], local: Callable[[str, str], ModelTranslation],
        release_local: Callable[[], None], *, local_model: str | None = None,
    ) -> None:
        self.mode = mode
        self.inference = inference
        self.client = client
        self.env = env
        self.local = local
        self.release_local = release_local
        self.local_model = local_model

    def _hosted(self, text: str, target: str) -> ModelTranslation:
        key = self.env.get("BODHAN_TRANSLATE_KEY")
        if not key:
            raise ProviderError("configuration", "Set BODHAN_TRANSLATE_KEY for hosted Indic-Translate.")
        raw = post_json(self.client, TRANSLATE_URL, key, {
            "model": MODEL,
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target,
        })
        return _chat_translation(raw)

    def translate(self, unit_id: str, source: str, target: str, text: str) -> TranslationResponse:
        attempts = self.inference.attempts.attempts
        first_attempt = len(attempts)
        value = reason = None
        try:
            result = self.inference.run_indic(
                mode=self.mode, model=MODEL, entity_ids=(unit_id, f"{source}->{target}"),
                local=lambda: self.local(text, target), release_local=self.release_local,
                hosted=lambda: self._hosted(text, target), hosted_usage=UNKNOWN_USAGE,
                measure_hosted_usage=_hosted_usage, local_model=self.local_model,
            )
        except ProviderError as error:
            reason = str(error)
        else:
            secrets = (secret for key, secret in self.env.items() if secret and (
                key == "LLM_API_KEY" or key.startswith("BODHAN_") and key.endswith("_KEY")
            ))
            if result.truncated:
                reason = "Indic-Translate reported a truncated response."
            elif not result.text.strip():
                reason = "Indic-Translate returned empty text."
            elif any(secret in result.text for secret in secrets):
                reason = "Indic-Translate returned credential-like text."
            else:
                value = result.text
        return TranslationResponse(value, tuple(attempt.id for attempt in attempts[first_attempt:]), reason)
