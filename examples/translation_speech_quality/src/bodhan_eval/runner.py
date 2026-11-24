"""Run cheap checks first, then independent Bodhan round trips and a judge."""

import hashlib
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from .checks import SCRIPTS, VOICES, deterministic_checks, flag, normalized, speech_text_flags
from .judge import judgment, verdict_signature
from .media import audio_stats, read_audio, write_chunks
from .providers import Unavailable


def read_dataset(path):
    rows, errors, seen = [], [], set()
    with Path(path).open() as data:
        for number, line in enumerate(data, 1):
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("Item must be an object")
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise ValueError("Missing nonempty string id")
                if item["id"] in seen:
                    raise ValueError("Duplicate id")
                if not isinstance(item.get("source"), str) or not item["source"].strip():
                    raise ValueError("Missing nonempty source")
                if len(item["source"]) > 20_000:
                    raise ValueError("Source exceeds 20,000-character per-item limit")
                if item.get("lang") not in SCRIPTS:
                    raise ValueError("Unsupported language code")
                for name in ("translation", "reference", "audio"):
                    if name in item and (not isinstance(item[name], str) or len(item[name]) > 20_000):
                        raise ValueError(f"Invalid {name}")
                glossary = item.get("glossary", {})
                if not isinstance(glossary, dict) or any(
                    not isinstance(k, str) or not (isinstance(v, str) or
                    isinstance(v, list) and all(isinstance(t, str) for t in v))
                    for k, v in glossary.items()
                ):
                    raise ValueError("Invalid glossary")
                seen.add(item["id"])
                rows.append(item)
            except (json.JSONDecodeError, ValueError, TypeError):
                errors.append({"line": number, "status": "invalid", "reason": "Malformed item, duplicate id, or invalid field"})
    return rows, errors


def audio_check(client, item, path):
    audio, rate = read_audio(path)
    stats = audio_stats(audio, rate)
    findings = []
    if stats["clipping_fraction"] > 0.02:
        findings.append(flag("garbled_audio", "major", "audio_seconds", (0, stats["duration_seconds"]),
                             {"clipping_fraction": stats["clipping_fraction"]}, "clipping", 0.95))
    if stats["silence_fraction"] > 0.8:
        findings.append(flag("garbled_audio", "major", "audio_seconds", (0, stats["duration_seconds"]),
                             {"silence_fraction": stats["silence_fraction"]}, "silence", 0.98))
    transcript_parts = []
    # Keep independently measured waveform evidence even if recognition fails.
    try:
        with tempfile.TemporaryDirectory() as temp:
            for chunk in write_chunks(audio, rate, temp):
                clip, sample_rate = read_audio(chunk)
                transcript_parts.append(client.transcribe(chunk, item["lang"], len(clip) / sample_rate))
    except Unavailable as exc:
        return {"stats": stats, "transcript": None, "unavailable": str(exc)}, findings
    transcript = " ".join(transcript_parts)
    alignment, text_findings = speech_text_flags(item.get("translation", ""), transcript)
    findings.extend(text_findings)
    return {"stats": stats, "comparison": alignment, "transcript": transcript}, findings


def process_item(original, *, client, dataset_dir, out, generate, roundtrips, judge,
                 judge_repeat, speech_selected):
    item = dict(original)
    result = {"id": item["id"], "lang": item["lang"], "group_id": item.get("group_id", item["id"]),
              "source": item["source"], "status": "complete", "candidates": [],
              "checks": {}, "unavailable": []}
    if "labels" in item:
        # Labels are evaluation-only. They do not affect checks, routing or prompts.
        item.pop("labels")
    try:
        if "translation" not in item and generate:
            item["translation"] = client.translate(item["source"], item["lang"])
            result["checks"]["generation"] = "Bodhan Indic-Translate"
        candidates, metrics = deterministic_checks(item)
        result["candidates"] = candidates
        result["checks"]["reference"] = metrics or {"status": "no reference supplied"}
        result["translation"] = item.get("translation")
        if item.get("translation") is None:
            result["status"] = "partial"
            result["unavailable"].append("No supplied translation; use --generate to create one")
            return result
        back, transcript = None, None
        if roundtrips and item["translation"].strip():
            try:
                back = client.translate(item["translation"], "en")
                result["checks"]["back_translation"] = back
            except Unavailable as exc:
                result["unavailable"].append(str(exc))
        audio_path = item.get("audio")
        if not audio_path and generate and speech_selected and item["translation"].strip():
            try:
                paths = client.speak(item["translation"], item["lang"], item.get("voice", VOICES[item["lang"]]))
                clips = [read_audio(p) for p in paths]
                if not clips or len({r for _, r in clips}) != 1:
                    raise Unavailable("Speech generation returned no audio or inconsistent sample rates")
                name = hashlib.sha256(item["id"].encode()).hexdigest()[:20] + ".wav"
                target = out / "audio" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                sf.write(target, np.concatenate([a for a, _ in clips]), clips[0][1], subtype="PCM_16")
                audio_path = str(target.resolve())
                result["generated_audio"] = str(target.relative_to(out))
                result["audio_label"] = "AI-generated with Bodhan Indic-Speak"
            except (Unavailable, ValueError, OSError) as exc:
                result["unavailable"].append(str(exc))
        if audio_path:
            try:
                path = Path(audio_path)
                if not path.is_absolute():
                    path = dataset_dir / path
                details, speech_candidates = audio_check(client, item, path)
                result["checks"]["speech"] = details
                result["candidates"].extend(speech_candidates)
                transcript = details["transcript"]
                if details.get("unavailable"):
                    result["unavailable"].append(details["unavailable"])
            except (Unavailable, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                result["unavailable"].append(str(exc))
        else:
            result["checks"]["speech"] = {"status": "audio not supplied or selected for generation"}
        if judge:
            try:
                findings, uncertain, rejected = judgment(client, item, back, transcript)
                result["candidates"].extend(findings)
                result["checks"]["judge"] = {"uncertain": uncertain, "rejected_findings": rejected}
                if judge_repeat:
                    other, _, _ = judgment(client, item, back, transcript, repeat=1)
                    result["checks"]["judge"]["repeat_agrees"] = verdict_signature(findings) == verdict_signature(other)
            except Unavailable as exc:
                result["unavailable"].append(str(exc))
        if result["unavailable"]:
            result["status"] = "partial"
        if item.get("reference") and normalized(item["reference"]) == normalized(item["translation"]):
            # A reference is evidence, not an infallible oracle. Preserve a
            # disagreement for review rather than declaring either side wrong.
            for finding in result["candidates"]:
                if finding["type"] not in ("speech_omission", "speech_repetition", "garbled_audio"):
                    finding["review_only"] = True
                    finding["counterevidence"] = "Supplied translation exactly agrees with the normalized reference"
    except (Unavailable, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result["status"] = "partial"
        result["unavailable"].append(str(exc))
    return result


def run_items(items, *, workers=2, speech_sample=12, judge_repeat_sample=6, **kwargs):
    # Stable, label-independent selection. A hash avoids a language-order bias.
    order = sorted(items, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
    speech_ids = {item["id"] for item in order[:speech_sample]}
    repeated_ids = {item["id"] for item in order[:judge_repeat_sample]}
    def work(item):
        if hasattr(kwargs["client"], "context"):
            kwargs["client"].context.item_id = item["id"]
        return process_item(item, speech_selected=item["id"] in speech_ids,
                            judge_repeat=item["id"] in repeated_ids, **kwargs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(work, items))
