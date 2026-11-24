
import json
import os
import time
import hashlib
import logging

from pathlib import Path

import requests
from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


# ============================================================
# CUSTOM EXCEPTION
# ============================================================

class BodhanAPIError(Exception):
    """Raised when a Bodhan API request fails."""
    pass


class BodhanBudgetExceeded(BodhanAPIError):
    """Raised before an API request when its estimated cost exceeds budget."""
    pass


# ============================================================
# BODHAN CLIENT
# ============================================================

class BodhanClient:
    """
    Unified client for Bodhan AI models.

    Supported models:
        1. indic-translate
        2. indic-speak
        3. indic-transcribe
    """

    def __init__(self, max_cost_inr=None):

        self.base_url = os.getenv(
            "BODHAN_BASE_URL",
            "https://api.bodhan.ai"
        ).rstrip("/")

        self.translate_key = os.getenv("BODHAN_TRANSLATE_KEY")
        self.speak_key = os.getenv("BODHAN_SPEAK_KEY")
        self.transcribe_key = os.getenv("BODHAN_TRANSCRIBE_KEY")

        self.timeout = 60
        self.max_retries = 3
        self.max_cost_inr = (
            float(max_cost_inr) if max_cost_inr is not None else None
        )
        self.usage = {
            "requests": {"translate": 0, "speak": 0, "transcribe": 0},
            "cache_hits": {"translate": 0, "speak": 0, "transcribe": 0},
            "input_tokens": 0,
            "output_tokens": 0,
            "characters": {"speak": 0},
            "audio_minutes": {"transcribe": 0.0},
            "estimated_cost_inr": 0.0,
            "unpriced_operations": [],
            "entries": [],
        }
        self.pricing = {
            "translate_input_per_1k": self._env_rate(
                "BODHAN_TRANSLATE_INPUT_INR_PER_1K_TOKENS"
            ),
            "translate_output_per_1k": self._env_rate(
                "BODHAN_TRANSLATE_OUTPUT_INR_PER_1K_TOKENS"
            ),
            "speak_per_10k_chars": self._env_rate(
                "BODHAN_SPEAK_INR_PER_10K_CHARS"
            ),
            "transcribe_per_minute": self._env_rate(
                "BODHAN_TRANSCRIBE_INR_PER_MINUTE"
            ),
        }

        self.session = requests.Session()

        # Cache successful API responses.
        self.cache = {}

        # Persistent cache directory.
        self.cache_dir = PROJECT_ROOT / "outputs" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info("BodhanClient initialized.")

    # ========================================================
    # INTERNAL HELPERS
    # ========================================================

    @staticmethod
    def _env_rate(name):
        """Read an optional non-negative INR rate from the environment."""
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return None
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number.") from exc
        if value < 0:
            raise ValueError(f"{name} cannot be negative.")
        return value

    def _estimate_request_cost(self, operation, **kwargs):
        """Conservative preflight estimate; returns None if rate is unknown."""
        if operation == "translate":
            input_rate = self.pricing["translate_input_per_1k"]
            output_rate = self.pricing["translate_output_per_1k"]
            if input_rate is None or output_rate is None:
                return None
            text = kwargs.get("text", "")
            # Approximate both prompt and completion at roughly one token per
            # four characters. Completion is budgeted at the input length.
            estimated_tokens = max(1, (len(text) + 3) // 4)
            return (estimated_tokens / 1000 * input_rate
                    + estimated_tokens / 1000 * output_rate)

        if operation == "speak":
            rate = self.pricing["speak_per_10k_chars"]
            if rate is None:
                return None
            return len(kwargs.get("text", "")) / 10000 * rate

        if operation == "transcribe":
            rate = self.pricing["transcribe_per_minute"]
            minutes = kwargs.get("audio_minutes")
            if rate is None or minutes is None:
                return None
            return minutes * rate

        raise ValueError(f"Unknown Bodhan operation: {operation}")

    def _ensure_budget(self, operation, **kwargs):
        """Block a request before network I/O if its estimated cost exceeds budget."""
        if self.max_cost_inr is None:
            return

        estimated = self._estimate_request_cost(operation, **kwargs)
        if estimated is None:
            raise BodhanBudgetExceeded(
                f"Cannot enforce ₹{self.max_cost_inr:.6f} budget: pricing is "
                f"missing for '{operation}'. Configure the corresponding "
                "BODHAN_* pricing variables in .env."
            )

        projected_total = self.usage["estimated_cost_inr"] + estimated
        if projected_total > self.max_cost_inr:
            raise BodhanBudgetExceeded(
                f"Budget would be exceeded before '{operation}' request: "
                f"estimated total ₹{projected_total:.6f} > "
                f"budget ₹{self.max_cost_inr:.6f}."
            )

    def _record_usage(self, operation, cost_inr, **metadata):
        """Record one successful, non-cached API operation."""
        if operation not in self.usage["requests"]:
            raise ValueError(f"Unknown usage operation: {operation}")

        self.usage["requests"][operation] += 1
        if cost_inr is None:
            if operation not in self.usage["unpriced_operations"]:
                self.usage["unpriced_operations"].append(operation)
        else:
            self.usage["estimated_cost_inr"] += float(cost_inr)

        self.usage["entries"].append({
            "operation": operation,
            "cost_inr": cost_inr,
            **metadata,
        })

    def get_usage_report(self):
        """Return a JSON-serializable snapshot of API usage and estimates."""
        report = json.loads(json.dumps(self.usage))
        report["estimated_cost_inr"] = round(
            float(report["estimated_cost_inr"]), 8
        )
        report["budget_inr"] = self.max_cost_inr
        report["budget_enforced"] = self.max_cost_inr is not None
        report["cost_is_partial"] = bool(report["unpriced_operations"])
        report["pricing_inr"] = dict(self.pricing)
        return report

    @staticmethod
    def _wav_duration_minutes(audio_path):
        """Read WAV duration; return None when duration cannot be determined."""
        try:
            import wave
            with wave.open(str(audio_path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                if rate <= 0:
                    return None
                return frames / rate / 60.0
        except (OSError, wave.Error, EOFError):
            return None

    def _get_headers(self, api_key, content_type=None):

        if not api_key:
            raise BodhanAPIError(
                "API key missing. Check your .env file."
            )

        headers = {
            "Authorization": f"Bearer {api_key}"
        }

        if content_type:
            headers["Content-Type"] = content_type

        return headers

    def _cache_key(self, operation, payload):

        serialized = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False
        )

        digest = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

        return f"{operation}_{digest}"

    def _read_cache(self, key):

        cache_file = self.cache_dir / f"{key}.json"

        if cache_file.exists():
            try:
                with cache_file.open(
                    "r",
                    encoding="utf-8"
                ) as file:
                    return json.load(file)

            except (OSError, json.JSONDecodeError):
                logger.warning("Could not read cache: %s", key)

        return None

    def _write_cache(self, key, data):

        cache_file = self.cache_dir / f"{key}.json"
        temporary_file = cache_file.with_suffix(".tmp")

        with temporary_file.open(
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        temporary_file.replace(cache_file)

    def _request(
        self,
        method,
        url,
        api_key,
        headers=None,
        **kwargs
    ):
        """
        Execute HTTP request with bounded retries.

        Retries:
            429: Respect Retry-After.
            5xx: Exponential backoff.
            Network errors: Exponential backoff.

        Does not retry ordinary 4xx errors.
        """

        request_headers = self._get_headers(api_key)

        if headers:
            request_headers.update(headers)

        for attempt in range(self.max_retries):

            try:

                response = self.session.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    timeout=self.timeout,
                    **kwargs
                )

                if response.status_code == 429:

                    if attempt == self.max_retries - 1:
                        break

                    retry_after = response.headers.get(
                        "Retry-After",
                        "2"
                    )

                    try:
                        wait_time = min(
                            float(retry_after),
                            30
                        )
                    except ValueError:
                        wait_time = 2

                    logger.warning(
                        "Rate limited. Retrying in %s seconds.",
                        wait_time
                    )

                    time.sleep(wait_time)
                    continue

                if response.status_code in (
                    500,
                    502,
                    503,
                    504
                ):

                    if attempt == self.max_retries - 1:
                        break

                    wait_time = 2 ** attempt

                    logger.warning(
                        "Server error %s. Retrying in %s seconds.",
                        response.status_code,
                        wait_time
                    )

                    time.sleep(wait_time)
                    continue

                response.raise_for_status()

                return response

            except requests.exceptions.HTTPError as exc:

                status = exc.response.status_code

                raise BodhanAPIError(
                    f"HTTP {status}: {exc.response.text}"
                ) from exc

            except requests.RequestException as exc:

                if attempt == self.max_retries - 1:
                    raise BodhanAPIError(
                        f"Network request failed: {exc}"
                    ) from exc

                time.sleep(2 ** attempt)

        raise BodhanAPIError(
            "Maximum retries exceeded. Check API limits and balance."
        )

    # ========================================================
    # 1. INDIC-TRANSLATE
    # ========================================================

    def translate(
        self,
        text,
        target_language
    ):
        """
        Translate text using Bodhan Indic-Translate.

        Args:
            text: Source text.
            target_language: ISO language code (hi, ta, bn, en).

        Returns:
            Dictionary containing translation and metadata.
        """

        if not isinstance(text, str) or not text.strip():
            raise ValueError("Translation input cannot be empty.")

        cache_payload = {
            "text": text,
            "target_language": target_language
        }

        key = self._cache_key(
            "translate",
            cache_payload
        )

        cached_result = self._read_cache(key)

        if cached_result is not None:
            logger.info("Translation cache hit.")
            self.usage["cache_hits"]["translate"] += 1
            return cached_result

        self._ensure_budget("translate", text=text)

        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": "indic-translate",
            "messages": [
                {
                    "role": "user",
                    "content": text
                }
            ],
            "target_language_code": target_language
        }

        logger.info(
            "Calling Indic-Translate: target=%s",
            target_language
        )

        response = self._request(
            method="POST",
            url=url,
            api_key=self.translate_key,
            headers={
                "Content-Type": "application/json"
            },
            json=payload
        )

        data = response.json()

        try:
            translation = (
                data["choices"][0]["message"]["content"]
            )

        except (KeyError, IndexError, TypeError) as exc:

            raise BodhanAPIError(
                f"Unexpected translation response: {data}"
            ) from exc

        api_usage = data.get("usage", {}) or {}
        prompt_tokens = int(api_usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(api_usage.get("completion_tokens", 0) or 0)
        usage_source = "provider"
        if prompt_tokens == 0 and completion_tokens == 0:
            # Some compatible gateways omit token usage. Keep the estimate
            # visible instead of silently reporting a zero-cost request.
            prompt_tokens = max(1, (len(text) + 3) // 4)
            completion_tokens = max(1, (len(translation) + 3) // 4)
            usage_source = "character_estimate"
        input_rate = self.pricing["translate_input_per_1k"]
        output_rate = self.pricing["translate_output_per_1k"]
        actual_cost = None
        if input_rate is not None and output_rate is not None:
            actual_cost = (
                prompt_tokens / 1000 * input_rate
                + completion_tokens / 1000 * output_rate
            )
        self.usage["input_tokens"] += prompt_tokens
        self.usage["output_tokens"] += completion_tokens
        self._record_usage(
            "translate", actual_cost,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            usage_source=usage_source,
            model=data.get("model"),
        )

        result = {
            "translation": translation,
            "model": data.get("model"),
            "usage": api_usage,
            "request_id": data.get("id"),
            "cost_inr": actual_cost,
            "usage_source": usage_source,
            "cached": False
        }

        self._write_cache(key, result)

        return result

    # ========================================================
    # 2. INDIC-SPEAK
    # ========================================================

    def speak(
        self,
        text,
        language,
        voice="Kavya",
        output_path=None
    ):
        """
        Generate speech using Bodhan Indic-Speak.

        Args:
            text: Text to convert into speech.
            language: ISO language code.
            voice: Voice identifier.
            output_path: Optional WAV output location.

        Returns:
            Dictionary containing audio metadata.
        """

        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech input cannot be empty.")

        cache_payload = {
            "text": text,
            "language": language,
            "voice": voice
        }

        key = self._cache_key(
            "speak",
            cache_payload
        )

        if output_path is None:

            output_path = (
                PROJECT_ROOT
                / "outputs"
                / f"{key}.wav"
            )

        output_path = Path(output_path)

        # Reuse previously generated audio.
        if output_path.exists():

            logger.info("Audio cache hit.")
            self.usage["cache_hits"]["speak"] += 1

            return {
                "audio_path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "cached": True
            }

        if len(text) > 1000:
            raise ValueError(
                "Speech requests must be kept short."
            )

        self._ensure_budget("speak", text=text)

        url = f"{self.base_url}/v1/audio/speech"

        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps(
                {"lang": language}
            )
        }

        logger.info(
            "Calling Indic-Speak: language=%s",
            language
        )

        response = self._request(
            method="POST",
            url=url,
            api_key=self.speak_key,
            headers={
                "Content-Type": "application/json"
            },
            json=payload
        )

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        if "audio/" not in content_type:
            raise BodhanAPIError(
                f"Expected audio response, received {content_type}"
            )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        output_path.write_bytes(response.content)

        speak_rate = self.pricing["speak_per_10k_chars"]
        speak_cost = (
            len(text) / 10000 * speak_rate
            if speak_rate is not None else None
        )
        self.usage["characters"]["speak"] += len(text)
        self._record_usage(
            "speak", speak_cost,
            characters=len(text),
            language=language,
        )

        return {
            "audio_path": str(output_path),
            "size_bytes": len(response.content),
            "content_type": content_type,
            "characters": len(text),
            "cost_inr": speak_cost,
            "cached": False
        }

    # ========================================================
    # 3. INDIC-TRANSCRIBE
    # ========================================================

    def transcribe(
        self,
        audio_path,
        language=None
    ):
        """
        Transcribe audio using Bodhan Indic-Transcribe.

        Args:
            audio_path: Path to audio file.
            language: Optional language code.

        Returns:
            Dictionary containing transcription.
        """

        audio_path = Path(audio_path)

        if not audio_path.exists():
            raise FileNotFoundError(
                f"Audio file not found: {audio_path}"
            )

        cache_payload = {
            "audio_path": str(audio_path.resolve()),
            "audio_size": audio_path.stat().st_size,
            "language": language
        }

        key = self._cache_key(
            "transcribe",
            cache_payload
        )

        cached_result = self._read_cache(key)

        if cached_result is not None:
            logger.info("Transcription cache hit.")
            self.usage["cache_hits"]["transcribe"] += 1
            return cached_result

        audio_minutes = self._wav_duration_minutes(audio_path)
        self._ensure_budget(
            "transcribe", audio_minutes=audio_minutes
        )

        url = (
            f"{self.base_url}/v1/audio/transcriptions"
        )

        logger.info(
            "Calling Indic-Transcribe: language=%s",
            language
        )

        with audio_path.open("rb") as audio_file:

            files = {
                "file": (
                    audio_path.name,
                    audio_file
                )
            }

            form_data = {
                "model": "indic-transcribe"
            }

            if language:
                form_data["language"] = language

            response = self._request(
                method="POST",
                url=url,
                api_key=self.transcribe_key,
                files=files,
                data=form_data
            )

        data = response.json()

        if "text" not in data:
            raise BodhanAPIError(
                f"Unexpected transcription response: {data}"
            )

        transcribe_rate = self.pricing["transcribe_per_minute"]
        transcribe_cost = (
            audio_minutes * transcribe_rate
            if audio_minutes is not None and transcribe_rate is not None
            else None
        )
        if audio_minutes is not None:
            self.usage["audio_minutes"]["transcribe"] += audio_minutes
        self._record_usage(
            "transcribe", transcribe_cost,
            audio_minutes=audio_minutes,
            model="indic-transcribe",
        )

        result = {
            "transcript": data["text"],
            "model": "indic-transcribe",
            "audio_minutes": audio_minutes,
            "cost_inr": transcribe_cost,
            "cached": False
        }

        self._write_cache(key, result)

        return result
