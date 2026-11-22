"""Programmatic mutations of supplied clean outputs; never generates Indic text."""

import argparse
import copy
import hashlib
import io
import json
import re
import wave
from pathlib import Path

from .artifacts import content_fingerprint


def split_for(parent_id):
    return "calibration" if int(hashlib.sha256(parent_id.encode()).hexdigest()[:8], 16) % 5 == 0 else "evaluation"


def mutations(clean):
    text = clean.get("translation") or ""
    if not text:
        return []
    variants = []

    def add(kind, changed, field, span, details):
        item = copy.deepcopy(clean)
        item.update(
            {
                "id": clean["id"] + "__" + kind,
                "parent_id": clean.get("parent_id", clean["id"]),
                "translation": changed,
                "labels": [{"type": kind, "severity": "major", "span_field": field, "span": span}],
                "label_origin": "programmatic_injection",
                "injection": details,
            }
        )
        # Audio from the clean translation no longer corresponds to the mutation.
        item.pop("audio", None)
        item.pop("transcript", None)
        item.pop("reviewed_content_sha256", None)
        variants.append(item)

    number = re.search(r"\d", text)
    if number:
        digit = number.group()
        import unicodedata

        value = unicodedata.decimal(digit)
        changed = chr(ord(digit) - value + (value + 1) % 10)
        add(
            "number",
            text[: number.start()] + changed + text[number.end() :],
            "translation",
            list(number.span()),
            {"operation": "increment_decimal_digit", "original": digit},
        )
    addition = " " + text
    add("addition", text + addition, "translation", [len(text) + 1, len(text + addition)], {"operation": "duplicate_translation"})
    if clean["lang"] != "en" and re.search(r"[A-Za-z]{2}", clean["source"]):
        add("wrong_language", clean["source"], "translation", [0, len(clean["source"])], {"operation": "replace_with_English_source"})
    for match in re.finditer(r"https?://[^\s)]+|\\[A-Za-z]+|\$|```", text):
        changed = text[: match.start()] + text[match.end() :]
        # Localize deletion on the source literal when present, not a nonexistent
        # translated span. Require the source to contain that literal.
        source_offset = clean["source"].find(match.group())
        if source_offset >= 0:
            add(
                "format",
                changed,
                "source",
                [source_offset, source_offset + len(match.group())],
                {"operation": "delete_protected_markup", "literal": match.group()},
            )
            break
    # Whole-paragraph omission has a precise source span when the document has
    # exactly two structurally corresponding paragraphs. No bilingual text is written.
    src_parts, dst_parts = clean["source"].split("\n\n"), text.split("\n\n")
    if len(src_parts) == len(dst_parts) == 2 and all(src_parts + dst_parts):
        start = len(src_parts[0]) + 2
        add(
            "omission",
            dst_parts[0],
            "source",
            [start, len(clean["source"])],
            {"operation": "drop_final_paragraph", "assumption": "two source/target paragraphs correspond in order; review before claiming human ground truth"},
        )
    for source_term, expected in clean.get("terms", {}).items():
        alternatives = [v for v in clean["terms"].values() if v != expected]
        if source_term in clean["source"] and expected in text and alternatives:
            start = text.index(expected)
            replacement = alternatives[0]
            add(
                "entity",
                text[:start] + replacement + text[start + len(expected) :],
                "translation",
                [start, start + len(replacement)],
                {"operation": "swap_protected_target_terms", "source_term": source_term},
            )
            break
    return variants


def perturb_audio(data, kind):
    with wave.open(io.BytesIO(data), "rb") as audio:
        params = audio.getparams()
        frames = audio.readframes(params.nframes)
    if params.sampwidth != 2 or params.comptype != "NONE" or not params.nframes:
        raise ValueError("audio injection requires nonempty PCM16 WAV")
    size = params.sampwidth * params.nchannels
    cutoff = (params.nframes * 3 // 4) * size
    if kind in ("speech_omission", "speech_repetition") and not any(frames[cutoff:]):
        raise ValueError("tail is digital silence; cannot label a speech omission/repetition")
    if kind == "speech_omission":
        result = frames[:cutoff]
        interval = [cutoff / size / params.framerate, params.nframes / params.framerate]
    elif kind == "speech_repetition":
        result = frames + frames[cutoff:]
        interval = [params.nframes / params.framerate, (len(result) / size) / params.framerate]
    elif kind == "garbled_audio":
        result = bytes(len(frames))
        interval = [0, params.nframes / params.framerate]
    else:
        raise ValueError("unknown audio perturbation")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams(params)
        audio.writeframes(result)
    return output.getvalue(), interval


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="clean supplied translations, preferably Bodhan-generated")
    parser.add_argument("--out", required=True)
    parser.add_argument("--require-human-clean", action="store_true", help="require content-bound human clean decisions on every parent")
    args = parser.parse_args(argv)
    source, destination = Path(args.dataset).resolve(), Path(args.out).resolve()
    if source == destination:
        parser.error("input and output must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = []
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not item.get("translation"):
            raise ValueError(f"{item['id']}: missing translation; generate with Bodhan before injecting")
        if item.get("labels"):
            raise ValueError(f"{item['id']}: cannot inject into a known erroneous item")
        if args.require_human_clean:
            if (
                item.get("labels") != []
                or not item.get("label_origin", "").startswith("human:")
                or item.get("reviewed_content_sha256") != content_fingerprint(item, source.parent)
            ):
                raise ValueError(f"{item['id']}: require a current content-bound human clean annotation")
        parent = item.get("parent_id", item["id"])
        item.update({"parent_id": parent, "split": split_for(parent)})
        # An unreviewed output is not automatically annotated clean.
        variants = mutations(item)
        if item.get("audio"):
            audio_source = source.parent / item["audio"]
            item["audio"] = str(audio_source.resolve())
            for kind in ("speech_omission", "speech_repetition", "garbled_audio"):
                data, interval = perturb_audio(audio_source.read_bytes(), kind)
                name = hashlib.sha256((parent + item["lang"] + kind).encode()).hexdigest() + ".wav"
                path = destination.parent / "audio" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                variant = copy.deepcopy(item)
                variant.update(
                    {
                        "id": item["id"] + "__" + kind,
                        "audio": str(path),
                        "label_origin": "programmatic_injection",
                        "labels": [{"type": kind, "severity": "major", "time_span": interval}],
                        "injection": {"operation": kind, "time_span": interval},
                    }
                )
                variant.pop("transcript", None)
                variant.pop("reviewed_content_sha256", None)
                variants.append(variant)
        result.extend([item] + variants)
    destination.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in result), encoding="utf-8")
    print(f"Wrote {len(result)} clean-parent and injected items to {destination}")


if __name__ == "__main__":
    main()
