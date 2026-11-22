"""Read-only Phase 3 evidence audit. Missing evidence can never become a pass."""

import argparse
import base64
import json
import wave
from collections import Counter
from pathlib import Path

from .artifacts import content_fingerprint, file_hash, implementation_hash, read_items, recipe_hash
from .benchmark import hashes, validate_assessment
from .inject import mutations, perturb_audio, split_for
from .models import BODHAN, canonical, digest, join_wav

DEMO_LANGS = {"hi", "ta", "bn"}
REQUIRED_CHECKS = {
    "recipe_tests",
    "recipe_lint",
    "cookbook_recipe",
    "cookbook_rules",
    "cookbook_tests",
    "cookbook_lint",
    "cookbook_validate",
    "make_check",
    "container",
}


def receipt_errors(item, kind, directory, cache):
    """Verify local cached request/output consistency, not remote attestation."""
    receipt = (item.get("generation") or {}).get(kind)
    model = "indic-translate" if kind == "translation" else "indic-speak"
    if not isinstance(receipt, dict) or receipt.get("model") != model:
        return ["missing Bodhan generation receipt"]
    try:
        input_data = item["source" if kind == "translation" else "translation"].encode("utf-8")
        output = item["translation"].encode("utf-8") if kind == "translation" else (directory / item["audio"]).read_bytes()
        if receipt.get("input_sha256") != digest(input_data) or receipt.get("output_sha256") != digest(output):
            return ["generation content hash mismatch"]
        keys = receipt.get("request_keys")
        if not isinstance(keys, list) or not keys:
            return ["missing generation request identities"]
        if cache is None:
            return ["cache directory required to independently reconcile generation receipts"]
        responses, inputs = [], []
        for key in keys:
            if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                return ["invalid generation request identity"]
            record = json.loads((cache / f"{key}.json").read_text(encoding="utf-8"))
            identity = record["identity"]
            response = base64.b64decode(record["response"], validate=True)
            if digest(canonical(identity).encode()) != key or digest(response) != record["response_sha256"]:
                return ["generation cache checksum mismatch"]
            endpoints = {"/translate", "/v1/chat/completions"} if kind == "translation" else {"/v1/audio/speech"}
            if identity.get("url") not in {BODHAN + endpoint for endpoint in endpoints} or identity.get("model") != model:
                return ["unexpected generation model or endpoint"]
            payload = identity["payload"]
            if kind == "translation":
                result = json.loads(response)
                if identity["url"].endswith("/chat/completions"):
                    choice = result["choices"][0]
                    if (
                        payload.get("model") != model
                        or payload.get("source_language_code") != "en"
                        or payload.get("messages") != [{"role": "user", "content": item["source"]}]
                        or payload.get("target_language_code") != item["lang"]
                        or choice.get("finish_reason") != "stop"
                        or choice["message"]["content"] != item["translation"]
                    ):
                        return ["translation receipt does not match item"]
                    continue
                if (
                    payload.get("text") != item["source"]
                    or payload.get("target_language") != item["lang"]
                    or result.get("translation") != item["translation"]
                    or result.get("truncated") is True
                ):
                    return ["translation receipt does not match item"]
            else:
                if payload.get("model") != model:
                    return ["synthesis payload model mismatch"]
                if json.loads(payload["instructions"]).get("lang") != item["lang"]:
                    return ["synthesis language mismatch"]
                inputs.append(payload["input"])
                responses.append(response)
        if kind == "translation" and len(keys) != 1:
            return ["translation receipt must contain one response"]
        if kind == "audio" and ("".join(inputs).encode() != input_data or join_wav(responses) != output):
            return ["synthesis chunks do not reconstruct item"]
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, EOFError, wave.Error):
        return ["unreadable or malformed generation evidence"]
    return []


def audit(dataset, report=None, benchmark=None, validation=None, cache=None, video_url=None, video_evidence=None):
    dataset = Path(dataset).resolve()
    rows = read_items(dataset)
    cache = Path(cache) if cache else None
    checks = []

    def record(name, passed, details, status=None):
        checks.append({"check": name, "status": status or ("pass" if passed else "missing"), "details": details})

    populated = [r for r in rows if isinstance(r.get("translation"), str) and r["translation"].strip()]
    record(
        "demo_outputs",
        len(populated) >= 100 and DEMO_LANGS.issubset({r["lang"] for r in populated}),
        {"records": len(rows), "translated_records": len(populated), "languages": dict(sorted(Counter(r["lang"] for r in populated).items()))},
    )
    parents = {(r.get("parent_id") or r["id"], r["lang"]): r for r in rows if not r.get("injection")}
    record("unique_parent_groups", len(parents) == sum(not r.get("injection") for r in rows), "one original per parent/language group")
    receipt_failures, reviewed_clean, audio_langs = [], [], set()
    for key, row in parents.items():
        errors = receipt_errors(row, "translation", dataset.parent, cache)
        if row.get("audio"):
            audio_errors = receipt_errors(row, "audio", dataset.parent, cache)
            errors += audio_errors
            if not audio_errors:
                audio_langs.add(row["lang"])
        if errors:
            receipt_failures.append({"id": key[0], "lang": key[1], "errors": errors})
        if row.get("labels") == [] and row.get("label_origin", "").startswith("human:"):
            try:
                if row.get("reviewed_content_sha256") == content_fingerprint(row, dataset.parent):
                    reviewed_clean.append(key)
            except OSError:
                pass
    record("bodhan_generation_provenance", bool(parents) and not receipt_failures, receipt_failures, "fail" if receipt_failures else None)
    record("speech_demo_coverage", DEMO_LANGS.issubset(audio_langs), {"verified_audio_languages": sorted(audio_langs)})
    record(
        "human_clean_negatives",
        DEMO_LANGS.issubset({key[1] for key in reviewed_clean}),
        {"content_bound_human_clean_items": len(reviewed_clean), "note": "minimum presence check, not a claim of statistical adequacy"},
    )
    injected, injection_errors, splits = Counter(), [], {}
    for row in rows:
        parent_id = row.get("parent_id") or row["id"]
        splits.setdefault(parent_id, set()).add(row.get("split"))
        if not row.get("injection"):
            continue
        parent = parents.get((parent_id, row["lang"]))
        kind = row["injection"].get("operation")
        try:
            human_confirmed = row.get("label_origin", "").startswith("human:") and row.get("reviewed_content_sha256") == content_fingerprint(
                row, dataset.parent
            )
            if parent is None or row["source"] != parent["source"] or not (row.get("label_origin") == "programmatic_injection" or human_confirmed):
                raise ValueError("unbound injection parent or origin")
            if kind in ("speech_omission", "speech_repetition", "garbled_audio"):
                data, interval = perturb_audio((dataset.parent / parent["audio"]).read_bytes(), kind)
                if digest(data) != digest((dataset.parent / row["audio"]).read_bytes()) or (
                    not human_confirmed and row["labels"] != [{"type": kind, "severity": "major", "time_span": interval}]
                ):
                    raise ValueError("audio mutation mismatch")
            else:
                possible = mutations(parent)
                if not any(
                    v["translation"] == row.get("translation") and (human_confirmed or v["labels"] == row.get("labels")) and v["injection"] == row["injection"]
                    for v in possible
                ):
                    raise ValueError("text mutation mismatch")
            injected.update(label["type"] for label in row["labels"])
        except (ValueError, KeyError, OSError, TypeError):
            injection_errors.append({"id": row["id"], "lang": row["lang"], "error": "mutation cannot be reproduced from parent"})
    record("injected_cases", bool(injected) and not injection_errors, {"categories": dict(sorted(injected.items())), "errors": injection_errors})
    split_errors = sorted(parent for parent, values in splits.items() if len(values) != 1 or values != {split_for(parent)})
    record("parent_split_isolation", bool(rows) and not split_errors, {"inconsistent_parent_ids": split_errors})

    report_hashes = {}
    if report:
        report = Path(report)
        try:
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            calibration = json.loads((report / "calibration.json").read_text(encoding="utf-8"))
            assessed = [json.loads(line) for line in (report / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            assessment = validate_assessment(summary, assessed, require_live=True)
            record("live_assessment_coverage", assessment["complete"] and summary.get("mode") == "model_backed", assessment)
            record("report_implementation", summary.get("implementation_sha256") == implementation_hash(), "report must match current implementation hash")
            expected = {(r["id"], r["lang"]): r for r in rows}
            matched = len(assessed) == len(rows) and len({(r["id"], r["lang"]) for r in assessed}) == len(rows)
            for result in assessed:
                item = expected.get((result["id"], result["lang"]))
                if item is None:
                    matched = False
                    continue
                # Compare outputs; a new checker transcript is not a new TTS output.
                target = {**result, "transcript": item.get("transcript")}
                matched &= content_fingerprint(item, dataset.parent) == content_fingerprint(target, dataset.parent)
            record("dataset_report_alignment", matched, "exact identities and original output content/audio")
            human = calibration.get("annotation_groups", {}).get("human", {})
            record(
                "human_held_out_evaluation",
                human.get("clean_items", 0) > 0 and human.get("error_items", 0) > 0,
                {"clean_items": human.get("clean_items", 0), "error_items": human.get("error_items", 0)},
            )
            report_hashes = hashes(report)
        except (OSError, ValueError, KeyError, TypeError):
            record("live_report", False, "required report artifacts missing, malformed or unreadable", "fail")
    else:
        record("live_report", False, "provide --report from the actual demo evaluation")
    if benchmark:
        try:
            path = Path(benchmark)
            evidence = json.loads((path / "benchmark.json").read_text(encoding="utf-8"))
            same_files = evidence.get("artifact_sha256") == hashes(path / "first") == hashes(path / "replay")
            independently_complete = []
            for child in ("first", "replay"):
                sub_summary = json.loads((path / child / "summary.json").read_text(encoding="utf-8"))
                sub_rows = [json.loads(line) for line in (path / child / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                independently_complete.append(
                    sub_summary.get("mode") == "model_backed"
                    and sub_summary.get("implementation_sha256") == implementation_hash()
                    and validate_assessment(sub_summary, sub_rows, require_live=True)["complete"]
                )
            executions = evidence.get("executions", [])
            zero_replay = (
                len(executions) == 2 and executions[1].get("network_attempts") == 0 and executions[1].get("reserved_inr_including_failed_attempts") == 0
            )
            valid = (
                evidence.get("version") == 2
                and evidence.get("verified") is True
                and same_files
                and all(independently_complete)
                and zero_replay
                and evidence.get("cold_cache") is True
                and evidence.get("dataset_sha256") == file_hash(dataset)
                and all(a.get("scope") == "model_backed" and a.get("complete") for a in evidence.get("assessments", []))
                and len(evidence.get("assessments", [])) == 2
            )
            record("live_cache_benchmark", valid, "cold cache, dataset-bound hashes, zero replay attempts and independently checked live coverage required")
        except (OSError, ValueError, TypeError):
            record("live_cache_benchmark", False, "missing or malformed benchmark evidence")
    else:
        record("live_cache_benchmark", False, "provide --benchmark directory")
    if validation:
        try:
            evidence = json.loads(Path(validation).read_text(encoding="utf-8"))
            recorded = {check["name"]: check for check in evidence.get("checks", [])}
            logs_match = all(file_hash(Path(validation).parent / c["log"]) == c.get("log_sha256") for c in recorded.values())
            valid = (
                evidence.get("recipe_sha256") == recipe_hash()
                and evidence.get("all_passed") is True
                and evidence.get("source_unchanged") is True
                and REQUIRED_CHECKS.issubset(recorded)
                and logs_match
                and all(recorded[name].get("status") == "pass" and recorded[name].get("exit_code") == 0 for name in REQUIRED_CHECKS)
            )
            record("local_and_container_checks", valid, "fresh check-runner evidence required; skipped tools cannot pass")
        except (OSError, ValueError, TypeError, KeyError):
            record("local_and_container_checks", False, "missing or malformed validation evidence")
    else:
        record("local_and_container_checks", False, "provide --validation checks.json")
    if video_evidence:
        try:
            proof = json.loads(Path(video_evidence).read_text(encoding="utf-8"))
            from urllib.parse import urlsplit

            url = urlsplit(proof["url"])
            duration = proof.get("duration_seconds")
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and not url.username
                and (not video_url or video_url == proof["url"])
                and proof.get("dataset_sha256") == file_hash(dataset)
                and isinstance(proof.get("reviewer"), str)
                and bool(proof["reviewer"].strip())
                and type(duration) in (int, float)
                and 0 < duration <= 300
                and proof.get("public_access_verified") is True
                and proof.get("reviewed_full_run_and_report") is True
                and isinstance(proof.get("verified_at"), str)
                and bool(proof["verified_at"])
            )
            record(
                "public_demo_video",
                valid,
                {"evidence_sha256": file_hash(video_evidence), "verification": "attributable human attestation, not an automatic media/network verification"},
            )
        except (OSError, ValueError, KeyError, TypeError):
            record("public_demo_video", False, "missing or malformed recording attestation")
    else:
        record(
            "public_demo_video",
            False,
            {"url_supplied": bool(video_url), "note": "provide attributable recording evidence after independent public-access/content review"},
            "unverified" if video_url else "missing",
        )
    return {
        "version": 1,
        "scope": "listed Phase 3 artifact checks; not submission authorization or provider authenticity verification",
        "ready": all(c["status"] == "pass" for c in checks),
        "dataset_sha256": file_hash(dataset),
        "implementation_sha256": implementation_hash(),
        "recipe_sha256": recipe_hash(),
        "checks": checks,
        "report_sha256": report_hashes,
        "note": (
            "This audit verifies local artifact consistency, not provider authenticity or human judgment quality. Quality targets are targets, not hard gates."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--report")
    parser.add_argument("--benchmark")
    parser.add_argument("--validation")
    parser.add_argument("--cache-dir")
    parser.add_argument("--video-url")
    parser.add_argument("--video-evidence", help="attributable manual recording verification JSON; never generated by this audit")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.dataset, args.report, args.benchmark, args.validation, args.cache_dir, args.video_url, args.video_evidence)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Readiness audit failed: {exc}\n")
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    (output / "readiness.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Phase 3 readiness", "", f"Ready: **{result['ready']}**", "", "| Evidence | Status |", "|---|---|"]
    lines += [f"| {check['check']} | {check['status']} |" for check in result["checks"]]
    lines += ["", result["note"], "", "See readiness.json for exact missing evidence and artifact hashes."]
    (output / "readiness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Phase 3 evidence checks passed: {result['ready']}; audit: {output}")
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
