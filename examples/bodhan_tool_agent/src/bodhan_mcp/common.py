"""Shared utilities, cache management, structured errors, and cost accounting for Bodhan MCP."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import uuid

import httpx
from pydantic import BaseModel

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_SPEND_LEDGER_PATH = Path(".cache/spend_ledger.jsonl")
DEFAULT_GLOBAL_CAP_INR = 9.0


def get_ist_now_iso() -> str:
    """Returns the current ISO 8601 timestamp in IST (+05:30)."""
    return datetime.now(IST).isoformat()


# ------------------------------------------------------------------------------
# Constants & Model Pricing (INR)
# ------------------------------------------------------------------------------

BODHAN_API_BASE = "https://api.bodhan.ai/v1"

# Published Bodhan AI API prices (INR):
# Transcribe: Rs 0.10 per audio minute
# Translate: Rs 0.20 per 10,000 output tokens
# Speak: Rs 6 per 10,000 input characters
# OCR: Rs 0.20 per image
PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR = 0.20
PRICE_OCR_PER_PAGE_INR = 0.20
PRICE_TRANSCRIBE_PER_AUDIO_MINUTE_INR = 0.10
PRICE_SPEAK_PER_10K_INPUT_CHARS_INR = 6.00

DEFAULT_MAX_INVOCATION_CALLS = 8


# ------------------------------------------------------------------------------
# Structured Error Schema
# ------------------------------------------------------------------------------


class StructuredError(BaseModel):
    code: str
    message: str
    retryable: bool = False


# ------------------------------------------------------------------------------
# Call Tracker (Budget & Invocation Limits)
# ------------------------------------------------------------------------------


class CallTracker:
    """Tracks and bounds total HTTP calls made during a single tool invocation."""

    def __init__(self, max_calls: int = DEFAULT_MAX_INVOCATION_CALLS):
        self.calls: int = 0
        self.max_calls: int = max_calls

    def record_call(self) -> bool:
        if self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True


# ------------------------------------------------------------------------------
# Cache Management
# ------------------------------------------------------------------------------


def get_cache_key(*parts: Union[str, bytes]) -> str:
    """Generates a sha256 hex digest for arbitrary string or bytes components."""
    hasher = hashlib.sha256()
    for part in parts:
        if isinstance(part, str):
            hasher.update(part.encode("utf-8"))
        elif isinstance(part, bytes):
            hasher.update(part)
        hasher.update(b"\x00")
    return hasher.hexdigest()


def read_cache(cache_key: str, cache_dir: Path) -> Optional[Dict[str, Any]]:
    """Reads a JSON cache entry from disk if present."""
    path = cache_dir / f"{cache_key}.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def write_cache(cache_key: str, data: Dict[str, Any], cache_dir: Path) -> None:
    """Writes a JSON cache entry to disk atomically."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{cache_key}.json"
        temp_path = cache_dir / f"{cache_key}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path.replace(path)
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Cost Calculation Helpers
# ------------------------------------------------------------------------------


def calculate_translate_cost_inr(completion_tokens: int) -> float:
    """Calculate cost in INR for translation (Rs 0.20 per 10,000 output tokens)."""
    return (completion_tokens / 10000.0) * PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR


def calculate_ocr_cost_inr(billed_pages: int) -> float:
    """Calculate cost in INR for OCR (Rs 0.20 per page image)."""
    return billed_pages * PRICE_OCR_PER_PAGE_INR


def calculate_transcribe_cost_inr(duration_seconds: float) -> float:
    """Calculate estimated cost in INR for transcription (Rs 0.10 per audio minute)."""
    return round((duration_seconds / 60.0) * PRICE_TRANSCRIBE_PER_AUDIO_MINUTE_INR, 6)


def calculate_speak_cost_inr(char_count: int) -> float:
    """Calculate estimated cost in INR for speech synthesis (Rs 6.00 per 10,000 input characters)."""
    return round((char_count / 10000.0) * PRICE_SPEAK_PER_10K_INPUT_CHARS_INR, 6)


# ------------------------------------------------------------------------------
# HTTP Retry & Error Handling Helper
# ------------------------------------------------------------------------------


def classify_http_response(
    response: httpx.Response, attempt: int, max_retries: int = 2
) -> Tuple[bool, float, Optional[StructuredError]]:
    """Evaluates HTTP response status and determines retryability and backoff.

    Returns:
        (should_retry, sleep_seconds, structured_error)
    """
    if response.status_code == 200:
        return False, 0.0, None

    # HTTP 415: Unsupported Media Type
    if response.status_code == 415:
        return (
            False,
            0.0,
            StructuredError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"Bodhan API rejected audio format (HTTP 415): {response.text}",
                retryable=False,
            ),
        )

    # HTTP 429: Rate limit or insufficient balance
    if response.status_code == 429:
        if attempt < max_retries:
            retry_after_hdr = response.headers.get("retry-after")
            sleep_s = float(retry_after_hdr) if retry_after_hdr else (2.0**attempt)
            return True, sleep_s, None
        return (
            False,
            0.0,
            StructuredError(
                code="RATE_LIMIT_EXCEEDED",
                message=f"Rate limit or balance exceeded (HTTP 429): {response.text}",
                retryable=True,
            ),
        )

    # HTTP 502 / 5xx: Gateway or server error
    if response.status_code == 502:
        # Check if 502 is model decoding error (do not retry blindly)
        return (
            False,
            0.0,
            StructuredError(
                code="PAGE_DECODE_ERROR" if "indic-ocr" in response.text else "BAD_GATEWAY",
                message=f"Bodhan API returned HTTP 502: {response.text}",
                retryable=False,
            ),
        )

    if 500 <= response.status_code < 600:
        if attempt < max_retries:
            return True, 1.0 * (attempt + 1), None
        return (
            False,
            0.0,
            StructuredError(
                code="SERVER_ERROR",
                message=f"Bodhan API server error (HTTP {response.status_code}): {response.text}",
                retryable=True,
            ),
        )

    # HTTP 401: Authentication
    if response.status_code == 401:
        return (
            False,
            0.0,
            StructuredError(
                code="AUTH_ERROR",
                message="Authentication Error, invalid or missing API key (HTTP 401).",
                retryable=False,
            ),
        )

    # General 4xx client errors
    return (
        False,
        0.0,
        StructuredError(
            code=f"HTTP_{response.status_code}",
            message=f"Bodhan API error (HTTP {response.status_code}): {response.text}",
            retryable=False,
        ),
    )


# ------------------------------------------------------------------------------
# Live-Call Safety Guard
# ------------------------------------------------------------------------------


def is_mock_transport(client: Optional[httpx.AsyncClient]) -> bool:
    """Detects whether an httpx.AsyncClient is using a mock transport for testing."""
    if client is None:
        return False
    transport = getattr(client, "_transport", None)
    if transport is not None and isinstance(transport, httpx.MockTransport):
        return True
    mounts = getattr(client, "_mounts", {})
    if mounts:
        for tr in mounts.values():
            if isinstance(tr, httpx.MockTransport):
                return True
    # In case unittest.mock is patching client.post or client methods
    post_fn = getattr(client, "post", None)
    if post_fn is not None and (
        hasattr(post_fn, "mock_calls") or hasattr(post_fn, "assert_called")
    ):
        return True
    return False


def check_live_calls_allowed(
    url: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[StructuredError]:
    """Safety guard: refuses real HTTP calls to api.bodhan.ai unless BODHAN_ALLOW_LIVE=1.

    Returns None if the call is allowed; otherwise returns a StructuredError
    with code LIVE_CALLS_DISABLED.
    """
    if "api.bodhan.ai" not in url:
        return None

    if is_mock_transport(client):
        return None

    if os.environ.get("BODHAN_ALLOW_LIVE") != "1":
        return StructuredError(
            code="LIVE_CALLS_DISABLED",
            message="Live calls to api.bodhan.ai are disabled. Set BODHAN_ALLOW_LIVE=1 in environment to permit paid calls.",
            retryable=False,
        )

    return None


# ------------------------------------------------------------------------------
# Spend Ledger Management & Global Cap
# ------------------------------------------------------------------------------


def get_ledger_path(override_path: Optional[Union[str, Path]] = None) -> Path:
    """Returns the effective spend ledger path, honoring overrides and env var."""
    if override_path is not None:
        return Path(override_path)
    env_path = os.getenv("BODHAN_LEDGER_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_SPEND_LEDGER_PATH


def log_spend_dispatched(
    model: str,
    estimated_cost_inr: float,
    caller: str,
    run_id: Optional[str] = None,
    call_id: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> str:
    """Writes a ledger line with status 'dispatched' BEFORE every real HTTP send.

    Returns the unique call_id for subsequent status update.
    """
    call_id = call_id or uuid.uuid4().hex[:12]
    now_ist = datetime.now(IST).isoformat()
    entry = {
        "call_id": call_id,
        "run_id": run_id or os.getenv("BODHAN_RUN_ID", "default"),
        "timestamp": now_ist,
        "model": model,
        "billed_units": 0,
        "estimated_cost_inr": round(float(estimated_cost_inr), 6),
        "estimated_rs": round(float(estimated_cost_inr), 6),
        "caller": caller,
        "cache_hit": False,
        "status": "dispatched",
        "http_status": None,
    }
    path = get_ledger_path(ledger_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return call_id


def update_spend_ledger(
    call_id: str,
    status: str,  # "completed" or "failed"
    http_status: Optional[int] = None,
    billed_units: Union[int, float] = 0,
    actual_cost_inr: Optional[float] = None,
    error_code: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> bool:
    """Updates the dispatched ledger line with completed or failed status atomically."""
    path = get_ledger_path(ledger_path)
    if not path.exists():
        return False

    lines = []
    updated = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    if data.get("call_id") == call_id:
                        data["status"] = status
                        data["http_status"] = http_status
                        if status == "completed":
                            data["billed_units"] = billed_units
                            if actual_cost_inr is not None:
                                data["estimated_cost_inr"] = round(float(actual_cost_inr), 6)
                                data["estimated_rs"] = round(float(actual_cost_inr), 6)
                        elif status == "failed":
                            if error_code:
                                data["error_code"] = error_code
                            data["estimated_cost_inr"] = 0.0
                            data["estimated_rs"] = 0.0
                        updated = True
                    lines.append(data)
                except Exception:
                    continue

        if updated:
            temp_path = path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                for item in lines:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            temp_path.replace(path)
    except Exception:
        return False

    return updated


def log_spend_cache_hit(
    model: str,
    caller: str,
    run_id: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Logs a cache hit to the ledger with Rs 0.00 and status 'completed'."""
    now_ist = datetime.now(IST).isoformat()
    entry = {
        "call_id": uuid.uuid4().hex[:12],
        "run_id": run_id or os.getenv("BODHAN_RUN_ID", "default"),
        "timestamp": now_ist,
        "model": model,
        "billed_units": 0,
        "estimated_cost_inr": 0.0,
        "estimated_rs": 0.0,
        "caller": caller,
        "cache_hit": True,
        "status": "completed",
        "http_status": 200,
    }
    path = get_ledger_path(ledger_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


def log_spend(
    model: str,
    billed_units: Union[int, float],
    estimated_cost_inr: float,
    caller: str,
    cache_hit: bool = False,
    run_id: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Backward-compatible one-shot logger for completed calls and cache hits."""
    if cache_hit:
        return log_spend_cache_hit(
            model=model, caller=caller, run_id=run_id, ledger_path=ledger_path
        )
    cid = log_spend_dispatched(
        model=model,
        estimated_cost_inr=estimated_cost_inr,
        caller=caller,
        run_id=run_id,
        ledger_path=ledger_path,
    )
    update_spend_ledger(
        call_id=cid,
        status="completed",
        http_status=200,
        billed_units=billed_units,
        actual_cost_inr=estimated_cost_inr,
        ledger_path=ledger_path,
    )
    return {
        "call_id": cid,
        "run_id": run_id or os.getenv("BODHAN_RUN_ID", "default"),
        "model": model,
        "billed_units": billed_units,
        "estimated_cost_inr": round(float(estimated_cost_inr), 6),
        "caller": caller,
        "cache_hit": False,
        "status": "completed",
    }


log_spend_event = log_spend


def total_spend_inr(
    run_id: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> float:
    """Reads the spend ledger and returns total spend in INR.

    If run_id is supplied, returns only spend for that run_id.
    If run_id is None, sums across all runs.
    Ignores entries with status == 'failed'.
    """
    if isinstance(run_id, (Path, str)) and (
        "/" in str(run_id) or "\\" in str(run_id) or str(run_id).endswith(".jsonl")
    ):
        ledger_path = run_id
        run_id = None

    path = get_ledger_path(ledger_path)
    if not path.exists():
        return 0.0
    total = 0.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("status") == "failed":
                        continue
                    if run_id is not None and data.get("run_id") != run_id:
                        continue
                    cost = data.get("estimated_cost_inr")
                    if cost is None:
                        cost = data.get("estimated_rs", 0.0)
                    total += float(cost)
                except Exception:
                    continue
    except Exception:
        return 0.0
    return round(total, 6)


def count_requests_from_ledger(
    run_id: Optional[str] = None,
    ledger_path: Optional[Union[str, Path]] = None,
) -> int:
    """Counts HTTP requests made, derived directly from the spend ledger."""
    if isinstance(run_id, (Path, str)) and (
        "/" in str(run_id) or "\\" in str(run_id) or str(run_id).endswith(".jsonl")
    ):
        ledger_path = run_id
        run_id = None

    path = get_ledger_path(ledger_path)
    if not path.exists():
        return 0
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("cache_hit") is True:
                        continue
                    if run_id is not None and data.get("run_id") != run_id:
                        continue
                    if data.get("status") in ("dispatched", "completed", "failed"):
                        count += 1
                except Exception:
                    continue
    except Exception:
        return 0
    return count


def check_global_cap_allowed(
    estimated_cost: float,
    ledger_path: Optional[Union[str, Path]] = None,
) -> Optional[StructuredError]:
    """Safety guard: refuses paid calls if total historical spend exceeds global cap."""
    raw_cap = os.getenv("BODHAN_GLOBAL_CAP_INR", str(DEFAULT_GLOBAL_CAP_INR))
    try:
        global_cap = float(raw_cap)
    except ValueError:
        global_cap = DEFAULT_GLOBAL_CAP_INR

    current_total = total_spend_inr(run_id=None, ledger_path=ledger_path)
    if current_total + estimated_cost > global_cap:
        return StructuredError(
            code="GLOBAL_CAP_EXCEEDED",
            message=f"Global spend cap of Rs {global_cap:.2f} exceeded. Total spend is Rs {current_total:.4f}, requested call costs Rs {estimated_cost:.4f}.",
            retryable=False,
        )
    return None
