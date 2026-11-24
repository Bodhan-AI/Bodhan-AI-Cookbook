"""Cost-aware per-item orchestration; independent checkers execute concurrently."""

import concurrent.futures
import wave
from pathlib import Path

from .audio import analyze_wav
from .checks import alignment, evaluate, normalize
from .judge import messages, validate_judgment, verdict_signature
from .models import ModelError, digest


def process_item(original, dataset_dir, client, args):
    item = dict(original)
    checks, review = {}, []
    generation = dict(original.get("generation") or {})

    def attempt(name, function):
        try:
            return function()
        except (ModelError, OSError, ValueError, EOFError, wave.Error) as exc:
            # ModelError deliberately excludes raw provider responses/credentials.
            checks[name] = {"status": "failed", "error": str(exc)}
            return None

    if not item.get("translation") and args.generate:
        with client.capture() as requests:
            result = attempt("generation_translation", lambda: client.translate(item["source"], item["lang"]))
        if result:
            item["translation"] = result
            checks["generation_translation"] = "completed"
            generation["translation"] = {
                "model": "indic-translate",
                "input_sha256": digest(item["source"].encode("utf-8")),
                "output_sha256": digest(result.encode("utf-8")),
                "request_keys": requests,
            }
    flags, deterministic = evaluate(item)
    checks.update(deterministic)
    audio_path = dataset_dir / item["audio"] if item.get("audio") else None
    selected = int(digest(f"{item['id']}:{item['lang']}".encode())[:8], 16) / 0x100000000 < args.speech_sample_rate
    if args.generate and item.get("translation") and audio_path is None and selected:
        with client.capture() as requests:
            data = attempt("generation_speech", lambda: client.speak(item["translation"], item["lang"]))
        if data:
            directory = Path(args.cache_dir) / "audio"
            directory.mkdir(parents=True, exist_ok=True)
            audio_path = directory / f"{digest(data)}.wav"
            audio_path.write_bytes(data)
            checks["generation_speech"] = {"status": "completed", "audio_sha256": digest(data)}
            generation["audio"] = {
                "model": "indic-speak",
                "input_sha256": digest(item["translation"].encode("utf-8")),
                "output_sha256": digest(data),
                "request_keys": requests,
            }
    if audio_path:
        signal = attempt("audio_signal", lambda: analyze_wav(audio_path))
        if signal is not None:
            checks["audio_signal"] = signal
            if signal["peak_amplitude"] == 0 and signal["duration_seconds"] > 0 and item.get("translation"):
                flags.append(
                    {
                        "category": "garbled_audio",
                        "severity": "major",
                        "span_field": "audio",
                        "time_span": [0, signal["duration_seconds"]],
                        "check": "audio_signal",
                        "confidence": 1.0,
                        "evidence": {"reason": "Every PCM sample is exactly zero despite nonempty intended speech"},
                    }
                )

    # Cheap deterministic checks run before either network operation. Speech is
    # checked whenever supplied; sampling applies only to expensive synthesis.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {}
        if args.skip_back_translation:
            checks["back_translation"] = {"status": "skipped", "reason": "disabled_by_flag"}
        elif item.get("translation") and not args.offline:
            futures[pool.submit(client.translate, item["translation"], "en", item["lang"])] = "back_translation"
        if audio_path and not args.offline:
            futures[pool.submit(lambda: client.transcribe(audio_path.read_bytes(), item["lang"]))] = "bodhan_asr"
        for future, name in futures.items():
            value = attempt(name, future.result)
            if value is not None:
                checks[name] = {"status": "completed", "text": value}
                if name == "back_translation":
                    checks[name]["alignment"] = alignment(item["source"], value)
                else:
                    item["transcript"] = value
    if item.get("translation") and "transcript" in item and item["transcript"] is not None:
        checks["speech_alignment"] = alignment(item["translation"], item["transcript"])

    judge_flags = []
    if args.skip_judge:
        checks["judge"] = {"status": "skipped", "reason": "disabled_by_flag"}
    elif item.get("translation") and not args.offline:
        verdicts = []
        rates = None if args.llm_input_inr_per_million is None else (args.llm_input_inr_per_million, args.llm_output_inr_per_million)
        for repetition in range(args.judge_repeats):
            output = attempt("judge", lambda: client.judge(messages(item, checks_for_judge(checks)), rates, repetition))
            if output is None:
                break
            validated = attempt("judge", lambda: validate_judgment(output, item))
            if validated is None:
                break
            verdicts.append(validated)
        if verdicts:
            judge_flags = verdicts[0]
            incomplete = len(verdicts) < args.judge_repeats
            failure = checks.get("judge", {}).get("error") if incomplete else None
            checks["judge"] = {
                "status": "completed" if len(verdicts) == args.judge_repeats else "partial",
                "rubric_version": 1,
                "assessments": len(verdicts),
                "requested_assessments": args.judge_repeats,
                "repeat_agreement": (sum(verdict_signature(v) == verdict_signature(verdicts[0]) for v in verdicts[1:]) / (len(verdicts) - 1))
                if len(verdicts) > 1
                else None,
            }
            if failure:
                checks["judge"]["error"] = failure
            if incomplete or (len(verdicts) > 1 and checks["judge"]["repeat_agreement"] != 1):
                for finding in judge_flags:
                    finding["uncertain"] = True
        speech_flags = [f for f in judge_flags if f["category"].startswith("speech_") or f["category"] == "garbled_audio"]
        if speech_flags and audio_path:
            confirmed = attempt("asr_confirmation", lambda: client.transcribe(audio_path.read_bytes(), item["lang"], replicate=1))
            if confirmed is not None:
                checks["asr_confirmation"] = {
                    "status": "completed",
                    "text": confirmed,
                    "agrees_with_first": normalize(confirmed) == normalize(item.get("transcript") or ""),
                }
            for finding in speech_flags:
                if confirmed is None or normalize(confirmed) != normalize(item.get("transcript") or ""):
                    finding["uncertain"] = True
        elif speech_flags:
            for finding in speech_flags:
                finding["uncertain"] = True
        for finding in judge_flags:
            if finding["category"].startswith("speech_") or finding["category"] == "garbled_audio":
                discrepancy = checks.get("speech_alignment", {}).get("differences")
                if not discrepancy:
                    finding["uncertain"] = True
                # No audio observation by the LLM: lack of an independent signal
                # must remain visible even if the ASR text looks plausible.
                finding["evidence"]["corroboration"] = "Repeated Bodhan ASR plus text judge; systematic ASR errors remain possible"
            if finding["uncertain"]:
                review.append(finding)
            else:
                flags.append(finding)
    for name in ("back_translation", "bodhan_asr", "judge"):
        checks.setdefault(name, {"status": "skipped", "reason": "offline" if args.offline else "input_unavailable"})
    status = "evaluated" if isinstance(item.get("translation"), str) else "unassessed"
    return {
        "id": item["id"],
        "lang": item["lang"],
        "status": status,
        "checks": checks,
        "flags": flags,
        "review": review,
        "labels": original.get("labels"),
        "label_origin": original.get("label_origin") or "unspecified",
        "parent_id": original.get("parent_id") or original["id"],
        "split": original.get("split"),
        "source": item["source"],
        "translation": item.get("translation"),
        "transcript": item.get("transcript"),
        "reference": item.get("reference"),
        "audio": str(audio_path.resolve()) if audio_path else None,
        "terms": original.get("terms", {}),
        "provenance": original.get("provenance"),
        "generation": generation,
        "injection": original.get("injection"),
        "reviewed_content_sha256": original.get("reviewed_content_sha256"),
    }


def checks_for_judge(checks):
    # Do not send labels, parent identities, injected categories or provenance.
    allowed = ("back_translation", "speech_alignment", "audio_signal", "reference_alignment")
    return {key: checks[key] for key in allowed if key in checks}
