"""The executor: resolves references, prices the plan, enforces budget and step caps, records a trace."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bodhan_mcp.core import costs
from bodhan_mcp.core import text as txt
from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.core.schemas import WrittenText
from bodhan_mcp.tools.ocr import ocr_document
from bodhan_mcp.tools.probe import probe_media
from bodhan_mcp.tools.speak import speak_text
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

from . import llm
from .planner import Plan, Step
from .trace import Trace, safe_args

MAX_GENERATED_CHARS = 12_000


@dataclass
class StepRecord:
    id: str
    intent: str
    tool: str
    args: dict[str, Any]
    ok: bool
    latency_ms: int
    cost_inr: float
    cache_hit: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


class Ledger:
    """One source of truth for money. Reserve before a call, settle after."""

    def __init__(self, cap: float, max_steps: int) -> None:
        self.cap = cap
        self.max_steps = max_steps
        self.spent = 0.0
        self.reserved = 0.0
        self.steps = 0
        self._lock = threading.Lock()

    def remaining(self) -> float:
        return max(self.cap - self.spent - self.reserved, 0.0)

    def take_step(self) -> None:
        with self._lock:
            if self.steps >= self.max_steps:
                raise BodhanError("STEP_LIMIT", f"Step limit of {self.max_steps} reached. Stopping cleanly.")
            self.steps += 1

    def reserve(self, amount: float) -> None:
        with self._lock:
            if self.spent + self.reserved + amount > self.cap + 1e-9:
                raise BodhanError(
                    "BUDGET_EXCEEDED",
                    f"The next step needs about Rs {amount:.2f}; Rs {max(self.cap - self.spent - self.reserved, 0):.2f} remains of the Rs {self.cap:.2f} budget.",
                    hint="Raise --max-cost-inr or ask for a smaller output.",
                )
            self.reserved += amount

    def settle(self, reserved: float, measured: float) -> None:
        with self._lock:
            self.reserved = max(self.reserved - reserved, 0.0)
            self.spent += measured


def price_step(step: Step, inventory: dict[str, Any]) -> float:
    """Python's own estimate: exact where the input exists, bounded from expected_chars where it does not."""
    if step.tool in ("probe_media", "detect_language"):
        return 0.0
    if step.tool == "ocr_document":
        pages = _pages_for(step.args.get("input_path", ""), step.args.get("pages"), inventory)
        return costs.ocr_cost(pages)
    if step.tool == "transcribe_audio":
        duration = inventory.get(step.args.get("audio_path", ""), {}).get("duration_s") or 0.0
        return costs.asr_cost(duration)
    if step.tool in ("translate_text", "speak_text"):
        chars = step.expected_chars or _chars_from_refs(step.args, inventory)
        if chars is None:
            raise BodhanError("LLM_INVALID_PLAN", f"Step {step.id} ({step.tool}) consumes generated text but has no expected_chars.", hint="The planner must estimate the character count for generated text.")
        chars = min(int(chars), MAX_GENERATED_CHARS)
        return costs.mt_estimate(chars) if step.tool == "translate_text" else costs.tts_cost(chars)
    if step.tool == "write_text":
        return 0.0
    raise BodhanError("LLM_INVALID_PLAN", f"Unknown tool '{step.tool}' in plan.")


def _pages_for(path: str, pages: str | None, inventory: dict[str, Any]) -> int:
    total = inventory.get(path, {}).get("pages") or 1
    return len(txt.parse_pages(pages, total))


def _chars_from_refs(args: dict[str, Any], inventory: dict[str, Any]) -> int | None:
    """For a step reading a file that already exists, size it from disk (free and exact)."""
    for key in ("source_path", "text_path"):
        value = args.get(key)
        if isinstance(value, str) and not value.startswith("$"):
            try:
                return len(Path(value).read_text(encoding="utf-8"))
            except OSError:
                return None
    return None


def resolve_refs(args: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn "$s1.text_path" into the real value; unknown references are a plan error caught before spending."""
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        resolved[key] = _resolve_one(value, results, key)
    return resolved


def _resolve_one(value: Any, results: dict[str, dict[str, Any]], key: str) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        ref = value[1:]
        step_id, _, field = ref.partition(".")
        if step_id not in results:
            raise BodhanError("LLM_INVALID_PLAN", f"Argument '{key}' references step {step_id}, which has not run.")
        if field not in results[step_id]:
            raise BodhanError("LLM_INVALID_PLAN", f"Step {step_id} has no output field '{field}'.")
        return results[step_id][field]
    if isinstance(value, list):
        return [_resolve_one(v, results, key) for v in value]
    return value


def run_plan(plan: Plan, *, out_dir: Path, ledger: Ledger, trace: Trace, inventory: dict[str, Any], records: list[StepRecord] | None = None, dry_run: bool = False) -> list[StepRecord]:
    """Run steps in dependency order; independent steps in parallel under the shared budget."""
    results: dict[str, dict[str, Any]] = {}
    records = records if records is not None else []
    done: set[str] = set()
    remaining = list(plan.steps)
    with ThreadPoolExecutor(max_workers=4) as pool:
        while remaining:
            ready = [s for s in remaining if all(d in done for d in s.depends_on)]
            if not ready:
                raise BodhanError("LLM_INVALID_PLAN", "The plan has a dependency cycle or a missing dependency.")
            if dry_run:
                done.update(s.id for s in ready)
                remaining = [s for s in remaining if s.id not in done]
                continue
            futures = {pool.submit(_run_step, s, results, out_dir, ledger, trace, inventory): s for s in ready}
            for future, step in futures.items():
                record = future.result()
                records.append(record)
                done.add(step.id)
                if record.ok:
                    results[step.id] = record.result
                else:
                    trace.emit("step_failed", step=step.id, error=record.error)
                    raise _StepFailed(record)
            remaining = [s for s in remaining if s.id not in done]
    return records


class _StepFailed(Exception):
    def __init__(self, record: StepRecord) -> None:
        super().__init__(record.error)
        self.record = record


def _run_step(step: Step, results: dict[str, dict[str, Any]], out_dir: Path, ledger: Ledger, trace: Trace, inventory: dict[str, Any]) -> StepRecord:
    started = time.monotonic()
    try:
        args = resolve_refs(step.args, results)
    except BodhanError as exc:
        return _failed(step, started, exc, {})
    if step.tool in _OUT_DIR_TOOLS:
        args.setdefault("out_dir", str(out_dir))
    estimate = price_step(step, inventory)
    ledger.take_step()
    try:
        ledger.reserve(estimate)
    except BodhanError as exc:
        return _failed(step, started, exc, args)
    trace.emit("tool_call", step=step.id, tool=step.tool, intent=step.intent, args=safe_args(args), reserved_inr=round(estimate, 4))
    if step.tool in _PAID_TOOLS:
        # the tool's own local ceiling is everything still unspent, so it can never exceed the run budget
        args["max_cost_inr"] = round(ledger.remaining() + estimate, 4)
    try:
        result, cache_hit = _dispatch(step, args, trace)
        measured = float(_dump(result).get("cost_inr", 0.0))
        ledger.settle(estimate, measured)
        summary = _summarise(result)
        trace.emit("tool_result", step=step.id, tool=step.tool, ok=True, latency_ms=int((time.monotonic() - started) * 1000), cost_inr=round(measured, 4), cache_hit=cache_hit, summary=summary)
        return StepRecord(step.id, step.intent, step.tool, args, True, int((time.monotonic() - started) * 1000), round(measured, 4), cache_hit, _dump(result))
    except BodhanError as exc:
        ledger.settle(estimate, 0.0)
        return _failed(step, started, exc, args)
    except Exception as exc:  # noqa: BLE001 - the run must still report honestly
        ledger.settle(estimate, 0.0)
        return _failed(step, started, BodhanError("INTERNAL", f"{type(exc).__name__}: {exc}"), args)


_OUT_DIR_TOOLS = {"transcribe_audio", "ocr_document", "translate_text", "speak_text"}
_PAID_TOOLS = {"transcribe_audio", "ocr_document", "translate_text", "speak_text"}


def _dispatch(step: Step, args: dict[str, Any], trace: Trace) -> tuple[Any, bool]:
    tool = step.tool
    if tool == "probe_media":
        return probe_media(args.get("paths") or [args.get("path", "")]), False
    if tool == "detect_language":
        return __import__("bodhan_mcp.tools.probe", fromlist=["detect_language"]).detect_language(args["text_path"]), False
    if tool == "transcribe_audio":
        return transcribe_audio(**args), False
    if tool == "ocr_document":
        return ocr_document(**args), False
    if tool == "translate_text":
        return translate_text(**args), False
    if tool == "speak_text":
        return speak_text(**args), False
    if tool == "write_text":
        return _write_text(args, trace), False
    raise BodhanError("LLM_INVALID_PLAN", f"Unknown tool '{tool}'.")


def _write_text(args: dict[str, Any], trace: Trace) -> WrittenText:
    inputs = [Path(p) for p in args.get("inputs", [])]
    target = int(args.get("target_chars") or 1200)
    remaining_chars = 20_000
    sources = []
    for path in inputs:
        content = path.read_text(encoding="utf-8")
        if _has_indic(content):
            raise BodhanError("LLM_WROTE_INDIC", f"{path.name} is not English; only English files may be used as prose sources.", hint="Translate it to English first.")
        remaining_chars -= len(content)
        if remaining_chars < 0:
            content = content[: max(len(content) + remaining_chars, 0)]
        sources.append(f"--- {path.name} ---\n{content}")
    system = (
        "You write English prose only. Never write any Indian-language text or non-Latin script. "
        "Use only the source material provided. Lead with the substance; no headings unless asked. "
        f"Target length: about {target} characters. Stop at a sentence boundary near that length."
    )
    user = f"Instruction: {args.get('instruction', 'Summarise the source.')}\n\n" + "\n\n".join(sources)
    reply = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=min(8000, target // 2 + 800))
    draft = reply.text.strip()
    trace.emit("llm_call", purpose="write_text", chars_in=reply.prompt_chars, chars_out=len(draft), latency_ms=reply.latency_ms, cost_inr=0, finish_reason=reply.finish_reason)
    if not draft or _has_indic(draft):
        raise BodhanError("LLM_WROTE_INDIC", "The draft contained non-English text and was rejected.", hint="The planner will not translate it; nothing downstream was generated.")
    truncated = len(draft) > target
    if truncated:
        cut = draft.rfind(".", 0, target)
        draft = draft[: cut + 1] if cut > target // 2 else draft[:target]
    out_dir = Path(args.get("out_dir") or ".")
    out_path = out_dir / f"draft_{args.get('target_chars', 'x')}.md"
    out_path.write_text(draft, encoding="utf-8")
    return WrittenText(text_path=str(out_path.resolve()), chars=len(draft), language="en", truncated=truncated)


def _has_indic(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if 0x0900 <= code <= 0x0DFF or 0x0F00 <= code <= 0x109F:
            return True
    return False


def _dump(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return {"value": str(result)}


def _summarise(result: Any) -> dict[str, Any]:
    data = _dump(result)
    keep = {"text_path", "markdown_path", "audio_path", "language_hint", "duration_s", "page_count", "chars", "source_chars", "output_chars", "ratio", "truncated", "cost_inr", "clips", "voice", "warnings", "segments", "requests"}
    summary = {k: v for k, v in data.items() if k in keep}
    if "chunks" in data:
        summary["chunks"] = len(data["chunks"])
    return summary


def _failed(step: Step, started: float, exc: BodhanError, args: dict[str, Any]) -> StepRecord:
    return StepRecord(step.id, step.intent, step.tool, args, False, int((time.monotonic() - started) * 1000), 0.0, False, {}, exc.payload()["error"])
