"""Deterministic per-deliverable post-condition contracts verifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bodhan_mcp.media import ffprobe_info
from bodhan_mcp.text_utils import (
    extract_markdown_tables,
    script_ratio_for_language,
    validate_subtitles,
)

from bodhan_agent.models import (
    ContractCheck,
    DeliverableSpec,
    DeliverableVerification,
    TaskSpec,
)
from bodhan_agent.store import ArtifactStore, ProvenanceViolationError


def verify_deliverable(
    deliverable: DeliverableSpec,
    *,
    published_path: Path | None,
    store: ArtifactStore,
    all_step_meta: dict[str, dict[str, Any]],
) -> DeliverableVerification:
    """Check all deterministic post-conditions for a single deliverable."""
    checks: list[ContractCheck] = []

    if deliverable.kind == "answer":
        # Answer-only deliverable (e.g. list of Telugu clips) may optionally have a published text file.
        if published_path and published_path.is_file():
            size = published_path.stat().st_size
            checks.append(ContractCheck(name="file_non_empty", passed=size > 0, detail=f"{size} bytes"))
            try:
                store.assert_indic_provenance(published_path)
                checks.append(ContractCheck(name="indic_provenance", passed=True, detail="Verified Bodhan/input provenance"))
            except ProvenanceViolationError as exc:
                checks.append(ContractCheck(name="indic_provenance", passed=False, detail=str(exc)))
        else:
            checks.append(ContractCheck(name="answer_recorded", passed=True, detail="Recorded in result.md"))
        return DeliverableVerification(
            deliverable_id=deliverable.id,
            kind=deliverable.kind,
            artifact_path=str(published_path) if published_path else None,
            passed=all(c.passed for c in checks),
            checks=checks,
        )

    # 1. File exists and non-empty.
    if not published_path or not published_path.is_file():
        checks.append(ContractCheck(name="file_exists", passed=False, detail="Deliverable artifact was not produced."))
        return DeliverableVerification(
            deliverable_id=deliverable.id,
            kind=deliverable.kind,
            artifact_path=None,
            passed=False,
            checks=checks,
        )

    size = published_path.stat().st_size
    checks.append(ContractCheck(name="file_non_empty", passed=size > 0, detail=f"{published_path.name} ({size} bytes)"))

    # 2. Indic provenance guard.
    try:
        store.assert_indic_provenance(published_path)
        checks.append(ContractCheck(name="indic_provenance", passed=True, detail="All Indic text verified from Bodhan/input"))
    except ProvenanceViolationError as exc:
        checks.append(ContractCheck(name="indic_provenance", passed=False, detail=str(exc)))

    # 3. Kind-specific contracts.
    if deliverable.kind == "audio":
        try:
            info = ffprobe_info(published_path)
            dur = info["duration_s"]
            checks.append(ContractCheck(name="audio_playable", passed=dur > 0.2, detail=f"duration={dur:.2f}s"))
            if deliverable.constraints.duration_s:
                target = deliverable.constraints.duration_s
                lo, hi = target * 0.80, target * 1.20
                in_tol = lo <= dur <= hi
                checks.append(
                    ContractCheck(
                        name="duration_tolerance_20pct",
                        passed=in_tol,
                        detail=f"actual={dur:.1f}s vs target={target:.1f}s (allowed {lo:.1f}-{hi:.1f}s)",
                    )
                )
        except Exception as exc:
            checks.append(ContractCheck(name="audio_playable", passed=False, detail=f"ffprobe error: {exc}"))

        # Find latest verify_speech result matching this audio or language.
        verify_metas = [
            m for m in all_step_meta.values() if "mean_cer" in m and "chunks_checked" in m
        ]
        if verify_metas:
            vm = verify_metas[-1]
            v_passed = bool(vm.get("passed", False))
            checks.append(
                ContractCheck(
                    name="back_transcription_verify",
                    passed=v_passed,
                    detail=f"mean_cer={vm.get('mean_cer')}, chunks={vm.get('chunks_checked')}, failed={vm.get('failed_chunk_indices')}",
                )
            )
        else:
            checks.append(
                ContractCheck(
                    name="back_transcription_verify",
                    passed=False,
                    detail="No verify_speech step ran for audio deliverable.",
                )
            )

    elif deliverable.kind == "subtitles":
        content = published_path.read_text(encoding="utf-8", errors="replace")
        val = validate_subtitles(content)
        checks.append(
            ContractCheck(
                name="subtitles_valid_monotonic",
                passed=bool(val["valid"]) and bool(val["monotonic"]),
                detail=f"cues={val['cue_count']}, monotonic={val['monotonic']}, errors={val['errors'][:3]}",
            )
        )
        if deliverable.language and deliverable.constraints.script == "native":
            ratio = script_ratio_for_language(content, deliverable.language)
            checks.append(
                ContractCheck(
                    name="target_script_ratio",
                    passed=ratio >= 0.8,
                    detail=f"script_ratio={ratio:.2f} for '{deliverable.language}' (min 0.80)",
                )
            )

    elif deliverable.kind in ("markdown", "text"):
        content = published_path.read_text(encoding="utf-8", errors="replace")
        if deliverable.language and deliverable.constraints.script == "native":
            ratio = script_ratio_for_language(content, deliverable.language)
            checks.append(
                ContractCheck(
                    name="target_script_ratio",
                    passed=ratio >= 0.8,
                    detail=f"script_ratio={ratio:.2f} for '{deliverable.language}' (min 0.80)",
                )
            )
        # Check translation coverage metadata if a translate_text step produced this deliverable.
        tr_metas = [
            m for m in all_step_meta.values()
            if isinstance(m.get("coverage"), dict)
            and (not deliverable.language or m.get("target_language") == deliverable.language)
        ]
        if tr_metas:
            cov = tr_metas[-1]["coverage"]
            checks.append(
                ContractCheck(
                    name="translation_chunk_coverage",
                    passed=bool(cov.get("chunks_matched", True)),
                    detail=f"chunks_in={tr_metas[-1].get('chunks_in')}, chunks_out={tr_metas[-1].get('chunks_out')}",
                )
            )
            if deliverable.constraints.keep_tables or cov.get("source_tables"):
                dst_tables = extract_markdown_tables(content)
                preserved = bool(cov.get("tables_preserved", False)) and len(dst_tables) >= len(cov.get("source_tables", []))
                checks.append(
                    ContractCheck(
                        name="tables_intact",
                        passed=preserved,
                        detail=f"source_tables={cov.get('source_tables')}, output_tables={dst_tables}",
                    )
                )
            if cov.get("source_digit_count", 0) > 0:
                src_n = int(cov.get("source_digit_count", 0))
                miss_n = len(cov.get("missing_digits", []))
                preserved_ratio = (src_n - miss_n) / max(1, src_n)
                checks.append(
                    ContractCheck(
                        name="digits_preserved",
                        passed=preserved_ratio >= 0.8,
                        detail=f"preserved={src_n - miss_n}/{src_n} ({preserved_ratio:.0%}), missing={cov.get('missing_digits', [])}",
                    )
                )

    return DeliverableVerification(
        deliverable_id=deliverable.id,
        kind=deliverable.kind,
        artifact_path=str(published_path),
        passed=all(c.passed for c in checks),
        checks=checks,
    )


def verify_all_deliverables(
    task_spec: TaskSpec,
    published_by_deliverable: dict[str, Path],
    store: ArtifactStore,
    all_step_meta: dict[str, dict[str, Any]],
) -> list[DeliverableVerification]:
    return [
        verify_deliverable(
            d,
            published_path=published_by_deliverable.get(d.id),
            store=store,
            all_step_meta=all_step_meta,
        )
        for d in task_spec.deliverables
    ]
