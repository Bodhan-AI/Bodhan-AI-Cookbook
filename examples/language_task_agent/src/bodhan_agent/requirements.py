"""Small deterministic output constraints, never task-specific execution routing.

Common explicit creation, narration and subtitle clauses are interpreted.
Input durations (for example 'transcribe this three-minute note') are not output
durations. Unrecognized phrasing and semantic quality remain planner judgments.
Selected audio and translation artifacts always need their own passing checks.
"""

import re
from bodhan_core.media import duration

NUMBERS = {
    word: i
    for i, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    )
}
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


def requirements(task):
    result = {}
    for clause in re.split(r"[;?]|\.(?!\d)|\b(?:and|then)\b", task.lower()):
        clause = re.sub(r"^\s*(?:(?:please|can you|could you|would you)\s+)+", "", clause)
        output = re.split(r"\b(?:of|from|covering|using|about|for)\b", clause, maxsplit=1)[0]
        creation = re.match(
            r"\s*(?:make|create|generate|produce|write|give|return|save|export|deliver)\b", output
        )
        if re.match(r"\s*translate\b", clause) or (
            creation and re.search(r"\btranslations?\b", output)
        ):
            result["translation_required"] = True
        narration = re.match(r"\s*(?:read\b.*\baloud\b|narrate\b|speak\b)", clause)
        audio = re.search(r"\b(?:audio|speech|narration|spoken|recording|mp3|wav|flac|ogg)\b", output)
        if narration or (creation and audio):
            result["audio_required"] = True
            # A duration beside an input being read is not an output-length demand.
            if creation:
                match = re.search(
                    r"\b(\d+(?:\.\d+)?|" + "|".join(NUMBERS) + r")[ -]+(second|minute|hour)s?\b",
                    output,
                )
                if match:
                    n = float(NUMBERS[match[1]]) if match[1] in NUMBERS else float(match[1])
                    seconds = n * {"second": 1, "minute": 60, "hour": 3600}[match[2]]
                    if seconds > 0:
                        tolerance = 0.01 if "exactly" in output else 0.25
                        result["audio_seconds"] = [
                            seconds * (1 - tolerance), seconds * (1 + tolerance)
                        ]
                for fmt in ("mp3", "wav", "flac", "ogg"):
                    if re.search(r"\b" + fmt + r"\b", output):
                        result["audio_format"] = "." + fmt
        if (creation and re.search(r"\b(?:subtitles?|captions?|srt)\b", output)) or re.search(
            r"\b(?:into|as|to)\b.*\b(?:subtitles?|captions?|srt)\b", clause
        ):
            result["subtitles_required"] = True
    return result


def validate(task, run, selected):
    required = requirements(task)
    outputs = [(ref, run.resolve(ref)) for ref in selected]
    audio = [path for _, path in outputs if path.suffix.lower() in AUDIO_SUFFIXES]
    errors = []
    for ref, path in outputs:
        kind = None
        if path.suffix.lower() in AUDIO_SUFFIXES:
            kind = "audio"
        elif path.suffix.lower() == ".srt" or run.provenance.get(ref) in {
            "bodhan_translation", "bodhan_translation_index"
        }:
            kind = "translation"
        if kind and not any(
            check.get("ref") == ref
            and check.get("kind") == kind
            and check.get("checked") is True
            and check.get("passed") is True
            for check in run.checks
        ):
            errors.append(f"Selected {kind} output {ref} lacks its own passing verification.")
    if required.get("translation_required") and not any(
        run.provenance.get(ref) in {"bodhan_translation", "bodhan_translation_index"}
        or path.suffix.lower() == ".srt" for ref, path in outputs
    ):
        errors.append(
            "Requested Bodhan translation output is missing. Select the actual checked translation artifacts or publish them verbatim with write_translation_index; planner prose is not a translation deliverable."
        )
    if required.get("subtitles_required") and not any(
        path.suffix.lower() == ".srt" for _, path in outputs
    ):
        errors.append("Requested SRT subtitle output is missing.")
    if required.get("audio_required") and not audio:
        errors.append("Requested audio output is missing.")
        return errors
    candidates = audio
    if required.get("audio_format"):
        candidates = [p for p in audio if p.suffix.lower() == required["audio_format"]]
        if not candidates:
            errors.append("Requested audio format " + required["audio_format"] + " is missing.")
    if "audio_seconds" in required:
        lower, upper = required["audio_seconds"]
        measured = [duration(p, run.remaining_time()) for p in candidates]
        if not any(lower <= value <= upper for value in measured):
            errors.append(
                f"Requested output audio must be between {lower:.2f} and {upper:.2f} seconds; measured {measured}. Revise content through the tools, without padding silence or deceptively changing speed, or return partial."
            )
    return errors
