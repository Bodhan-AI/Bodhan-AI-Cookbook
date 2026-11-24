"""Evaluation runner and reproducible reporting."""

import argparse
import csv
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from .artifacts import content_fingerprint, implementation_hash
from .calibration import report as calibration_report
from .checks import SCRIPTS
from .models import Client, load_environment
from .pipeline import process_item


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate(item):
    if not isinstance(item, dict):
        raise ValueError("record must be an object")
    for key in ("id", "source", "lang"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("translation", "reference", "audio", "transcript"):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            raise ValueError(f"{key} must be text or null")
    terms = item.get("terms", {})
    if not isinstance(terms, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in terms.items()):
        raise ValueError("terms must map source terms to nonempty target strings")
    if "labels" in item:
        if not isinstance(item["labels"], list):
            raise ValueError("labels must be a list")
        for label in item["labels"]:
            if not isinstance(label, dict) or not isinstance(label.get("type"), str):
                raise ValueError("each label needs a type")
            if "span" in label:
                span = label["span"]
                field = label.get("span_field", "translation")
                if (
                    not isinstance(span, list)
                    or len(span) != 2
                    or any(type(x) is not int for x in span)
                    or not 0 <= span[0] <= span[1] <= len(item.get(field) or "")
                ):
                    raise ValueError("label span must be within its original text field")
            if "time_span" in label:
                span = label["time_span"]
                if (
                    not isinstance(span, list)
                    or len(span) != 2
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in span)
                    or not 0 <= span[0] < span[1]
                ):
                    raise ValueError("label time_span must be a nonempty seconds interval")
    for key in ("parent_id", "split", "label_origin"):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            raise ValueError(f"{key} must be text")
    for key in ("generation", "injection"):
        if key in item and item[key] is not None and not isinstance(item[key], dict):
            raise ValueError(f"{key} must be an object")
    if item.get("reviewed_content_sha256") is not None:
        value = item["reviewed_content_sha256"]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("reviewed_content_sha256 must be a SHA256 digest")


def compare(current, baseline):
    previous = {}
    for line in (baseline / "items.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        previous[(row["id"], row["lang"])] = row
    rows = ["# Baseline diff", "", "Comparisons use item ID and language. Fewer flags do not by themselves prove better quality.", ""]
    seen = set()
    for row in current:
        key = (row["id"], row["lang"])
        seen.add(key)
        old = previous.get(key)
        if old is None:
            rows.append(f"- Added item {key!r} ({row['status']}).")
            continue
        if old["status"] != row["status"]:
            rows.append(f"- Coverage changed {key!r}: {old['status']} -> {row['status']}.")
        old_flags = {json.dumps(f, sort_keys=True, ensure_ascii=False) for f in old["flags"]}
        new_flags = {json.dumps(f, sort_keys=True, ensure_ascii=False) for f in row["flags"]}
        for value in sorted(new_flags - old_flags):
            rows.append(f"- New flag {key!r}: `{value}`")
        for value in sorted(old_flags - new_flags):
            rows.append(f"- Resolved flag {key!r}: `{value}`")
    for key in sorted(set(previous) - seen):
        rows.append(f"- Removed item {key!r}; not counted as an improvement.")
    if len(rows) == 4:
        rows.append("No changes.")
    return "\n".join(rows) + "\n"


def run(args):
    dataset = Path(args.dataset).resolve()
    out = Path(args.out)
    langs = set(args.langs.split(","))
    unknown = langs - set(SCRIPTS)
    if unknown:
        raise ValueError(f"Unsupported language codes: {', '.join(sorted(unknown))}")
    if args.baseline and Path(args.baseline).resolve() == out.resolve():
        raise ValueError("baseline and output directories must differ")
    started = time.monotonic()
    client = Client(args.cache_dir, args.max_cost_inr, args.concurrency, args.retries, env=load_environment(args.env_file))
    results, logs, errors, annotations = [], [], [], {}
    calibration_records = {}
    if args.calibration:
        for line in Path(args.calibration).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("labels"), list):
                raise ValueError("calibration record requires labels list")
            key = (record["id"], record["lang"])
            if key in calibration_records:
                raise ValueError("duplicate calibration identity")
            calibration_records[key] = record
    seen, filtered = set(), 0
    next_item_at = 0.0
    for line_no, line in enumerate(dataset.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            validate(item)
            if item["lang"] not in langs:
                filtered += 1
                continue
            key = (item["id"], item["lang"])
            if key in seen:
                raise ValueError(f"duplicate item identity {key!r}")
            seen.add(key)
            if key in calibration_records:
                annotation = calibration_records[key]
                item = {**item, "labels": annotation["labels"], "label_origin": annotation.get("label_origin", "provided_calibration")}
                if annotation.get("reviewed_content_sha256"):
                    item["reviewed_content_sha256"] = annotation["reviewed_content_sha256"]
                annotations[key] = annotation["labels"]
                validate(item)
            if item.get("reviewed_content_sha256") and item["reviewed_content_sha256"] != content_fingerprint(item, dataset.parent):
                raise ValueError("stale human annotation: reviewed content changed")
        except (ValueError, TypeError, OSError) as exc:
            errors.append({"line": line_no, "error": str(exc)})
            continue
        delay = next_item_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        calls_before = client.calls
        row = process_item(item, dataset.parent, client, args)
        if client.calls > calls_before:
            next_item_at = time.monotonic() + args.live_item_delay_seconds
        if item.get("reviewed_content_sha256"):
            reviewed_output = {**row, "transcript": item.get("transcript")}
            if content_fingerprint(reviewed_output, dataset.parent) != item["reviewed_content_sha256"]:
                row["labels"] = None
                row["label_origin"] = "unreviewed_after_generation"
                row["reviewed_content_sha256"] = None
                row["checks"]["human_annotation"] = {"status": "invalidated", "reason": "generation changed reviewed output; review again"}
                annotations.pop(key, None)
        results.append(row)
        if item.get("split") == "calibration" and row.get("labels") is not None:
            annotations[key] = row["labels"]
        for check, evidence in sorted(row["checks"].items()):
            logs.append({"id": row["id"], "lang": row["lang"], "check": check, "result": evidence})
    calibrated = calibration_report(results, set(annotations))
    by_lang, categories = {}, Counter()
    for lang in sorted(langs):
        rows = [r for r in results if r["lang"] == lang]
        by_lang[lang] = {
            "items": len(rows),
            "flagged_items": sum(bool(r["flags"]) for r in rows),
            "unassessed_items": sum(r["status"] == "unassessed" for r in rows),
            "categories": dict(sorted(Counter(f["category"] for r in rows for f in r["flags"]).items())),
        }
    for row in results:
        categories.update(f["category"] for f in row["flags"])
    check_failures = sum(isinstance(value, dict) and value.get("status") == "failed" for row in results for value in row["checks"].values())
    agreements = [
        row["checks"]["judge"]["repeat_agreement"]
        for row in results
        if isinstance(row["checks"].get("judge"), dict) and row["checks"]["judge"].get("repeat_agreement") is not None
    ]
    nominal_cost = math.fsum(record["reservation_inr"] for _, record in sorted(client.records.items()))
    coverage = defaultdict(Counter)
    for row in results:
        for check, value in row["checks"].items():
            state = value.get("status", "completed") if isinstance(value, dict) else str(value)
            coverage[check][state] += 1
    summary = {
        "version": 2,
        "implementation_sha256": implementation_hash(),
        "mode": "offline" if args.offline else "model_backed",
        "settings": {"generate": args.generate, "speech_sample_rate": args.speech_sample_rate, "judge_repeats": args.judge_repeats},
        "items": len(results),
        "invalid_items": len(errors),
        "filtered_items": filtered,
        "unmatched_calibration_records": len(set(calibration_records) - seen),
        "languages": by_lang,
        "categories": dict(sorted(categories.items())),
        "cost_inr": nominal_cost,
        "cost_basis": "conservative uncached reservation for unique successful requests; not execution billing",
        "model_calls": len(client.records),
        "errors": errors,
        "failed_checks": check_failures,
        "check_coverage": {k: dict(v) for k, v in sorted(coverage.items())},
        "severities": dict(sorted(Counter(f["severity"] for r in results for f in r["flags"]).items())),
        "judge_consistency": sum(agreements) / len(agreements) if agreements else None,
        "judge_consistency_items": len(agreements),
        "evaluation": calibrated["evaluation"],
        "calibrated_thresholds": calibrated["tuning"]["thresholds"],
        "model_requests": dict(sorted(client.records.items())),
        "quality_targets": {"detection_recall": 0.8, "false_alarm_rate": 0.1, "localization_overlap": 0.7, "category_accuracy": 0.7, "judge_consistency": 0.9},
    }
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "summary.json", summary)
    write_json(out / "calibration.json", calibrated)
    for filename, rows in (("items.jsonl", results), ("checks.jsonl", logs)):
        (out / filename).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    exported = [
        {
            k: v
            for k, v in row.items()
            if k
            in (
                "id",
                "lang",
                "source",
                "translation",
                "reference",
                "audio",
                "labels",
                "label_origin",
                "parent_id",
                "split",
                "terms",
                "provenance",
                "generation",
                "injection",
                "reviewed_content_sha256",
            )
            and v is not None
        }
        for row in results
    ]
    (out / "outputs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in exported), encoding="utf-8")
    text = [
        "# Quality report",
        "",
        "Unflagged does not mean clean. Inspect skipped and failed check coverage before interpreting quality.",
        "",
        f"Items: {len(results)}. Invalid: {len(errors)}. Filtered: {filtered}. Failed checks: {check_failures}.",
        f"Conservative uncached request cost: INR {nominal_cost:.6f}. Current execution billing is logged separately in the cache directory.",
        "",
        "| Language | Items | Flagged | Unassessed |",
        "|---|---:|---:|---:|",
    ]
    for lang, count in by_lang.items():
        text.append(f"| {lang} | {count['items']} | {count['flagged_items']} | {count['unassessed_items']} |")
    text += ["", "## Held-out quality targets", "", "| Metric | Measured | Target | Status |", "|---|---:|---:|---|"]
    for name, target in summary["quality_targets"].items():
        value = summary["judge_consistency"] if name == "judge_consistency" else calibrated["evaluation"].get(name)
        met = value is not None and (value <= target if name == "false_alarm_rate" else value >= target)
        text.append(f"| {name} | {value if value is not None else 'not measured'} | {target} | {'met' if met else 'missed or unsupported'} |")
    text += [
        "",
        "Metrics reflect the annotation origins listed in calibration.json; synthetic labels are not human validation.",
        "Missed targets require category-level review; checker failures, absent references and insufficient labels limit coverage.",
        "",
        "## Findings",
        "",
    ]
    for row in results:
        for finding in row["flags"]:
            text.append(
                f"- {row['id']} ({row['lang']}): {finding['category']}, {finding['severity']}, "
                f"{finding['span_field']}{finding.get('span', finding.get('time_span'))}; {json.dumps(finding['evidence'], ensure_ascii=False)}"
            )
    (out / "summary.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    with (out / "review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "lang", "category", "span_field", "span", "evidence", "human_type", "human_severity", "annotator"])
        writer.writeheader()
        for row in results:
            for finding in row["review"]:
                writer.writerow({"id": row["id"], "lang": row["lang"], **{k: finding[k] for k in ("category", "span_field", "span", "evidence")}})
    if args.baseline:
        (out / "diff.md").write_text(compare(results, Path(args.baseline)), encoding="utf-8")
    elif (out / "diff.md").exists():
        (out / "diff.md").unlink()
    execution = {
        "seconds": time.monotonic() - started,
        "network_attempts": client.calls,
        "cache_hits": client.hits,
        "reserved_inr_including_failed_attempts": client.budget.reserved,
        "items": len(results),
        "note": "Runtime execution metadata is outside report/ to keep cached report artifacts deterministic.",
    }
    write_json(Path(args.cache_dir) / "last_execution.json", execution)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bodhan translation and speech quality harness", allow_abbrev=False)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--langs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--calibration")
    parser.add_argument("--max-cost-inr", type=float, default=50)
    parser.add_argument("--offline", action="store_true", help="run deterministic checks only, without network calls")
    parser.add_argument("--env-file", default=".env", help="local dotenv file; process environment takes precedence")
    parser.add_argument("--cache-dir", default=".cache/bodhan_eval")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--live-item-delay-seconds", type=float, default=0, help="wait after an item that made live API requests")
    parser.add_argument("--speech-sample-rate", type=float, default=1.0)
    parser.add_argument("--judge-repeats", type=int, default=2)
    parser.add_argument("--skip-judge", action="store_true", help="evaluate Bodhan outputs without calling the judge")
    parser.add_argument("--skip-back-translation", action="store_true", help="skip translation round trips during speech-only diagnostics")
    parser.add_argument("--llm-input-inr-per-million", type=float)
    parser.add_argument("--llm-output-inr-per-million", type=float)
    parser.add_argument("--regression-limit", type=int, help="CI: exit 2 if new baseline flags exceed this count")
    args = parser.parse_args(argv)
    if not math.isfinite(args.live_item_delay_seconds) or args.live_item_delay_seconds < 0:
        parser.error("--live-item-delay-seconds must be finite and nonnegative")
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr < 0:
        parser.error("--max-cost-inr must be finite and non-negative")
    if args.regression_limit is not None and (args.regression_limit < 0 or not args.baseline):
        parser.error("--regression-limit must be nonnegative and requires --baseline")
    if args.generate and args.offline:
        parser.error("--generate requires model access; omit --offline")
    if args.concurrency < 1 or args.judge_repeats < 1 or not 0 <= args.retries <= 5:
        parser.error("concurrency and judge repeats must be positive; retries must be between 0 and 5")
    if not math.isfinite(args.speech_sample_rate) or not 0 <= args.speech_sample_rate <= 1:
        parser.error("--speech-sample-rate must be between 0 and 1")
    rates = (args.llm_input_inr_per_million, args.llm_output_inr_per_million)
    if (rates[0] is None) != (rates[1] is None) or any(v is not None and (not math.isfinite(v) or v < 0) for v in rates):
        parser.error("Supply both nonnegative finite LLM INR rates, or neither")
    try:
        summary = run(args)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")
    print(f"Evaluated {summary['items']} items; {summary['invalid_items']} invalid. Report: {args.out}")
    if args.regression_limit is not None:
        new_flags = sum(line.startswith("- New flag ") for line in (Path(args.out) / "diff.md").read_text(encoding="utf-8").splitlines())
        if new_flags > args.regression_limit:
            print(f"Regression limit exceeded: {new_flags} new evidence flags > {args.regression_limit}")
            return 2
    return 0
