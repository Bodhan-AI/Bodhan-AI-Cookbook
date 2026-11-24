"""Tier-2 check: the LLM judge.

Design choices, all aimed at the "judge reliability" and "LLM never writes
Indian-language text" requirements:

* The translation is shown as numbered whitespace tokens and the judge answers
  with token *indices*. It never has to write Indic text to say where an error is,
  and a span it gives can always be mapped exactly back to the translation.
  Indices out of range are rejected, so hallucinated locations are dropped.
* The answer shape is enforced with a strict JSON schema where the endpoint
  supports one (the client falls back to JSON mode, then to plain prompting).
* Explanations are English; any Indic characters that still appear in them are
  masked by ``strip_indic`` before they reach the report.
* Temperature 0, and every answer is cached by its exact prompt.
* Replicas (K2a): the judge is asked twice with the same information in a
  different order (source-first vs translation-first, findings before/after the
  back-translation). If the two disagree on any category, a third replica breaks
  the tie (up to ``max_replicas``); an invalid answer is replaced the same way.
  Each replica is its own evidence source with weight ``replica_weight``: one
  replica alone stays below the default flag threshold (review queue), two
  agreeing replicas pass it, so fusion's noisy-OR *is* the majority vote.
* Jury (K2b): with ``models`` set, replica k goes to model k mod M, and at least
  one replica per model is asked, so every juror votes once.
* Repeat-consistency (K2c): on a deterministic sample of items the first
  replica's prompt is sent again unchanged (only the cache key differs). The
  agreement is reported apart from order-consistency and adds no evidence.
* Long translations (K2d): over ``window_tokens`` tokens, the text is judged in
  windows of whole sentences that share one sentence with their neighbours; the
  source window is the proportional range plus a margin. Indices are mapped back
  to the full text, so signals look exactly like those of a short item.
* The judge sees the deterministic findings and the back-translation, which
  counters its tendency to accept fluent output, but is told both can be wrong.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from typing import NamedTuple

from ..clients import strip_indic
from ..config import canonical_category, canonical_severity, category_area, lang_info
from ..models import ItemResult, Signal
from ..normalize import Token, segments, ws_tokens
from ..spans import span_to_token_range, tokens_to_span
from .base import Check, SkipCheck

# The categories the rubric lets the judge report, in rubric order.
ALLOWED = ("omission", "addition", "mistranslation", "number", "term", "format", "wrong_language")
UNLOCATED_OK = ("omission", "wrong_language", "format")  # may be reported without a token range
ORDERS = (
    ("SOURCE", "TRANSLATION", "BT", "FINDINGS"),
    ("TRANSLATION", "SOURCE", "FINDINGS", "BT"),
    ("FINDINGS", "SOURCE", "BT", "TRANSLATION"),
)
SOURCE_SLACK = 0.15  # a source window reaches this share of its length past the proportional range, each side


def rubric(lang_name: str) -> str:
    text = resources.files("bodhan_eval.data").joinpath("judge_rubric.md").read_text(encoding="utf-8")
    return text.replace("{lang_name}", lang_name)


def numbered(tokens: list[Token]) -> str:
    return " ".join(f"[{k}]{t.text}" for k, t in enumerate(tokens))


def verdict_schema() -> dict:
    """The rubric's answer shape as a strict JSON schema (every field required; a missing range is null)."""
    token_range = {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}
    error = {
        "type": "object",
        "additionalProperties": False,
        "required": ["category", "severity", "target_tokens", "source_tokens", "explanation", "confidence"],
        "properties": {
            "category": {"type": "string", "enum": list(ALLOWED)},
            "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
            "target_tokens": token_range,
            "source_tokens": token_range,
            "explanation": {"type": "string"},
            "confidence": {"type": "number"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["errors"],
        "properties": {"errors": {"type": "array", "items": error}},
    }


def sampled(key: str, fraction: float) -> bool:
    """Deterministic sample by item key: the same items are chosen on every run and every machine."""
    if fraction <= 0:
        return False
    if fraction >= 1:
        return True
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) / 0x1_0000_0000 < fraction


# ---------------------------------------------------------------------------
# long-text windows (K2d)
# ---------------------------------------------------------------------------


class Window(NamedTuple):
    """Token index ranges [t0, t1) of the translation and [s0, s1) of the source shown in one judge prompt."""

    t0: int
    t1: int
    s0: int
    s1: int


def _boundaries(text: str, toks: list[Token]) -> list[int]:
    """Token indices where a sentence-like segment starts, plus 0 and len(toks): every token is in one segment."""
    starts = [t.start for t in toks]
    cuts = {0, len(toks)} | {bisect_left(starts, a) for a, _ in segments(text)}
    return sorted(c for c in cuts if c <= len(toks))


def _pieces(bounds: list[int], max_tokens: int) -> list[tuple[int, int]]:
    """Group consecutive segments into windows of at most ``max_tokens`` tokens; neighbours share one segment.

    A segment longer than ``max_tokens`` (a paragraph with no sentence end) is cut into window-sized parts.
    """
    units = []
    for a, b in zip(bounds, bounds[1:]):
        units += [(x, min(b, x + max_tokens)) for x in range(a, b, max_tokens)]
    out: list[tuple[int, int]] = []
    k = 0
    while k < len(units):
        j = k
        while j + 1 < len(units) and units[j + 1][1] - units[k][0] <= max_tokens:
            j += 1
        out.append((units[k][0], units[j][1]))
        if j == len(units) - 1:
            break
        k = j if j > k else j + 1  # the last segment opens the next window, unless the window is that segment
    return out


def judge_windows(source: str, translation: str, max_tokens: int) -> list[Window]:
    s_tok, t_tok = ws_tokens(source), ws_tokens(translation)
    ns, nt = len(s_tok), len(t_tok)
    if nt <= max_tokens:
        return [Window(0, nt, 0, ns)]
    s_bounds = _boundaries(source, s_tok)
    out = []
    for t0, t1 in _pieces(_boundaries(translation, t_tok), max_tokens):
        lo, hi = t0 * ns // nt, -(-t1 * ns // nt)  # proportional range (ceil at the end)
        slack = math.ceil(SOURCE_SLACK * (hi - lo))
        lo, hi = max(0, lo - slack), min(ns, hi + slack)
        s0 = s_bounds[bisect_right(s_bounds, lo) - 1]  # widen to whole source segments
        s1 = s_bounds[bisect_left(s_bounds, hi)]
        out.append(Window(t0, t1, s0, s1))
    return out


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------


@dataclass
class _Tally:
    calls: int = 0
    invalid_outputs: int = 0  # answers that are not a JSON object with an "errors" list
    dropped: int = 0  # error entries rejected (unknown category, bad indices, no location)


@dataclass(frozen=True)
class _Ask:
    """Everything one replica needs (passed as a value: check instances hold no state, K3)."""

    s_tok: list[Token]
    t_tok: list[Token]
    windows: list[Window]
    findings: tuple[Signal, ...]  # earlier checks' signals, taken before any replica runs (replicas stay blind)
    lang_name: str
    weight: float
    max_tokens: int
    jury: bool


class JudgeCheck(Check):
    name = "judge"
    tier = 2
    description = "LLM judge with a written MQM-style rubric; answers with token-index spans; re-ordered replicas, tie-break on disagreement."

    def applies(self, r, ctx):
        return bool(r.item.translation and r.item.translation.strip()) and ctx.options.get("judge", True)

    def run(self, r: ItemResult, ctx) -> None:
        if not ctx.llm.available():
            raise SkipCheck("LLM not configured (LLM_MODEL / LLM_API_KEY)")
        cfg = ctx.judge_cfg
        src, tgt = r.item.source, self.translation(r)
        wins = judge_windows(src, tgt, int(cfg["window_tokens"]))
        if len(wins) > int(cfg["max_windows"]):
            raise SkipCheck(f"translation needs {len(wins)} judge windows; max_windows is {cfg['max_windows']}")
        models = list(cfg.get("models") or []) or [ctx.llm.model]
        ask = _Ask(
            ws_tokens(src),
            ws_tokens(tgt),
            wins,
            tuple(s for s in r.signals if s.check != self.name),
            lang_info(r.item.lang).name,
            float(cfg["replica_weight"]),
            int(cfg["max_tokens"]),
            jury=len(models) > 1,
        )
        base = max(int(cfg["replicas"]), len(models))
        cap = max(int(cfg["max_replicas"]), base)
        tally = _Tally()
        verdicts: list[list[str] | None] = []
        tie_break = False
        while len(verdicts) < cap:
            if len(verdicts) >= base:
                valid = [v for v in verdicts if v is not None]
                if len(valid) < min(base, 2):
                    pass  # an invalid answer: ask another replica
                elif _tied(valid):
                    tie_break = True
                else:
                    break
            k = len(verdicts)
            found = self._replica(r, ctx, ask, k, models[k % len(models)], tally)
            verdicts.append(None if found is None else sorted({s.category for s in found}))
            r.signals.extend(found or [])
        self._repeat(r, ctx, ask, models[0], verdicts, tally, float(cfg.get("repeat_sample", 0.0)))

        valid = [v for v in verdicts if v is not None]
        votes = Counter(c for v in valid for c in v)
        r.state["judge_verdicts"] = verdicts
        r.state["judge_majority"] = sorted(c for c, n in votes.items() if 2 * n > len(valid))
        r.metrics.update(
            judge_replicas=len(verdicts),
            judge_tie_break=tie_break,
            judge_windows=len(wins),
            judge_calls=tally.calls,
            judge_invalid_outputs=tally.invalid_outputs,
            judge_dropped_errors=tally.dropped,
            judge_invalid=tally.invalid_outputs + tally.dropped,
        )
        if len(valid) >= 2:  # order consistency: the first two valid replicas (re-ordered prompts, or two jurors)
            r.metrics["judge_agree_binary"] = bool(valid[0]) == bool(valid[1])
            r.metrics["judge_agree_categories"] = valid[0] == valid[1]

    def _repeat(self, r, ctx, ask: _Ask, model: str, verdicts, tally: _Tally, fraction: float) -> None:
        """K2c: re-send replica 1's exact prompt on a sampled item; record agreement, add no evidence."""
        if not verdicts or verdicts[0] is None or not sampled(r.item.key, fraction):
            return
        again = self._replica(r, ctx, ask, 0, model, tally, cache_tag="repeat")
        if again is not None:
            r.metrics["judge_repeat_agree"] = sorted({s.category for s in again}) == verdicts[0]

    def _replica(
        self, r: ItemResult, ctx, ask: _Ask, k: int, model: str, tally: _Tally, cache_tag: str | None = None
    ) -> list[Signal] | None:
        """One replica over all windows: its accepted signals, or None if no window gave a valid answer."""
        order = ORDERS[k % len(ORDERS)]
        signals: list[Signal] = []
        answered = False
        for w, win in enumerate(ask.windows):
            obj = ctx.llm.chat_json(
                self._messages(r, ask, win, w, order),
                max_tokens=ask.max_tokens,
                schema=verdict_schema(),
                model=model,
                cache_tag=cache_tag,
            )
            tally.calls += 1
            if obj is None or not isinstance(obj.get("errors"), list):
                tally.invalid_outputs += 1
                continue
            answered = True
            for e in obj["errors"]:
                sig = self._to_signal(r, e, ask, win, k, model)
                if sig is None:
                    tally.dropped += 1
                else:
                    signals.append(sig)
        return signals if answered else None

    def _messages(self, r: ItemResult, ask: _Ask, win: Window, w: int, order: tuple[str, ...]) -> list[dict]:
        ss, ts = ask.s_tok[win.s0 : win.s1], ask.t_tok[win.t0 : win.t1]
        blocks = {
            "SOURCE": f"SOURCE (English):\n{numbered(ss)}",
            "TRANSLATION": f"TRANSLATION ({ask.lang_name}):\n{numbered(ts)}",
            "BT": f"BACK-TRANSLATION (noisy, per segment):\n{self._back_translation(r, ts)}",
            "FINDINGS": f"AUTOMATIC FINDINGS (may be false alarms):\n{_findings(ask.findings, ss, ts, len(ask.windows) > 1)}",
        }
        user = "\n\n".join(blocks[b] for b in order)
        if len(ask.windows) > 1:
            user += (
                f"\n\nThis is part {w + 1} of {len(ask.windows)} of a longer text. The SOURCE excerpt reaches a "
                "little past the TRANSLATION excerpt at both ends: do not report source text at its edges as omitted."
            )
        user += "\n\nReturn the JSON now."
        return [{"role": "system", "content": rubric(ask.lang_name)}, {"role": "user", "content": user}]

    @staticmethod
    def _back_translation(r: ItemResult, ts: list[Token]) -> str:
        if not ts:
            return "(not available)"
        lo, hi = ts[0].start, ts[-1].end
        lines = [f"- {text}" for (a, b), text in r.state.get("bt_segments", []) if a < hi and lo < b]
        return "\n".join(lines) or "(not available)"

    def _to_signal(self, r: ItemResult, e: object, ask: _Ask, win: Window, replica: int, model: str) -> Signal | None:
        if not isinstance(e, dict):
            return None
        cat = canonical_category(str(e.get("category", "")))
        if cat not in ALLOWED:
            return None
        span = _span(e.get("target_tokens"), ask.t_tok, win.t0, win.t1)
        sspan = _span(e.get("source_tokens"), ask.s_tok, win.s0, win.s1)
        if span is None and sspan is None and cat not in UNLOCATED_OK:
            return None  # an error we cannot localise is not actionable
        try:
            conf = float(e.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        conf = max(0.0, min(1.0, conf)) if math.isfinite(conf) else 0.7
        explanation = strip_indic(str(e.get("explanation", ""))[:300])
        who = f"{model}, replica {replica + 1}" if ask.jury else f"replica {replica + 1}"
        return Signal(
            check=self.name,
            category=cat,
            score=min(1.0, ask.weight * (0.7 + 0.3 * conf)),
            evidence=f"Judge ({who}): {explanation}",
            severity=canonical_severity(e.get("severity")),
            span=span,
            source_span=sspan,
            details={"replica": replica, "confidence": round(conf, 3), "model": model},
        )


def _findings(signals: tuple[Signal, ...], ss: list[Token], ts: list[Token], windowed: bool) -> str:
    """Earlier checks' findings, located by the window's own token numbers. In a window, findings that are
    located elsewhere in the text are left out; unlocated ones are shown in every window."""
    lines = []
    for s in signals:
        if category_area(s.category) != "translation" or s.score < 0.3:
            continue
        where = []
        located = bool(s.span and s.span[1] > s.span[0]) or s.source_span is not None
        if s.span and s.span[1] > s.span[0]:
            rng = span_to_token_range(ts, s.span)
            if rng:
                where.append(f"translation tokens {rng[0]}-{rng[1]}")
        if s.source_span:
            rng = span_to_token_range(ss, s.source_span)
            if rng:
                where.append(f"source tokens {rng[0]}-{rng[1]}")
        if windowed and located and not where:
            continue
        lines.append(f"- [{s.category}] {s.evidence} ({', '.join(where) or 'location unknown'})")
    return "\n".join(lines[:20]) or "(none)"


def _span(v: object, toks: list[Token], lo: int, hi: int) -> tuple[int, int] | None:
    """A judge range [first, last], local to the window's tokens lo..hi-1 -> a character span in the full text."""
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        return None
    a, b = v
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
        return None
    n = hi - lo
    if a < 0 or b < a or a >= n:
        return None
    return tokens_to_span(toks, lo + a, lo + min(b, n - 1))


def _tied(valid: list[list[str]]) -> bool:
    """Some category was reported by exactly half of the valid replicas: no majority either way."""
    votes = Counter(c for v in valid for c in v)
    return any(2 * n == len(valid) for n in votes.values())
