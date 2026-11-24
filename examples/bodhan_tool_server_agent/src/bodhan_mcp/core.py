"""Shared plumbing: errors, workspace sandbox, cost ledger, disk cache, HTTP with retry."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import httpx

PRICES = {  # INR, from console.bodhan.ai docs
    "transcribe_per_min": 0.10,
    "translate_per_10k_tokens": 0.20,
    "speak_per_10k_chars": 6.0,
    "ocr_per_image": 0.20,
}
KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}
BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")
TIMEOUTS = {"transcribe": 60, "translate": 60, "speak": 90, "ocr": 90}


class ToolError(Exception):
    """Structured, actionable error. str() is JSON so MCP clients get code/message/hint."""

    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False, **details):
        self.code, self.message, self.hint, self.retryable, self.details = code, message, hint, retryable, details
        super().__init__(json.dumps(self.as_dict()))

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint,
                "retryable": self.retryable, "details": self.details}


def register_heif() -> None:
    """Let Pillow open HEIC/HEIF phone photos (no-op if the plugin is missing)."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass


# ---------- request ids (for tracing/support) ----------
_REQ_IDS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("req_ids", default=None)


def begin_request_ids() -> None:
    _REQ_IDS.set([])  # the list object is shared with child tasks, so gather()ed calls append to it


def take_request_ids() -> list[str]:
    return list(dict.fromkeys(_REQ_IDS.get() or []))[:50]


# ---------- workspace ----------
def workdir() -> Path:
    p = Path(os.environ.get("BODHAN_WORKDIR", ".bodhan_work")).resolve()
    (p / "artifacts").mkdir(parents=True, exist_ok=True)
    return p


def _roots() -> list[Path]:
    extra = [Path(x).resolve() for x in os.environ.get("BODHAN_INPUT_DIRS", "").split(os.pathsep) if x]
    return [workdir(), *extra]


def resolve_in(path: str) -> Path:
    """Resolve an input path. The agent restricts reads with BODHAN_INPUT_DIRS (listed files or folders, plus the
    workspace); a standalone server, where that variable is unset, reads any file. Secrets files (.env*) never."""
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if p.name.startswith(".env"):
        raise ToolError("PATH_NOT_ALLOWED", f"'{path}' looks like a secrets file", "Only media and text inputs can be read.")
    if "BODHAN_INPUT_DIRS" in os.environ and not any(p == r or r in p.parents for r in _roots()):
        raise ToolError("PATH_NOT_ALLOWED", f"'{path}' is outside the allowed directories",
                        "Use one of the input files you were given or a path returned by a tool.")
    if not p.is_file():
        raise ToolError("FILE_NOT_FOUND", f"file not found: {path}", "Check the path from the file list.")
    return p


def read_text(p: Path) -> str:
    """Read a text file; tolerate a UTF-8 BOM and UTF-16 (BOM) files, replace undecodable bytes."""
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def out_path(name: str) -> Path:
    name = Path(name).name or "out.bin"  # no directories: sandbox
    return workdir() / "artifacts" / name


def write_text(name: str, text: str) -> Path:
    p = out_path(name)
    p.write_text(text, encoding="utf-8")
    return p


def preview(text: str, n: int = 400) -> str:
    return text if len(text) <= n else text[:n] + "…"


# ---------- cost ledger ----------
def _ledger() -> Path:
    return workdir() / "ledger.jsonl"


def spent() -> float:
    p = _ledger()
    if not p.exists():
        return 0.0
    return sum(json.loads(line)["inr"] for line in p.read_text().splitlines() if line.strip())


def check_budget(estimate: float) -> None:
    cap = os.environ.get("BODHAN_MAX_COST_INR")
    if cap and spent() + estimate > float(cap) + 1e-9:
        raise ToolError("BUDGET_EXCEEDED",
                        f"estimated ₹{estimate:.2f} would exceed the cap (spent ₹{spent():.2f} of ₹{float(cap):.2f})",
                        "Reduce the input, choose a cheaper plan, or stop and report partial results.")


_reserved = 0.0  # money promised to calls that are in flight; asyncio is single-threaded, so check+reserve is atomic


def reserve(estimate: float) -> None:
    """Claim budget for a call about to start. Parallel calls can no longer all pass the check and overshoot the cap."""
    global _reserved
    cap = os.environ.get("BODHAN_MAX_COST_INR")
    if cap and spent() + _reserved + estimate > float(cap) + 1e-9:
        raise ToolError("BUDGET_EXCEEDED",
                        f"budget exhausted: this call needs about Rs {estimate:.3f} but only Rs {max(0.0, float(cap) - spent() - _reserved):.3f} "
                        f"of the Rs {float(cap):.2f} cap is left",
                        "Stop and report what was completed; raise --max-cost-inr to do the rest.")
    _reserved += estimate


def release(estimate: float) -> None:
    global _reserved
    _reserved = max(0.0, _reserved - estimate)


def record(model: str, inr: float, note: str = "") -> None:
    with _ledger().open("a") as f:
        f.write(json.dumps({"ts": time.time(), "model": model, "inr": inr, "note": note}) + "\n")


# ---------- disk cache ----------
def _cache_dir() -> Path:
    p = Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def key_of(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\x00")
    return h.hexdigest()


def cache_get(key: str) -> bytes | None:
    if os.environ.get("BODHAN_NO_CACHE"):
        return None
    f = _cache_dir() / key
    return f.read_bytes() if f.exists() else None


def cache_put(key: str, data: bytes) -> None:
    (_cache_dir() / key).write_bytes(data)


# ---------- HTTP with retry + concurrency limit ----------
_sems: dict[str, asyncio.Semaphore] = {}
LIMITS = {"transcribe": 4, "translate": 4, "speak": 2, "ocr": 4}


def _sem(model: str) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    k = f"{model}-{id(loop)}"
    if k not in _sems:
        _sems[k] = asyncio.Semaphore(LIMITS[model])
    return _sems[k]


def api_key(model: str) -> str:
    k = os.environ.get(KEY_ENV[model]) or os.environ.get("BODHAN_API_KEY")  # shared key: the Cookbook convention
    if not k:
        raise ToolError("MISSING_API_KEY", f"{KEY_ENV[model]} is not set",
                        f"Set {KEY_ENV[model]} (or BODHAN_API_KEY for all four models) in the environment or .env.")
    return k


async def post(model: str, path: str, *, retries: int = 5, **kw) -> httpx.Response:
    """POST to Bodhan. Backoff on 429/5xx/timeouts only. Returns a 2xx response or raises ToolError."""
    key = api_key(model)
    delay = 1.0
    last: ToolError | None = None
    for attempt in range(retries):
        try:
            async with _sem(model), httpx.AsyncClient(timeout=TIMEOUTS[model]) as c:  # slot held only while the request runs
                r = await c.post(BASE_URL + path, headers={"Authorization": f"Bearer {key}"}, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = ToolError("UPSTREAM_TIMEOUT", f"{model} request failed: {type(e).__name__}",
                             "Retry later.", retryable=True)
        else:
            rid = r.headers.get("x-request-id")
            ids = _REQ_IDS.get()
            if rid and ids is not None:
                ids.append(rid)
            if r.status_code < 300:
                return r
            detail = _err_detail(r)
            if _out_of_credit(r, detail):  # never retry: more attempts cannot help
                raise ToolError("INSUFFICIENT_CREDITS", f"{model}: account balance too low ({r.status_code}: {detail})",
                                "Add credits in the Bodhan console. Stop and report what was completed.",
                                status=r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                code = "RATE_LIMITED" if r.status_code == 429 else "UPSTREAM_ERROR"
                last = ToolError(code, f"{model} returned {r.status_code}: {detail}",
                                 "Back off and retry.", retryable=True, status=r.status_code)
                if r.status_code == 429:
                    delay = max(delay, 5.0)  # real per-minute limits: wait longer than a few seconds
                ra = r.headers.get("retry-after")
                if ra and ra.replace(".", "").isdigit():
                    delay = max(delay, float(ra))
                delay = min(delay, MAX_BACKOFF_S)  # a huge Retry-After must not stall the run
                if r.status_code == 502 and model == "ocr":
                    raise last  # caller decides (raise max_tokens)
            else:
                raise _map_4xx(model, r, detail)
        if attempt < retries - 1:
            await asyncio.sleep(delay + random.random() * 0.3)
            delay = min(delay * 2, MAX_BACKOFF_S)
    raise last  # type: ignore[misc]


MAX_BACKOFF_S = 60.0
_inflight: dict[str, asyncio.Future] = {}


async def once(key: str, factory):
    """Single-flight: concurrent identical work (same cache key) runs once and every caller gets its result."""
    if key in _inflight:
        return await asyncio.shield(_inflight[key])
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        res = await factory()
        fut.set_result(res)
        return res
    except BaseException as e:
        fut.set_exception(e)
        fut.exception()  # mark retrieved: no "never retrieved" noise when nobody else waited
        raise
    finally:
        _inflight.pop(key, None)


_CREDIT_WORDS = re.compile(r"credit|balance|quota|insufficient|payment|billing|top.?up|exceeded ?budget|over budget", re.IGNORECASE)  # Bodhan: "429 ExceededBudget: User=... over budget"


def _out_of_credit(r: httpx.Response, detail: str) -> bool:
    return r.status_code == 402 or (r.status_code in (400, 403, 429) and bool(_CREDIT_WORDS.search(detail)))


def _err_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error", j)
        return (e.get("message") or e.get("detail") or str(e))[:300] if isinstance(e, dict) else str(e)[:300]
    except Exception:
        return r.text[:200]


def _map_4xx(model: str, r: httpx.Response, detail: str) -> ToolError:
    s = r.status_code
    if s in (401, 403):
        return ToolError("AUTH_FAILED", f"{model} rejected the API key ({s})", "Check the key for this model.")
    if s == 413:
        return ToolError("TOO_LARGE", f"{model} payload too large: {detail}", "Send smaller input.")
    if s == 415:
        return ToolError("UNSUPPORTED_FORMAT", f"{model} refused the media: {detail}", "Use WAV/FLAC/OGG/MP3.")
    if s in (400, 422):
        return ToolError("INVALID_ARGUMENT", f"{model} rejected the request: {detail}", "Fix the arguments.")
    return ToolError("UPSTREAM_ERROR", f"{model} returned {s}: {detail}", "", status=s)
