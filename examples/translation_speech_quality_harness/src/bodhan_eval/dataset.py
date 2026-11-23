import json
import math
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import CHECK_THRESHOLDS, MAX_AUDIO_FILE_BYTES, MAX_JSONL_LINE_BYTES
from .labels import canonical_category, canonical_severity
from .languages import get_lang
from .models import Item, Label


@dataclass
class QuarantineRec:
    line: int
    reason: str
    raw: str
    lang: str = ""


@dataclass
class SkippedRec:
    line: int
    reason: str
    id: str
    lang: str


@dataclass
class DatasetResult:
    items: list[Item] = field(default_factory=list)
    quarantined: list[QuarantineRec] = field(default_factory=list)
    skipped: list[SkippedRec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_input_lines: int = 0


def _safe_text(value: str) -> str:
    return "".join(
        f"\\u{ord(char):04x}" if 0xD800 <= ord(char) <= 0xDFFF else char
        for char in value
    )


def _normalize_language(value: Any) -> str:
    code = str(value).strip().lower().replace("_", "-")
    return code.split("-", 1)[0]


def _parse_labels(
    raw_labels: Any,
    warnings: list[str] | None = None,
    line_number: int = 0,
    text_length: int | None = None,
) -> list[Label]:
    if not isinstance(raw_labels, list):
        if warnings is not None:
            warnings.append(f"line {line_number}: unmapped_labels:expected_list")
        return []
    labels: list[Label] = []
    for raw in raw_labels:
        if not isinstance(raw, dict):
            if warnings is not None:
                warnings.append(f"line {line_number}: unmapped_label:not_object")
            continue
        raw_type = str(raw.get("type", "unmapped"))
        severity = canonical_severity(str(raw.get("severity", "major")))
        span = raw.get("span")
        if isinstance(span, list):
            if (
                len(span) == 2
                and all(isinstance(value, int | float) for value in span)
                and all(math.isfinite(value) for value in span)
            ):
                parsed_span: tuple[int, int] | list[float] | None
                if all(isinstance(value, int) for value in span):
                    parsed_span = (span[0], span[1])
                else:
                    parsed_span = [float(span[0]), float(span[1])]
            else:
                parsed_span = None
                if warnings is not None:
                    warnings.append(f"line {line_number}: unmapped_label_span")
        else:
            parsed_span = None
            if span is not None and warnings is not None:
                warnings.append(f"line {line_number}: unmapped_label_span")
        if parsed_span is not None and text_length is not None:
            start, end = parsed_span
            if start < 0 or end < start or end > text_length:
                parsed_span = None
                if warnings is not None:
                    warnings.append(f"line {line_number}: unmapped_label_span")
        if span is not None and parsed_span is None:
            raw_type = "unmapped"
        extra = {key: value for key, value in raw.items() if key not in {"type", "severity", "span"}}
        labels.append(
            Label(
                type=canonical_category(raw_type, "text") or "unmapped",
                severity=severity,
                span=parsed_span,
                annotator=raw.get("annotator"),
                extra=extra,
            )
        )
    return labels


def _validated_audio_path(dataset_path: Path, value: Any) -> tuple[Path | None, str | None]:
    if not isinstance(value, str) or not value:
        return None, "audio_path_not_relative_file"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None, "audio_path_url_not_allowed"
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None, "audio_path_outside_dataset"
    root = dataset_path.parent.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        return None, "audio_path_outside_dataset"
    if not resolved.exists():
        return None, "audio_path_missing"
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        return None, "audio_path_not_readable_file"
    if resolved.stat().st_size > MAX_AUDIO_FILE_BYTES:
        return None, "audio_path_size_limit"
    if resolved.suffix.lower() == ".wav":
        try:
            with wave.open(str(resolved), "rb") as wav:
                rate = wav.getframerate()
                duration = wav.getnframes() / rate if rate else float("inf")
        except (wave.Error, OSError, EOFError):
            return None, "audio_path_invalid_wav"
        if duration > CHECK_THRESHOLDS["speech_max_duration_s"]["value"]:
            return None, "audio_duration_limit"
    return resolved, None


def read_dataset(path: Path, langs: set[str] | None = None) -> DatasetResult:
    path = Path(path)
    result = DatasetResult()
    counts: dict[tuple[str, str], int] = {}
    normalized_langs = (
        {_normalize_language(lang) for lang in langs} if langs is not None else None
    )

    with path.open("rb") as handle:
        line_number = 0
        while raw_bytes := handle.readline(MAX_JSONL_LINE_BYTES + 1):
            line_number += 1
            if len(raw_bytes) > MAX_JSONL_LINE_BYTES and not raw_bytes.endswith(b"\n"):
                while remainder := handle.readline(MAX_JSONL_LINE_BYTES + 1):
                    if remainder.endswith(b"\n"):
                        break
                result.total_input_lines += 1
                result.quarantined.append(
                    QuarantineRec(line_number, "line_too_large", "")
                )
                continue
            if len(raw_bytes) > MAX_JSONL_LINE_BYTES:
                result.total_input_lines += 1
                result.quarantined.append(
                    QuarantineRec(line_number, "line_too_large", "")
                )
                continue
            if line_number == 1:
                raw_bytes = raw_bytes.removeprefix(b"\xef\xbb\xbf")
            if not raw_bytes.strip():
                continue
            result.total_input_lines += 1
            try:
                raw_line = raw_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                result.quarantined.append(
                    QuarantineRec(line_number, "invalid_utf8", "")
                )
                continue
            if not raw_line:
                continue
            try:
                raw = json.loads(raw_line)
            except json.JSONDecodeError as error:
                result.quarantined.append(
                    QuarantineRec(line_number, f"bad_json: {error.msg}", raw_line)
                )
                continue
            if not isinstance(raw, dict):
                result.quarantined.append(
                    QuarantineRec(line_number, "bad_json_object", raw_line)
                )
                continue
            missing = [key for key in ("id", "lang", "source") if key not in raw]
            if missing:
                result.quarantined.append(
                    QuarantineRec(line_number, f"missing_fields:{','.join(missing)}", raw_line)
                )
                continue

            lang = _normalize_language(raw["lang"])
            if normalized_langs is not None and lang not in normalized_langs:
                result.warnings.append(f"line {line_number}: lang_filtered:{lang}")
                result.skipped.append(SkippedRec(
                    line=line_number,
                    reason=f"language_filtered:{lang}",
                    id=str(raw["id"]),
                    lang=lang,
                ))
                continue
            if get_lang(lang) is None:
                result.quarantined.append(
                    QuarantineRec(line_number, f"unknown_language:{lang}", raw_line, lang)
                )
                continue

            audio_path = None
            if raw.get("audio") is not None:
                audio_path, audio_error = _validated_audio_path(path, raw["audio"])
                if audio_error is not None:
                    result.quarantined.append(
                        QuarantineRec(line_number, audio_error, raw_line, lang)
                    )
                    continue
            source = raw["source"]
            if not isinstance(source, str):
                result.quarantined.append(
                    QuarantineRec(line_number, "wrong_type:source", raw_line, lang)
                )
                continue
            translation = raw.get("translation")
            if translation is not None and not isinstance(translation, str):
                result.quarantined.append(
                    QuarantineRec(line_number, "wrong_type:translation", raw_line, lang)
                )
                continue
            reference = raw.get("reference")
            if reference is not None and not isinstance(reference, str):
                result.quarantined.append(
                    QuarantineRec(line_number, "wrong_type:reference", raw_line, lang)
                )
                continue
            item_id = _safe_text(str(raw["id"]))
            id_lang = (item_id, lang)
            count = counts.get(id_lang, 0)
            if count:
                result.warnings.append(f"duplicate id {item_id}; suffixed")
                item_id = f"{item_id}__dup{count}"
            counts[id_lang] = count + 1

            result.items.append(
                Item(
                    id=item_id,
                    lang=lang,
                    source=_safe_text(source),
                    translation=_safe_text(translation) if translation is not None else None,
                    reference=_safe_text(reference) if reference is not None else None,
                    audio=audio_path,
                    labels=_parse_labels(
                        raw["labels"] if "labels" in raw else [],
                        result.warnings,
                        line_number,
                        max(len(source), len(translation) if translation is not None else 0),
                    ),
                    raw=raw,
                )
            )
    result.items.sort(key=lambda item: (item.id, item.lang))
    return result
