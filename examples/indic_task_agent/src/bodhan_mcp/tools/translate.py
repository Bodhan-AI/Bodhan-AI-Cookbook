"""translate_text: whole document when it fits, heading splits when it does not."""
from __future__ import annotations

from pathlib import Path

from ..core import api, cache, catalog, config, costs
from ..core import text as txt
from ..core.errors import BodhanError
from ..core.langid import identify
from ..core.schemas import Translation

_CODEMIX_BANNED = ("en", "ks-Deva", "sd-Arab")


def _validate(target: str, script: str) -> None:
    if target not in catalog.MT_LANGUAGES:
        raise catalog.unsupported_language(target, "translation", catalog.MT_LANGUAGES)
    if script not in ("native", "roman", "codemix"):
        raise BodhanError("UNSUPPORTED_SCRIPT", f"Unknown script '{script}'.", hint="Use native, roman or codemix.")
    if script != "native" and target == "en":
        raise BodhanError("UNSUPPORTED_SCRIPT", "English has no script variants; use script='native'.", hint="Only non-English targets accept roman or codemix.")
    if script == "codemix" and target in _CODEMIX_BANNED:
        raise BodhanError("UNSUPPORTED_SCRIPT", f"codemix is not available for {target}.", hint="Use native for this target.")


def translate_text(
    source_path: str,
    target_language: str,
    script: str = "native",
    out_dir: str | None = None,
    max_cost_inr: float | None = None,
    force: bool = False,
) -> Translation:
    """Translate a UTF-8 text or Markdown file into one of Bodhan's 23 supported languages (26 codes counting script variants: as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur). Source language is detected by the model, so translate from any language including English. The whole document is sent when it fits the model's window, which preserves headings, lists and tables; longer documents are split at heading and page boundaries only. Writes the translation to a file and returns its path — the translated text is never returned inline. Rejects unsupported targets before making any request."""
    src = Path(source_path)
    if not src.is_file():
        raise BodhanError("MISSING_FILE", f"{source_path} does not exist.", hint="Pass a path to an existing text file.")
    _validate(target_language, script)
    source = src.read_text(encoding="utf-8")
    if not source.strip():
        raise BodhanError("EMPTY_INPUT", f"{src.name} is empty.", hint="Nothing to translate.")
    out = Path(out_dir) if out_dir else config.default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    budget = costs.CallBudget(max_cost_inr)
    segments = txt.split_document(source, config.MT_MAX_REQUEST_CHARS)
    pieces: list[str] = []
    warnings: list[str] = []
    total_tokens = 0
    requests = 0
    truncated_any = False
    for i, segment in enumerate(segments):
        key = cache.digest("mt", target_language, script, segment)
        cached = None if force else cache.get_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("text"), str):
            pieces.append(cached["text"])
            continue
        piece = _translate_segment(segment, target_language, script, budget, warnings, i)
        requests += piece["requests"]
        total_tokens += piece["tokens"]
        truncated_any = truncated_any or piece["truncated"]
        pieces.append(piece["text"])
        if not force:
            cache.put_json(key, {"text": piece["text"], "tokens": piece["tokens"]})
    if len(segments) > 1:
        warnings.append(f"split_into_{len(segments)}_segments")
    joined = "\n\n".join(p.strip() for p in pieces)
    out_path = out / f"{src.stem}.{target_language}.txt"
    out_path.write_text(joined, encoding="utf-8")
    spent = costs.mt_cost(total_tokens) if requests else 0.0
    source_guess = identify(source)
    return Translation(
        text_path=str(out_path.resolve()),
        source_language_hint=source_guess["language"],
        target_language=target_language,
        segments=len(segments),
        requests=requests,
        source_chars=len(source),
        output_chars=len(joined),
        output_tokens=total_tokens,
        ratio=round(len(joined) / max(len(source), 1), 3),
        truncated=truncated_any,
        cost_inr=round(spent, 4),
        warnings=warnings,
    )


def _translate_segment(segment: str, target: str, script: str, budget: costs.CallBudget, warnings: list[str], index: int) -> dict:
    """One segment, with a single re-split on truncation or a too-large payload."""
    estimate = costs.mt_estimate(len(segment))
    budget.reserve(estimate, f"Translating segment {index + 1}")
    try:
        result = api.translate(segment, target, script, max_output_tokens=_token_ceiling(segment, budget))
    except BodhanError as exc:
        if exc.code == "PAYLOAD_TOO_LARGE":
            return _retry_split(segment, target, script, budget, warnings, index, "payload_too_large")
        raise
    if not result["truncated"] and result["text"].strip():
        budget.add(costs.mt_cost(result["tokens"]))
        return {"text": result["text"], "tokens": result["tokens"], "truncated": False, "requests": 1}
    return _retry_split(segment, target, script, budget, warnings, index, "truncated")


def _retry_split(segment: str, target: str, script: str, budget: costs.CallBudget, warnings: list[str], index: int, why: str) -> dict:
    half = max(len(segment) // 2, 1)
    left, right = segment[:half], segment[half:]
    if not left.strip() or not right.strip():
        raise BodhanError("TRANSLATE_TRUNCATED", f"Segment {index + 1} was truncated and cannot be split further.", hint="Shorten the source text or split it into more files.")
    warnings.append(f"{why}_segment_{index}_resplit")
    out, tokens, requests = [], 0, 0
    for part in (left, right):
        estimate = costs.mt_estimate(len(part))
        budget.reserve(estimate, f"Re-translating part of segment {index + 1}")
        result = api.translate(part, target, script, max_output_tokens=_token_ceiling(part, budget))
        requests += 1
        tokens += result["tokens"]
        budget.add(costs.mt_cost(result["tokens"]))
        if result["truncated"]:
            raise BodhanError("TRANSLATE_TRUNCATED", f"Segment {index + 1} is still truncated after one re-split.", hint="Try a shorter source file.")
        out.append(result["text"])
    return {"text": "\n\n".join(out), "tokens": tokens, "truncated": False, "requests": requests}


def _token_ceiling(segment: str, budget: costs.CallBudget) -> int:
    """Cap output tokens so the atomic reservation is the most this call can cost."""
    allowed_rupees = budget.cap - budget.spent if budget.cap is not None else None
    estimate = int(len(segment) * 1.5) + 64
    if allowed_rupees is None:
        return estimate
    ceiling = int(allowed_rupees / costs.MT_PER_10K_TOKENS * 10_000)
    return max(1, min(estimate, ceiling))
