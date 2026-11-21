"""Run isolated task-agent evaluations and report pass rate separately from coverage."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


RECIPE_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("cases.jsonl")
DESIGN_TASKS_PATH = Path(__file__).resolve().parents[1] / "design_tasks.jsonl"

DESIGN_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "stt_ambiguous_language_01": {"required_tools": ["transcribe_audio"], "must_contain_any": ["transcript", "language", "words"], "minimum_text_artifact_characters": 8},
    "ocr_table_translate_01": {"required_tools": ["ocr_document", "translate_text"], "minimum_text_artifact_characters": 20},
    "pdf_summary_audio_01": {"required_tools": ["ocr_document", "translate_text", "synthesize_speech"], "required_artifact_extensions": [".wav"], "minimum_text_artifact_characters": 50, "minimum_text_artifact_count": 2},
    "clip_filter_telugu_01": {"required_tools": ["transcribe_audio", "translate_text"], "minimum_text_artifact_characters": 15, "minimum_text_artifact_count": 4},
    "unsupported_french_01": {"must_contain_any": ["unsupported", "not supported", "cannot", "can't", "does not support"], "forbid_paid_success": True, "forbidden_artifact_extensions": [".wav", ".mp4"]},
    "ocr_injection_data_01": {"required_tools": ["ocr_document", "translate_text"], "minimum_text_artifact_characters": 12, "minimum_text_artifact_count": 2, "artifact_must_contain_all": ["school", "nine"]},
    "audio_chunk_boundary_01": {"required_tools": ["transcribe_audio", "translate_text"], "minimum_text_artifact_characters": 40, "minimum_text_artifact_count": 2, "artifact_must_contain_unicode_script": "bengali"},
    "cache_same_input_different_lang_01": {"required_tools": ["translate_text"], "minimum_artifact_count": 2, "minimum_text_artifact_characters": 20},
    "transliterate_hindi_roman_01": {"required_tools": ["transliterate_text"], "must_contain_any": ["namaste", "namastē", "namaste, aap"], "minimum_result_characters": 8, "artifact_must_contain_all": ["namaste", "kaise"]},
    "transliterate_hindi_native_01": {"required_tools": ["transliterate_text"], "minimum_result_characters": 6, "artifact_must_contain_unicode_script": "devanagari", "artifact_must_contain_all": ["आपका", "स्वागत"]},
    "transliteration_unsupported_script_01": {"must_contain_any": ["cyrillic", "unsupported", "cannot"], "forbid_paid_success": True},
    "transcribe_translate_transliterate_01": {"required_tools": ["transcribe_audio", "translate_text", "transliterate_text"], "minimum_result_characters": 30, "minimum_text_artifact_count": 3},
    "ocr_translate_transliterate_01": {"required_tools": ["ocr_document", "translate_text", "transliterate_text"], "minimum_text_artifact_characters": 20, "minimum_result_characters": 30, "minimum_text_artifact_count": 3},
    "video_transcription_01": {"required_tools": ["transcribe_video"], "minimum_text_artifact_characters": 8, "minimum_text_artifact_count": 1},
    "video_translation_dub_01": {"required_tool_alternatives": [["translate_video"], ["extract_video_audio", "transcribe_audio", "translate_text", "synthesize_speech", "replace_video_audio_track"]], "required_artifact_extensions": [".mp4"]},
    "video_translation_high_level_01": {"required_tools": ["translate_video"], "required_artifact_extensions": [".mp4"]},
}


def load_cases() -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [row["case_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("case_id values must be unique")
    return rows


def load_design_cases() -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in DESIGN_TASKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = []
    for row in rows:
        expected = DESIGN_EXPECTATIONS.get(row["case_id"])
        if expected is None:
            raise ValueError(f"No task-agent evaluation expectations for {row['case_id']}")
        limits = row.get("limits", {})
        cases.append({
            "case_id": row["case_id"],
            "task": row["prompt"],
            "inputs": [f"evals/{value.rstrip('/')}" for value in row.get("inputs", []) if value != "inline_text"],
            "required_tools": expected.get("required_tools", []),
            "required_tool_alternatives": expected.get("required_tool_alternatives", []),
            "forbidden_tools": expected.get("forbidden_tools", []),
            "must_contain_any": expected.get("must_contain_any", []),
            "forbid_paid_success": expected.get("forbid_paid_success", False),
            "forbidden_artifact_extensions": expected.get("forbidden_artifact_extensions", []),
            "required_artifact_extensions": expected.get("required_artifact_extensions", []),
            "minimum_artifact_count": expected.get("minimum_artifact_count", 0),
            "minimum_text_artifact_count": expected.get("minimum_text_artifact_count", 0),
            "minimum_text_artifact_characters": expected.get("minimum_text_artifact_characters", 0),
            "minimum_result_characters": expected.get("minimum_result_characters", 0),
            "result_must_contain_unicode_script": expected.get("result_must_contain_unicode_script"),
            "artifact_must_contain_unicode_script": expected.get("artifact_must_contain_unicode_script"),
            "artifact_must_contain_any": expected.get("artifact_must_contain_any", []),
            "artifact_must_contain_all": expected.get("artifact_must_contain_all", []),
            # The agent reserves tool steps for automatic dry runs and artifact retrieval.
            "max_steps": max(6, int(limits.get("max_tool_calls", 8)) * 2),
            "max_cost_inr": limits.get("max_cost_inr", 20),
        })
    return cases


def run_case(case: dict[str, Any], output_root: Path, max_parallel_tools: int, *, cache_only: bool = False) -> dict[str, Any]:
    case_id = case["case_id"]
    case_root = output_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    inputs = [str((RECIPE_ROOT / value).resolve()) for value in case.get("inputs", [])]
    command = [
        sys.executable,
        "-m",
        "bodhan_agent",
        "--task",
        case["task"],
        "--files",
        *inputs,
        "--out",
        str(case_root / "runs"),
        "--max-steps",
        str(case.get("max_steps", 30)),
        "--max-cost-inr",
        str(case.get("max_cost_inr", 20)),
        "--max-parallel-tools",
        str(max_parallel_tools),
        "--tool-timeout-seconds",
        str(case.get("tool_timeout_seconds", 240)),
    ]
    env = os.environ.copy()
    # Each child MCP process gets a fresh in-memory cache and isolated artifact
    # stores, so parallel cases cannot reuse or overwrite another case's state.
    env.update(
        {
            # Run the checked-out recipe sources even when this repository is
            # not installed editable in the active virtual environment.
            "PYTHONPATH": os.pathsep.join(filter(None, [str(RECIPE_ROOT / "src"), env.get("PYTHONPATH", "")])),
            "BODHAN_CACHE_BACKEND": "redis" if cache_only else "memory",
            "BODHAN_CACHE_ONLY": "true" if cache_only else "false",
            "BODHAN_ARTIFACT_DIR": str(case_root / "mcp-artifacts"),
            "BODHAN_MODEL_CACHE_ARTIFACT_DIR": str(case_root / "model-cache-artifacts"),
            "BODHAN_ARTIFACT_HTTP_PORT": "0",
        }
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=RECIPE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=case.get("run_timeout_seconds", 600),
        )
        stdout, stderr, return_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr += "\nEvaluation runner: task exceeded its run_timeout_seconds.\n"
        return_code = 124
    elapsed = round(time.monotonic() - started, 3)
    (case_root / "stdout.log").write_text(stdout, encoding="utf-8")
    (case_root / "stderr.log").write_text(stderr, encoding="utf-8")

    run_dirs = sorted((case_root / "runs").glob("*")) if (case_root / "runs").exists() else []
    report_path = next((path / "report.json" for path in reversed(run_dirs) if (path / "report.json").is_file()), None)
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path else None
    observed_tools = {
        event.get("tool")
        for event in (report or {}).get("tool_events", [])
        if event.get("type") == "tool_call" and event.get("tool")
    }
    successful_tools = {
        event.get("tool")
        for event in (report or {}).get("tool_events", [])
        if event.get("type") == "tool_call" and event.get("tool") and not event.get("is_error")
        and not (event.get("tool") in {"transcribe_audio", "transcribe_video", "translate_text", "transliterate_text", "synthesize_speech", "ocr_document", "translate_audio", "translate_video"} and event.get("arguments", {}).get("dry_run") is True)
    }
    required = set(case.get("required_tools", []))
    forbidden = set(case.get("forbidden_tools", []))
    alternatives = [set(group) for group in case.get("required_tool_alternatives", [])]
    required_total = len(required) + (1 if alternatives else 0)
    attempted_covered = len(required & observed_tools) + int(any(group <= observed_tools for group in alternatives))
    successful_covered = len(required & successful_tools) + int(any(group <= successful_tools for group in alternatives))
    final_text = ""
    deterministic_checks: list[dict[str, Any]] = []
    text_artifact_contents: list[str] = []
    if report_path:
        result_path = report_path.parent / "result.md"
        if result_path.is_file():
            final_text = result_path.read_text(encoding="utf-8").casefold()
        artifact_rows = (report or {}).get("artifacts", [])
        actual_artifacts: list[Path] = []
        text_artifact_characters = 0
        artifact_error = None
        run_root = report_path.parent.resolve()
        for row in artifact_rows:
            artifact_path = (run_root / str(row.get("path", ""))).resolve()
            if not artifact_path.is_relative_to(run_root) or not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
                artifact_error = f"missing or empty artifact: {row.get('path')}"
                continue
            actual_artifacts.append(artifact_path)
            if artifact_path.suffix.casefold() in {".txt", ".md", ".srt", ".vtt"}:
                try:
                    content = artifact_path.read_text(encoding="utf-8")
                    text_artifact_characters += len(content)
                    text_artifact_contents.append(content.casefold())
                except (OSError, UnicodeDecodeError):
                    artifact_error = f"text artifact is not readable UTF-8: {row.get('path')}"
        deterministic_checks.append({"name": "reported_artifacts_exist_and_nonempty", "passed": artifact_error is None, "detail": artifact_error or f"checked {len(actual_artifacts)} artifact(s)"})
        for extension in case.get("required_artifact_extensions", []):
            matched = [path for path in actual_artifacts if path.suffix.casefold() == extension.casefold()]
            valid = bool(matched)
            if extension.casefold() == ".wav":
                def has_wav_header(path: Path) -> bool:
                    try:
                        with path.open("rb") as media:
                            header = media.read(12)
                        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"
                    except OSError:
                        return False
                valid = any(has_wav_header(path) for path in matched)
            elif extension.casefold() == ".mp4":
                def has_mp4_header(path: Path) -> bool:
                    try:
                        with path.open("rb") as media:
                            header = media.read(12)
                        return len(header) >= 8 and header[4:8] == b"ftyp"
                    except OSError:
                        return False
                valid = any(has_mp4_header(path) for path in matched)
            deterministic_checks.append({"name": f"valid_artifact{extension}", "passed": valid, "detail": f"matched {len(matched)} artifact(s)"})
        for extension in case.get("forbidden_artifact_extensions", []):
            matched = [path.name for path in actual_artifacts if path.suffix.casefold() == extension.casefold()]
            deterministic_checks.append({"name": f"no_artifact{extension}", "passed": not matched, "detail": f"unexpected: {matched}" if matched else "none found"})
        min_artifacts = int(case.get("minimum_artifact_count", 0))
        if min_artifacts:
            deterministic_checks.append({"name": "minimum_artifact_count", "passed": len(actual_artifacts) >= min_artifacts, "detail": f"{len(actual_artifacts)} >= {min_artifacts}"})
        min_text = int(case.get("minimum_text_artifact_characters", 0))
        if min_text:
            deterministic_checks.append({"name": "minimum_text_artifact_characters", "passed": text_artifact_characters >= min_text, "detail": f"{text_artifact_characters} >= {min_text}"})
        min_text_count = int(case.get("minimum_text_artifact_count", 0))
        if min_text_count:
            deterministic_checks.append({"name": "minimum_text_artifact_count", "passed": len(text_artifact_contents) >= min_text_count, "detail": f"{len(text_artifact_contents)} >= {min_text_count}"})
        artifact_phrases = [phrase.casefold() for phrase in case.get("artifact_must_contain_any", [])]
        if artifact_phrases:
            merged_artifacts = "\n".join(text_artifact_contents)
            passed_phrases = [phrase for phrase in artifact_phrases if phrase in merged_artifacts]
            deterministic_checks.append({"name": "artifact_contains_expected_content", "passed": bool(passed_phrases), "detail": f"matched {passed_phrases}" if passed_phrases else f"none of {artifact_phrases} found"})
        required_artifact_phrases = [phrase.casefold() for phrase in case.get("artifact_must_contain_all", [])]
        if required_artifact_phrases:
            merged_artifacts = "\n".join(text_artifact_contents)
            missing_phrases = [phrase for phrase in required_artifact_phrases if phrase not in merged_artifacts]
            deterministic_checks.append({"name": "artifact_contains_all_expected_content", "passed": not missing_phrases, "detail": "all required phrases found" if not missing_phrases else f"missing {missing_phrases}"})
        min_result_chars = int(case.get("minimum_result_characters", 0))
        if min_result_chars:
            deterministic_checks.append({"name": "minimum_result_characters", "passed": len(final_text.strip()) >= min_result_chars, "detail": f"{len(final_text.strip())} >= {min_result_chars}"})
        script = case.get("result_must_contain_unicode_script")
        if script == "devanagari":
            contains_script = any("\u0900" <= char <= "\u097f" for char in final_text)
            deterministic_checks.append({"name": "result_contains_devanagari", "passed": contains_script, "detail": "Devanagari code point present" if contains_script else "no Devanagari code point found"})
        artifact_script = case.get("artifact_must_contain_unicode_script")
        if artifact_script:
            ranges = {"devanagari": (0x0900, 0x097F), "bengali": (0x0980, 0x09FF)}
            lower, upper = ranges[artifact_script]
            contains_script = any(any(lower <= ord(char) <= upper for char in content) for content in text_artifact_contents)
            deterministic_checks.append({"name": f"artifact_contains_{artifact_script}", "passed": contains_script, "detail": f"{artifact_script} code point present in text artifact" if contains_script else f"no {artifact_script} code point found in text artifacts"})
    launch_error = case.get("expected_launch_error")
    if launch_error:
        passed = return_code != 0 and launch_error.casefold() in (stdout + stderr).casefold()
    elif report is None:
        passed = False
    else:
        accepted_statuses = set(case.get("expected_statuses", ["completed"]))
        phrases = [phrase.casefold() for phrase in case.get("must_contain_any", [])]
        passed = (
            report.get("status") in accepted_statuses
            and required <= successful_tools
            and (not alternatives or any(group <= successful_tools for group in alternatives))
            and not (forbidden & observed_tools)
            and (not phrases or any(phrase in final_text for phrase in phrases))
            and all(check["passed"] for check in deterministic_checks)
            and (not case.get("forbid_paid_success") or not any(e.get("type") == "tool_call" and e.get("tool") in {"transcribe_audio", "transcribe_video", "translate_text", "transliterate_text", "synthesize_speech", "ocr_document", "translate_audio", "translate_video"} and not e.get("arguments", {}).get("dry_run") and not e.get("is_error") for e in (report or {}).get("tool_events", [])))
        )

    return {
        "case_id": case_id,
        "passed": bool(passed),
        "case_executed": bool(report is not None or (launch_error and return_code != 0)),
        "expected_tool_units": required_total,
        "attempted_tool_units": attempted_covered,
        "successful_tool_units": successful_covered,
        "attempted_tool_coverage": round(attempted_covered / required_total, 3) if required_total else None,
        "execution_coverage": round(successful_covered / required_total, 3) if required_total else None,
        "expected_tools": sorted(required),
        "observed_tools": sorted(observed_tools),
        "forbidden_tools_observed": sorted(forbidden & observed_tools),
        "deterministic_checks": deterministic_checks,
        "status": report.get("status") if report else None,
        "bodhan_cost_inr": report.get("bodhan_cost_inr") if report else None,
        "steps": report.get("steps") if report else None,
        "duration_seconds": elapsed,
        "return_code": return_code,
        "run_dir": str(report_path.parent) if report_path else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Directory for isolated per-case runs and summary.json")
    parser.add_argument("--case", action="append", dest="case_ids", help="Case ID to run; repeat to select several")
    parser.add_argument("--jobs", type=int, default=1, help="Maximum independent evaluation cases to run concurrently")
    parser.add_argument("--max-parallel-tools", type=int, default=3, help="Per-agent concurrency limit")
    parser.add_argument("--suite", choices=["task_agent", "design_tasks"], default="task_agent", help="Run the 8-case task-agent suite or the original 16 MCP design tasks")
    parser.add_argument("--cache-only", action="store_true", help="Use shared Redis cache, refusing any Bodhan API request on a cache miss")
    args = parser.parse_args()
    if args.jobs < 1 or args.max_parallel_tools < 1:
        parser.error("--jobs and --max-parallel-tools must be positive")

    cases = load_design_cases() if args.suite == "design_tasks" else load_cases()
    if args.case_ids:
        selected = set(args.case_ids)
        unknown = selected - {case["case_id"] for case in cases}
        if unknown:
            parser.error(f"unknown case ID(s): {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["case_id"] in selected]

    media = subprocess.run(
        [sys.executable, str(RECIPE_ROOT / "evals" / "prepare_media.py")],
        cwd=RECIPE_ROOT,
        text=True,
        capture_output=True,
    )
    if media.returncode:
        parser.error(media.stderr.strip() or "Could not prepare the synthetic evaluation media")
    if media.stdout.strip():
        print(media.stdout.strip())

    output_root = Path(args.out).expanduser().resolve() / f"agent-eval-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    output_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_case, case, output_root, args.max_parallel_tools, cache_only=args.cache_only): case for case in cases}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                case = futures[future]
                results.append({"case_id": case["case_id"], "passed": False, "execution_coverage": 0.0, "runner_error": str(exc)})

    results.sort(key=lambda item: item["case_id"])
    summary = {
        "cases": len(results),
        "passed": sum(bool(item.get("passed")) for item in results),
        "pass_rate": round(sum(bool(item.get("passed")) for item in results) / max(1, len(results)), 4),
        "case_execution_coverage": round(sum(bool(item.get("case_executed")) for item in results) / max(1, len(results)), 4),
        "execution_coverage": round(sum(int(item.get("successful_tool_units", 0)) for item in results) / max(1, sum(int(item.get("expected_tool_units", 0)) for item in results)), 4),
        "attempted_tool_coverage": round(sum(int(item.get("attempted_tool_units", 0)) for item in results) / max(1, sum(int(item.get("expected_tool_units", 0)) for item in results)), 4),
        "suite": args.suite,
        "cache_only": args.cache_only,
        "parallel_jobs": args.jobs,
        "max_parallel_tools": args.max_parallel_tools,
        "results": results,
    }
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Results and per-case traces: {output_root}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
