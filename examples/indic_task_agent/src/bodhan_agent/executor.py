"""The executor: resolves references, prices the plan, enforces budget and step caps, records a trace."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bodhan_mcp.core import costs
from bodhan_mcp.core import text as txt
from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.core.schemas import WrittenText
from bodhan_mcp.tools.ocr import ocr_document
from bodhan_mcp.tools.probe import detect_language, probe_media
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
    for step in plan.steps:
        prior = _load_state(out_dir).get(step.id)
        if prior and prior.get("tool") == step.tool and _outputs_exist(prior.get("result", {})):
            record = StepRecord(**{**prior, "cost_inr": 0.0, "cache_hit": True})
            records.append(record)
            results[step.id] = record.result
            done.add(step.id)
            trace.emit("step_reused", step=step.id, tool=step.tool, reason="completed in an earlier run")
    remaining = [s for s in plan.steps if s.id not in done]
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
                    _save_state(out_dir, record)
                else:
                    trace.emit("step_failed", step=step.id, error=record.error)
                    raise _StepFailed(record)
            remaining = [s for s in remaining if s.id not in done]
    return records


def _state_path(out_dir: Path) -> Path:
    return out_dir.parent / "state.json"


def _load_state(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Records of steps that finished in an earlier run of this folder; only read when --resume is used."""
    if not _RESUME.get():
        return {}
    try:
        return json.loads(_state_path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(out_dir: Path, record: StepRecord) -> None:
    path = _state_path(out_dir)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state[record.id] = asdict(record)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _outputs_exist(result: dict[str, Any]) -> bool:
    paths = [result.get(k) for k in ("text_path", "markdown_path", "audio_path") if result.get(k)]
    return bool(paths) and all(Path(p).is_file() for p in paths)


_RESUME: ContextVar[bool] = ContextVar("bodhan_resume", default=False)


def set_resume(on: bool) -> None:
    _RESUME.set(on)


class _StepFailed(Exception):
    def __init__(self, record: StepRecord) -> None:
        super().__init__(record.error)
        self.record = record


def _run_step(step: Step, results: dict[str, dict[str, Any]], out_dir: Path, ledger: Ledger, trace: Trace, inventory: dict[str, Any]) -> StepRecord:
    started = time.monotonic()
    try:
        args = resolve_refs(step.args, results)
        _guard_llm_text(step, args)
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
    call_args = dict(args)
    if step.tool in _PAID_TOOLS:
        # the tool's own local ceiling is everything still unspent, so it can never exceed the run budget
        call_args["max_cost_inr"] = round(ledger.remaining() + estimate, 4)
    trace.emit("tool_call", step=step.id, tool=step.tool, intent=step.intent, args=safe_args(call_args), reserved_inr=round(estimate, 4))
    marker = _mark_pending(out_dir, step) if step.tool in _PAID_TOOLS else None
    try:
        result, cache_hit = _dispatch(step, call_args, trace)
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
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)


_CODE_ARGS = {"path", "paths", "source_path", "text_path", "audio_path", "input_path", "out_dir", "inputs", "target_language", "language", "script", "format", "gender", "style", "voice", "pages", "table_format"}


def _guard_llm_text(step: Step, args: dict[str, Any]) -> None:
    """Indic text never travels through an argument: only paths, codes and English prose are arguments."""
    for key, value in args.items():
        if key in _CODE_ARGS or not isinstance(value, str):
            continue
        if _has_indic(value):
            raise BodhanError(
                "LLM_WROTE_INDIC",
                f"Step {step.id} ({step.tool}) put Indian-language text in argument '{key}'.",
                hint="Indic text must travel as files; the model may write English only.",
            )


def _pending_dir(out_dir: Path) -> Path:
    return out_dir.parent / ".pending"


def _mark_pending(out_dir: Path, step: Step) -> Path:
    """A paid call in flight leaves a marker, so a crash cannot look like it cost nothing."""
    path = _pending_dir(out_dir) / f"{step.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"step": step.id, "tool": step.tool, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
    return path


def stale_markers(out: Path) -> list[dict[str, Any]]:
    """Markers left by an interrupted run: those calls may already have been billed."""
    found: list[dict[str, Any]] = []
    for path in sorted(_pending_dir(out / "artifacts").glob("*.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            found.append({"step": path.stem, "tool": "unknown"})
        path.unlink(missing_ok=True)
    return found


_OUT_DIR_TOOLS = {"transcribe_audio", "ocr_document", "translate_text", "speak_text"}
_PAID_TOOLS = {"transcribe_audio", "ocr_document", "translate_text", "speak_text"}


def _dispatch(step: Step, args: dict[str, Any], trace: Trace) -> tuple[Any, bool]:
    tool = step.tool
    if tool == "probe_media":
        return probe_media(args.get("paths") or [args.get("path", "")]), False
    if tool == "detect_language":
        return detect_language(args["text_path"]), False
    if tool == "transcribe_audio":
        return transcribe_audio(**args), False
    if tool == "ocr_document":
        return ocr_document(**args), False
    if tool == "translate_text":
        return translate_text(**args), False
    if tool == "speak_text":
        return speak_text(**args), False
    if tool == "write_text":
        return _write_text(args, trace, step.id), False
    raise BodhanError("LLM_INVALID_PLAN", f"Unknown tool '{tool}'.")


def _write_text(args: dict[str, Any], trace: Trace, step_id: str = "draft") -> WrittenText:
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
    reply = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=min(12000, target // 2 + 4000))
    draft = reply.text.strip()
    trace.emit("llm_call", purpose="write_text", chars_in=reply.prompt_chars, chars_out=len(draft), latency_ms=reply.latency_ms, cost_inr=0, finish_reason=reply.finish_reason)
    if not draft or _has_indic(draft):
        raise BodhanError("LLM_WROTE_INDIC", "The draft contained non-English text and was rejected.", hint="The planner will not translate it; nothing downstream was generated.")
    truncated = len(draft) > target
    if truncated:
        cut = draft.rfind(".", 0, target)
        draft = draft[: cut + 1] if cut > target // 2 else draft[:target]
    out_dir = Path(args.get("out_dir") or ".")
    out_path = out_dir / f"draft_{step_id}.md"
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
