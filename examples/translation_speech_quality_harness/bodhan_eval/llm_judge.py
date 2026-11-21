import json
import os
import re
import time
import logging
import requests

from dotenv import load_dotenv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


class LLMJudgeError(Exception):
    """Raised when the LLM judge fails."""


class LLMJudge:
    """
    Evaluates translation quality using an LLM.

    The LLM only evaluates translations.
    It must never generate, rewrite, or correct translated text.
    """

    CATEGORIES = [
        "meaning_error",
        "number_error",
        "omission",
        "addition",
        "repetition",
        "fluency",
        "grammar",
        "terminology",
        "formatting_error",
        "other"
    ]

    SEVERITIES = [
        "critical",
        "major",
        "minor",
        "none"
    ]

    def __init__(self):

        self.base_url = os.getenv(
            "LLM_BASE_URL",
            "https://api.groq.com/openai/v1"
        ).rstrip("/")

        self.model = os.getenv(
            "LLM_MODEL",
            "openai/gpt-oss-20b"
        )

        self.api_key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("GROQ_API_KEY")
        )

        self.timeout = 60
        self.max_retries = 3
        self.max_backoff = 30

        self.session = requests.Session()

        # Usage/cost accounting for the external LLM judge (e.g. Groq).
        # Rates are optional and expressed as INR per 1 million tokens.
        self.usage = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_inr": 0.0,
            "unpriced": False,
            "entries": []
        }
        self.input_rate_inr_per_1m = self._read_rate(
            "LLM_INPUT_INR_PER_1M_TOKENS"
        )
        self.output_rate_inr_per_1m = self._read_rate(
            "LLM_OUTPUT_INR_PER_1M_TOKENS"
        )

        if not self.api_key:
            raise LLMJudgeError(
                "LLM_API_KEY is missing from .env"
            )

    @staticmethod
    def _read_rate(env_name):
        raw = os.getenv(env_name)
        if raw is None or raw.strip() == "":
            return None
        try:
            value = float(raw)
            if value < 0:
                raise ValueError
            return value
        except ValueError as exc:
            raise LLMJudgeError(
                f"{env_name} must be a non-negative number."
            ) from exc

    def get_usage_report(self):
        """Return a JSON-serializable snapshot of LLM token usage and cost."""
        rates_configured = (
            self.input_rate_inr_per_1m is not None
            and self.output_rate_inr_per_1m is not None
        )
        return {
            **self.usage,
            "estimated_cost_inr": round(
                self.usage["estimated_cost_inr"], 8
            ),
            "pricing_inr": {
                "input_per_1m_tokens": self.input_rate_inr_per_1m,
                "output_per_1m_tokens": self.output_rate_inr_per_1m
            },
            "cost_is_partial": not rates_configured or self.usage["unpriced"],
            "provider": self.base_url,
            "model": self.model
        }

    def _record_usage(self, usage_data):
        """Record provider-reported token usage; never estimate token counts."""
        if not isinstance(usage_data, dict):
            self.usage["unpriced"] = True
            self.usage["entries"].append({
                "model": self.model,
                "input_tokens": None,
                "output_tokens": None,
                "cost_inr": None,
                "reason": "Provider response did not include usage metadata."
            })
            return

        input_tokens = usage_data.get(
            "prompt_tokens", usage_data.get("input_tokens")
        )
        output_tokens = usage_data.get(
            "completion_tokens", usage_data.get("output_tokens")
        )

        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens < 0
            or isinstance(output_tokens, bool)
            or not isinstance(output_tokens, int)
            or output_tokens < 0
        ):
            self.usage["unpriced"] = True
            self.usage["entries"].append({
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_inr": None,
                "reason": "Provider usage metadata was missing or invalid."
            })
            return

        self.usage["requests"] += 1
        self.usage["input_tokens"] += input_tokens
        self.usage["output_tokens"] += output_tokens

        cost = None
        if (
            self.input_rate_inr_per_1m is not None
            and self.output_rate_inr_per_1m is not None
        ):
            cost = (
                input_tokens * self.input_rate_inr_per_1m
                + output_tokens * self.output_rate_inr_per_1m
            ) / 1_000_000
            self.usage["estimated_cost_inr"] += cost
        else:
            self.usage["unpriced"] = True

        self.usage["entries"].append({
            "model": self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_inr": round(cost, 8) if cost is not None else None
        })

    # =====================================================
    # 1. PROMPT CONSTRUCTION
    # =====================================================

    @staticmethod
    def build_prompt(
        source,
        translation,
        source_language,
        target_language
    ):

        return f"""
You are a professional multilingual translation quality evaluator.

Your task is to evaluate an EXISTING translation.

You must NEVER:
- Translate text.
- Rewrite text.
- Correct text.
- Generate an alternative translation.

Evaluate these dimensions:

1. Meaning preservation
2. Completeness
3. Unsupported information
4. Number and entity preservation
5. Repetition
6. Fluency
7. Grammar
8. Terminology
9. Formatting and punctuation

SOURCE LANGUAGE: {source_language}
TARGET LANGUAGE: {target_language}

SOURCE TEXT:
{source}

TRANSLATION:
{translation}

ERROR CLASSIFICATION RULES:

Choose the most specific applicable category.

- number_error:
  Numbers, dates, amounts, percentages or quantities are
  missing, changed or incorrectly represented.

- omission:
  Important information from the source is missing
  in the translation.

- addition:
  The translation introduces unsupported information
  that is absent from the source.

- repetition:
  A word, phrase or meaningful segment is unnecessarily
  repeated in the translation.

- meaning_error:
  The translation changes, reverses or distorts the
  original meaning without fitting a more specific category.

- fluency:
  The translation is unnatural or difficult to understand.

- grammar:
  The translation contains grammatical errors.

- terminology:
  Domain-specific terminology is incorrectly used.

- formatting_error:
  Punctuation, Markdown, LaTeX or other formatting
  has been incorrectly preserved.

- other:
  An identifiable error that does not fit the categories above.

IMPORTANT CLASSIFICATION EXAMPLES:

Example 1:
Source: The customer paid Rs 500.
Translation: The customer paid Rs 600.
Category: number_error

Example 2:
Source: The company received a payment.
Translation: The company received.
Category: omission

Example 3:
Source: The delivery will arrive tomorrow.
Translation: The delivery will will arrive tomorrow.
Category: repetition

Example 4:
Source: The delivery will arrive tomorrow.
Translation: The delivery will arrive tomorrow. Additional unsupported information.
Category: addition

Example 5:
Source: The capital is New Delhi.
Translation: The capital is Mumbai.
Category: meaning_error

Do not classify a specific number error as a generic meaning_error.
Do not classify repetition as a generic meaning_error.
Do not classify unsupported additions as generic meaning_error.
Do not classify omissions as generic meaning_error when omission is the more accurate category.

SCORING RUBRIC:

- 5: Excellent. Meaning preserved and language natural.
- 4: Good. Minor issues that do not change meaning.
- 3: Acceptable. Noticeable issues but main meaning preserved.
- 2: Poor. Significant meaning or completeness problems.
- 1: Very poor. Major distortion or unusable translation.

ERROR SEVERITY:

- critical: Translation is unusable or reverses important meaning.
- major: Significant omission, addition, number error or meaning distortion.
- minor: Small grammar, fluency, terminology or formatting issue.
- none: No meaningful translation error.

Return ONLY valid JSON.

Required JSON structure:

{{
    "score": 1,
    "meaning_preserved": true,
    "fluency": 1,
    "completeness": 1,
    "hallucination": false,
    "overall_severity": "none",
    "errors": [
        {{
            "category": "number_error",
            "severity": "major",
            "span": "exact problematic text from translation",
            "explanation": "Brief explanation"
        }}
    ],
    "reasoning": "Brief explanation of the evaluation"
}}

STRICT RULES:

1. Never produce a corrected translation.
2. Never rewrite either text.
3. Never invent errors.
4. Every error span must be copied exactly from the translation.
5. If there are no errors, return an empty errors array.
6. Use only the specified categories and severity levels.
7. Evaluate meaning, not just word similarity.
8. If the translation is correct, return score 5 and severity none.
9. If unsupported information is present, set hallucination to true.
10. Return valid JSON without Markdown code fences.
"""

    # =====================================================
    # 2. API REQUEST WITH RETRIES
    # =====================================================

    def _call_llm(self, prompt):

        url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a translation quality evaluator. "
                        "You only evaluate. Never generate or correct "
                        "translations. Return valid JSON only."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0,
            "max_tokens": 1200,
            "response_format": {
                "type": "json_object"
            }
        }

        for attempt in range(self.max_retries + 1):

            try:

                response = self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )

                # Handle rate limiting.
                if response.status_code == 429:

                    if attempt >= self.max_retries:
                        raise LLMJudgeError(
                            "LLM API rate limit exceeded after retries."
                        )

                    retry_after = response.headers.get("Retry-After")

                    try:
                        wait_time = float(retry_after)
                    except (TypeError, ValueError):
                        wait_time = 2 ** attempt

                    wait_time = min(
                        max(wait_time, 1),
                        self.max_backoff
                    )

                    logger.warning(
                        "LLM rate limited. Retrying in %s seconds. "
                        "Attempt %s/%s",
                        wait_time,
                        attempt + 1,
                        self.max_retries
                    )

                    time.sleep(wait_time)
                    continue

                # Retry temporary server errors.
                if response.status_code in (500, 502, 503, 504):

                    if attempt >= self.max_retries:
                        response.raise_for_status()

                    wait_time = min(
                        2 ** attempt,
                        self.max_backoff
                    )

                    logger.warning(
                        "LLM server error %s. Retrying in %s seconds.",
                        response.status_code,
                        wait_time
                    )

                    time.sleep(wait_time)
                    continue

                response.raise_for_status()

                data = response.json()

                # Count each successful provider response exactly once.
                self._record_usage(data.get("usage"))

                content = data["choices"][0]["message"]["content"]

                if not isinstance(content, str) or not content.strip():
                    raise LLMJudgeError(
                        "LLM returned an empty response."
                    )

                return content

            except LLMJudgeError:
                raise

            except requests.RequestException as exc:

                if attempt >= self.max_retries:
                    raise LLMJudgeError(
                        f"LLM API request failed after retries: {exc}"
                    ) from exc

                wait_time = min(
                    2 ** attempt,
                    self.max_backoff
                )

                logger.warning(
                    "LLM request failed. Retrying in %s seconds.",
                    wait_time
                )

                time.sleep(wait_time)

            except (KeyError, IndexError, TypeError, ValueError) as exc:

                raise LLMJudgeError(
                    "Unexpected response format from LLM API."
                ) from exc

        raise LLMJudgeError(
            "LLM request failed unexpectedly."
        )

    # =====================================================
    # 3. JSON RESPONSE PARSING
    # =====================================================

    @staticmethod
    def parse_response(response_text):

        if not isinstance(response_text, str):
            raise LLMJudgeError(
                "LLM response must be a string."
            )

        text = response_text.strip()

        # Remove Markdown code fences if present.
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
            flags=re.IGNORECASE
        ).strip()

        try:
            result = json.loads(text)

        except json.JSONDecodeError as exc:

            # Attempt to extract a JSON object.
            match = re.search(
                r"\{.*\}",
                text,
                flags=re.DOTALL
            )

            if not match:
                raise LLMJudgeError(
                    "LLM did not return valid JSON."
                ) from exc

            try:
                result = json.loads(match.group(0))

            except json.JSONDecodeError as inner_exc:
                raise LLMJudgeError(
                    "LLM returned malformed JSON."
                ) from inner_exc

        if not isinstance(result, dict):
            raise LLMJudgeError(
                "LLM response must be a JSON object."
            )

        required_fields = [
            "score",
            "meaning_preserved",
            "fluency",
            "completeness",
            "hallucination",
            "overall_severity",
            "errors",
            "reasoning"
        ]

        for field in required_fields:

            if field not in result:
                raise LLMJudgeError(
                    f"Missing field in LLM response: {field}"
                )

        if (
            isinstance(result["score"], bool)
            or not isinstance(result["score"], int)
            or result["score"] not in range(1, 6)
        ):
            raise LLMJudgeError("Invalid overall score.")

        for field in ["meaning_preserved", "hallucination"]:

            if not isinstance(result[field], bool):
                raise LLMJudgeError(
                    f"Invalid boolean field: {field}"
                )

        for field in ["fluency", "completeness"]:

            value = result[field]

            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value not in range(1, 6)
            ):
                raise LLMJudgeError(
                    f"Invalid score for {field}."
                )

        if result["overall_severity"] not in LLMJudge.SEVERITIES:
            raise LLMJudgeError(
                "Invalid overall severity."
            )

        if not isinstance(result["errors"], list):
            raise LLMJudgeError(
                "Errors must be a list."
            )

        for error in result["errors"]:

            if not isinstance(error, dict):
                raise LLMJudgeError(
                    "Invalid error entry."
                )

            if error.get("category") not in LLMJudge.CATEGORIES:
                raise LLMJudgeError(
                    "Invalid error category."
                )

            if error.get("severity") not in LLMJudge.SEVERITIES:
                raise LLMJudgeError(
                    "Invalid error severity."
                )

            for field in ["span", "explanation"]:

                if not isinstance(error.get(field), str):
                    raise LLMJudgeError(
                        f"Invalid error field: {field}"
                    )

        return result

    # =====================================================
    # 4. COMPLETE EVALUATION
    # =====================================================

    def evaluate(
        self,
        source,
        translation,
        source_language,
        target_language
    ):

        if not source or not translation:
            raise LLMJudgeError(
                "Source and translation must not be empty."
            )

        prompt = self.build_prompt(
            source,
            translation,
            source_language,
            target_language
        )

        raw_response = self._call_llm(prompt)

        result = self.parse_response(raw_response)

        # Validate exact error spans.
        for error in result["errors"]:

            span = error["span"]

            if span and span not in translation:
                raise LLMJudgeError(
                    "An error span does not match the original translation."
                )

        # Consistency checks.
        if result["overall_severity"] == "none" and result["errors"]:
            raise LLMJudgeError(
                "LLM reported errors but overall severity is none."
            )

        if result["overall_severity"] != "none" and not result["errors"]:
            raise LLMJudgeError(
                "LLM reported an error severity but provided no errors."
            )

        result["model"] = self.model

        return result