import json
from typing import Any

from .cache import Cache, make_key
from .judge import (
    JudgeClient,
    JudgeResult,
    append_judge_flags,
    clean_judge_reply,
    judge_item,
)
from .models import Flag, Item


_JUDGE_CATEGORIES = {"addition", "mistranslation", "omission", "term"}


def has_judge_candidates(records: list[dict[str, Any]]) -> bool:
    return any(
        record.get("terminal_state") == "scored"
        and any(
            flag.get("suppressed_reason")
            or flag.get("category") in _JUDGE_CATEGORIES
            for flag in record.get("flags", [])
        )
        for record in records
    )


def _as_flag(value: dict[str, Any]) -> Flag:
    span = value.get("span")
    source_span = value.get("source_span")
    audio_span = value.get("audio_span_s")
    return Flag(
        category=value["category"],
        severity=value["severity"],
        modality=value["modality"],
        span=tuple(span) if isinstance(span, list) else span,
        source_span=tuple(source_span) if isinstance(source_span, list) else source_span,
        audio_span_s=tuple(audio_span) if isinstance(audio_span, list) else audio_span,
        evidence=value.get("evidence", {}),
        detectors=value.get("detectors", []),
        confidence=value.get("confidence", 0.0),
        suppressed_reason=value.get("suppressed_reason"),
    )


def _serialize_flag(flag: Flag) -> dict[str, Any]:
    return {
        "category": flag.category,
        "severity": flag.severity,
        "modality": flag.modality,
        "span": list(flag.span) if flag.span is not None else None,
        "source_span": (
            list(flag.source_span) if flag.source_span is not None else None
        ),
        "audio_span_s": (
            list(flag.audio_span_s) if flag.audio_span_s is not None else None
        ),
        "evidence": flag.evidence,
        "detectors": flag.detectors,
        "confidence": flag.confidence,
        "suppressed_reason": flag.suppressed_reason,
    }


def _candidate_hints(flags: list[dict[str, Any]]) -> str:
    return "\n".join(
        "category={}; detectors={}; target_span={}".format(
            flag["category"],
            ",".join(flag.get("detectors", [])),
            flag.get("span"),
        )
        for flag in flags
        if flag.get("suppressed_reason")
        or flag.get("category") in _JUDGE_CATEGORIES
    )


def _consensus_findings(
    blind: JudgeResult,
    confirmation: JudgeResult,
) -> JudgeResult:
    blind_keys = {
        (finding["category"], tuple(finding["span"]))
        for finding in blind.findings
    }
    findings = [
        finding
        for finding in confirmation.findings
        if (finding["category"], tuple(finding["span"])) in blind_keys
    ]
    return JudgeResult(
        findings=findings,
        discarded=blind.discarded + confirmation.discarded,
        suspected_injection=(
            blind.suspected_injection or confirmation.suspected_injection
        ),
        unavailable=blind.unavailable or confirmation.unavailable,
    )


def _mark_unchecked(record: dict[str, Any], status: str) -> None:
    record["terminal_state"] = "failed_checks"
    record["skipped_reason"] = None
    record["failed_checks"] = sorted(set(record.get("failed_checks", [])) | {"judge_unavailable"})
    record["judge"] = {
        "status": status,
        "discarded": 0,
        "consensus_findings": 0,
        "suspected_injection": any(
            flag.get("evidence", {}).get("suspected_injection")
            for flag in record.get("flags", [])
        ),
    }


def _cache_result(
    cache: Cache,
    model: str,
    base_url: str,
    pass_name: str,
    item: Item,
    candidate_hints: str | None,
    asr_transcript: str | None = None,
    result: JudgeResult | None = None,
) -> JudgeResult | None:
    payload = json.dumps(
        {
            "source": item.source,
            "translation": item.translation,
            "lang": item.lang,
            "candidate_hints": candidate_hints or "",
            "asr_transcript": asr_transcript or "",
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    key = make_key(
        f"{base_url.rstrip('/')}/judge/{pass_name}",
        model,
        {"temperature": 0.0},
        payload,
    )
    if result is None:
        cached = cache.get(key)
        if not isinstance(cached, dict) or not isinstance(cached.get("findings"), list):
            return None
        sanitized = clean_judge_reply(
            json.dumps({"findings": cached["findings"]}, ensure_ascii=True),
            item.source,
            item.translation or "",
            item.lang,
        )
        return JudgeResult(
            findings=sanitized.findings,
            discarded=int(cached.get("discarded", 0)) + sanitized.discarded,
            suspected_injection=bool(cached.get("suspected_injection", False)),
            unavailable=sanitized.unavailable,
        )
    if not result.unavailable:
        safe_findings = [
            {
                key: finding[key]
                for key in (
                    "category",
                    "severity",
                    "target_quote",
                    "source_quote",
                    "rationale",
                )
            }
            for finding in result.findings
        ]
        cache.put(key, {
            "findings": safe_findings,
            "discarded": result.discarded,
            "suspected_injection": result.suspected_injection,
        })
    return result


async def run_judge_consistency(
    items: list[Item],
    records: list[dict[str, Any]],
    client: JudgeClient | None,
    cache: Cache | None,
    *,
    model: str,
    base_url: str,
    sample_size: int,
    runs: int = 3,
) -> dict[str, Any]:
    if client is None:
        return {
            "status": "not_configured",
            "sample_size": 0,
            "runs": runs,
            "flag_agreement": 0.0,
            "category_agreement": 0.0,
        }
    candidate_keys = [
        (record["id"], record["lang"])
        for record in sorted(records, key=lambda value: (value["id"], value["lang"]))
        if record.get("terminal_state") == "scored"
        and any(
            flag.get("suppressed_reason")
            or flag.get("category") in _JUDGE_CATEGORIES
            for flag in record.get("flags", [])
        )
    ]
    candidate_keys = candidate_keys[: max(0, sample_size)]
    item_map = {(item.id, item.lang): item for item in items}
    if not candidate_keys:
        return {
            "status": "insufficient_support",
            "sample_size": 0,
            "runs": runs,
            "flag_agreement": 0.0,
            "category_agreement": 0.0,
        }

    flag_agreements: list[bool] = []
    category_agreements: list[bool] = []
    unavailable_count = 0
    for key in candidate_keys:
        item = item_map.get(key)
        if item is None:
            unavailable_count += 1
            continue
        repeated: list[JudgeResult] = []
        record = next(
            value
            for value in records
            if (value["id"], value["lang"]) == key
        )
        candidate_hints = _candidate_hints(record.get("flags", []))
        for run_index in range(max(1, runs)):
            result = await _cached_judge_item(
                client,
                item,
                cache,
                model,
                base_url,
                f"consistency-{run_index}",
                candidate_hints=candidate_hints,
                cache_bypass=True,
            )
            repeated.append(result)
            unavailable_count += int(result.unavailable)
        flag_states = [bool(result.findings) for result in repeated]
        flag_agreements.append(len(set(flag_states)) == 1)
        category_states = [
            frozenset(finding["category"] for finding in result.findings)
            for result in repeated
        ]
        category_agreements.append(len(set(category_states)) == 1)
    sample_count = len(flag_agreements)
    return {
        "status": "ok" if unavailable_count == 0 and sample_count else "partial",
        "sample_size": sample_count,
        "runs": max(1, runs),
        "flag_agreement": round(
            sum(flag_agreements) / sample_count if sample_count else 0.0,
            6,
        ),
        "category_agreement": round(
            sum(category_agreements) / sample_count if sample_count else 0.0,
            6,
        ),
        "unavailable": unavailable_count,
    }


async def _cached_judge_item(
    client: JudgeClient,
    item: Item,
    cache: Cache | None,
    model: str,
    base_url: str,
    pass_name: str,
    candidate_hints: str | None = None,
    cache_bypass: bool = False,
    asr_transcript: str | None = None,
) -> JudgeResult:
    if cache is not None and not cache_bypass:
        cached = _cache_result(
            cache, model, base_url, pass_name, item, candidate_hints,
            asr_transcript,
        )
        if cached is not None:
            return cached
    result = await judge_item(
        client,
        item.source,
        item.translation or "",
        item.lang,
        candidate_hints=candidate_hints,
        asr_transcript=asr_transcript,
    )
    if cache is not None and not cache_bypass:
        _cache_result(
            cache, model, base_url, pass_name, item, candidate_hints,
            asr_transcript, result,
        )
    return result


async def run_judge_stage(
    items: list[Item],
    records: list[dict[str, Any]],
    client: JudgeClient | None,
    *,
    enabled: bool,
    cache: Cache | None = None,
    model: str = "unconfigured",
    base_url: str = "",
    cache_bypass: bool = False,
    asr_transcripts: dict[tuple[str, str], str] | None = None,
) -> None:
    item_map = {(item.id, item.lang): item for item in items}
    for record in records:
        if record.get("terminal_state") != "scored":
            continue
        original_flags = record.get("flags", [])
        candidates = [
            flag for flag in original_flags
            if flag.get("suppressed_reason")
            or flag.get("category") in _JUDGE_CATEGORIES
        ]
        if not candidates:
            continue
        item = item_map.get((record["id"], record["lang"]))
        if item is None or not item.translation:
            _mark_unchecked(record, "unavailable")
            continue
        if not enabled:
            _mark_unchecked(record, "disabled")
            record["failed_checks"] = ["judge_disabled_for_candidates"]
            continue
        if client is None:
            _mark_unchecked(record, "not_configured")
            continue

        blind = await _cached_judge_item(
            client,
            item,
            cache,
            model,
            base_url,
            "blind",
            cache_bypass=cache_bypass,
            asr_transcript=(asr_transcripts or {}).get((item.id, item.lang)),
        )
        if blind.unavailable:
            _mark_unchecked(record, "unavailable")
            continue
        confirmation = await _cached_judge_item(
            client,
            item,
            cache,
            model,
            base_url,
            "confirmation",
            candidate_hints=_candidate_hints(candidates),
            cache_bypass=cache_bypass,
            asr_transcript=(asr_transcripts or {}).get((item.id, item.lang)),
        )
        if confirmation.unavailable:
            _mark_unchecked(record, "unavailable")
            continue

        consensus = _consensus_findings(blind, confirmation)
        deterministic_flags = [_as_flag(flag) for flag in original_flags]
        merged_flags = append_judge_flags(deterministic_flags, consensus)
        record["flags"] = [
            _serialize_flag(flag) for flag in merged_flags
        ]
        record["judge"] = {
            "status": "complete",
            "discarded": consensus.discarded,
            "consensus_findings": len(consensus.findings),
            "suspected_injection": consensus.suspected_injection,
        }
        record["decisions"].append({
            "check": "llm_judge",
            "reason": "candidate corroborated by blind and confirmation passes",
        })
