"""Code-level verification after the LLM says it is done.

The LLM's claim is not trusted on its own: every artifact must exist, every generated audio file must
have passed back-transcription and every translation its checks. Otherwise "completed" is downgraded.
"""

from __future__ import annotations

from pathlib import Path

PRODUCERS = {"synthesize_speech", "translate_text", "create_subtitles", "transcribe_audio", "ocr_document", "write_text"}


def verify(outcome, tool_results: list[dict], artifacts_dir: Path) -> dict:
    checks: list[dict] = []
    produced: dict[str, dict] = {}
    for tr in tool_results:
        r = tr["result"]
        if not r.get("ok") or tr["tool"] not in PRODUCERS:
            continue
        for key in ("path", "transcript_json"):
            if r.get(key):
                produced[str(Path(r[key]).resolve())] = tr
        if tr["tool"] == "synthesize_speech":
            v = r.get("verification") or {}
            checks.append({"artifact": r["path"], "check": "audio back-transcription",
                           "passed": bool(v.get("passed")) if v else None,
                           "detail": {k: v.get(k) for k in ("mean_cer", "max_cer", "regenerated", "failed_sentences")} if v
                           else "verification disabled or language not supported by indic-transcribe"})
        elif tr["tool"] in ("translate_text", "create_subtitles") and r.get("checks"):
            c = r["checks"]
            checks.append({"artifact": r["path"], "check": "translation coverage", "passed": c["passed"],
                           "detail": c["problems"] or {"length_ratio": c["length_ratio"],
                                                       "numbers_checked": c["numbers_checked"]}})

    missing, unverified = [], []
    resolved = []
    for a in outcome.artifacts:
        p = Path(a)
        cand = [p, artifacts_dir / p.name, artifacts_dir / p]
        hit = next((c for c in cand if c.is_file()), None)
        if hit is None:
            missing.append(a)
            continue
        resolved.append(str(hit))
        if str(hit.resolve()) not in produced:
            unverified.append(a)

    failed = [c for c in checks if c["passed"] is False and c["artifact"] in resolved]
    status, notes = outcome.status, []
    if status == "completed":
        if missing:
            status, notes = "partial", notes + [f"Listed artifacts do not exist: {missing}"]
        if failed:
            status = "partial"
            notes.append("Some deliverables failed verification: " + ", ".join(Path(c["artifact"]).name for c in failed))
        if not outcome.artifacts and not any(tr["result"].get("ok") for tr in tool_results if tr["tool"] in PRODUCERS):
            notes.append("No model output was produced; answer is the LLM's explanation only.")
    return {"status": status, "checks": checks, "artifacts": resolved, "missing_artifacts": missing,
            "not_produced_by_tools": unverified, "notes": notes,
            "all_passed": all(c["passed"] is not False for c in checks)}
