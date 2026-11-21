import random

from .models import Item, Label


TEXT_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number",
    "term",
    "format",
    "language",
)


def inject_holdout_text(
    item: Item,
    category: str,
    rng: random.Random,
    translate=None,
    pool: list[Item] | None = None,
) -> tuple[str, Label]:
    text = item.translation or item.source

    if category == "omission":
        candidates = [index for index, char in enumerate(text) if char in "।."]
        if candidates:
            position = rng.choice(candidates)
            changed = text[:position].rstrip() + text[position + 1:]
            start = 0
            end = min(len(changed), max(1, position))
            method = "trailing_clause_delete"
        else:
            cut = max(1, len(text) // 3)
            changed = text[cut:].lstrip()
            start = 0
            end = min(len(changed), cut)
            method = "leading_clause_delete"
        return changed, Label(
            category,
            "major",
            (start, end),
            "injector",
            {"method": method, "injector": "B"},
        )

    if category == "addition":
        duplication = text.strip()
        if not duplication:
            duplication = "दूसरा वाक्य"
        position = len(text)
        changed = text + " " + duplication
        return changed, Label(
            category,
            "minor",
            (position, len(changed)),
            "injector",
            {"method": "own_sentence_duplicate", "injector": "B"},
        )

    if category == "mistranslation":
        perturbed = item.source
        replacements = (
            ("not ", ""),
            (" is ", " is not "),
            (" increases ", " decreases "),
            (" before ", " after "),
        )
        selected = rng.choice(replacements)
        perturbed = perturbed.replace(selected[0], selected[1], 1)
        if translate is not None:
            changed = translate(perturbed, item.lang)
        else:
            changed = text + " गलत"
        start = max(0, len(changed) - min(len(changed), 5))
        return changed, Label(
            category,
            "major",
            (start, len(changed)),
            "injector",
            {
                "method": "english_perturbation_retranslate",
                "injector": "B",
            },
        )

    if category == "number":
        replacements = (
            ("km", "m"),
            ("kg", "g"),
            ("°C", "°F"),
            ("m", "km"),
            ("g", "kg"),
        )
        for old, new in replacements:
            if old in text:
                position = text.find(old)
                changed = text[:position] + new + text[position + len(old):]
                return changed, Label(
                    category,
                    "major",
                    (position, position + len(new)),
                    "injector",
                    {"method": "unit_symbol_swap", "injector": "B"},
                )
        position = rng.randrange(len(text) + 1)
        changed = text[:position] + " 1" + text[position:]
        return changed, Label(
            category,
            "major",
            (position, position + 2),
            "injector",
            {"method": "number_insert", "injector": "B"},
        )

    if category == "term":
        words = [word for word in text.split() if word]
        if words:
            position = rng.randrange(len(words))
            replacement = "प्रतिशब्द"
            prefix = " ".join(words[:position])
            start = len(prefix) + (1 if prefix else 0)
            words[position] = replacement
            changed = " ".join(words)
            return changed, Label(
                category,
                "major",
                (start, start + len(replacement)),
                "injector",
                {"method": "cross_item_content_term_variant", "injector": "B"},
            )
        changed = text + " प्रतिशब्द"
        return changed, Label(
            category,
            "major",
            (len(text), len(changed)),
            "injector",
            {"method": "term_insert", "injector": "B"},
        )

    if category == "format":
        if "$" in text:
            position = text.rfind("$")
            changed = text[:position] + text[position + 1:]
            span = (position, position + 1)
        else:
            position = len(text)
            changed = text + " $"
            span = (position, len(changed))
        return changed, Label(
            category,
            "major",
            span,
            "injector",
            {"method": "unbalanced_dollar", "injector": "B"},
        )

    if category == "language":
        if pool:
            alternatives = [
                candidate.translation
                for candidate in pool
                if candidate.lang == item.lang
                and candidate.translation
                and candidate.translation != item.translation
            ]
            if alternatives:
                changed = rng.choice(alternatives)
                return changed, Label(
                    category,
                    "major",
                    (0, len(changed)),
                    "injector",
                    {"method": "same_language_other_item", "injector": "B"},
                )
        changed = item.source
        return changed, Label(
            category,
            "major",
            (0, len(changed)),
            "injector",
            {"method": "untranslated_source", "injector": "B"},
        )

    raise ValueError(f"unknown injector B category: {category}")


def inject_holdout_speech(wav, category: str, rng: random.Random):
    import io
    import wave

    import numpy as np

    if isinstance(wav, bytes):
        with wave.open(io.BytesIO(wav), "rb") as handle:
            sr = handle.getframerate()
            data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").copy()
    elif hasattr(wav, "samples"):
        sr = wav.sr
        data = (wav.samples * 32767).astype("<i2")
    else:
        sr = 24000
        data = np.array(wav, dtype="<i2")

    length = len(data)
    if category == "speech_omission":
        cut = max(1, int(length * 0.18))
        start = rng.randrange(0, max(1, length - cut))
        changed = np.concatenate([data[:start], data[start + cut:]])
        span = (start / sr, (start + cut) / sr)
        return changed, Label(
            category,
            "major",
            None,
            "injector",
            {"method": "middle_voiced_omission", "injector": "B", "audio_span_s": span},
        )
    if category == "repetition":
        segment = min(max(1, int(sr)), max(1, length))
        start = rng.randrange(0, max(1, length - segment + 1))
        changed = np.concatenate([data[:segment], data, data[segment:]])
        span = (segment / sr, (segment + segment) / sr)
        return changed, Label(
            category,
            "minor",
            None,
            "injector",
            {"method": "leading_segment_duplicate", "injector": "B", "audio_span_s": span},
        )
    if category == "garbled":
        changed = data.copy()
        if length:
            start = rng.randrange(0, length)
            end = min(length, start + max(1, int(sr * 1.5)))
            changed[start:end] = -32768
        else:
            start = end = 0
        span = (start / sr, end / sr)
        return changed, Label(
            category,
            "major",
            None,
            "injector",
            {"method": "saturation_burst", "injector": "B", "audio_span_s": span},
        )
    raise ValueError(f"unknown injector B speech category: {category}")


def build_injector_b_dataset(
    clean_items: list[Item],
    seed: int,
    error_rate: float = 0.5,
) -> list[Item]:
    rng = random.Random(seed)
    categories = TEXT_CATEGORIES
    built: list[Item] = []
    for item in clean_items:
        copy = Item(
            id=item.id,
            lang=item.lang,
            source=item.source,
            translation=item.translation,
            reference=item.reference,
            audio=item.audio,
            labels=[],
            raw=dict(item.raw),
        )
        if rng.random() < error_rate:
            category = rng.choice(categories)
            changed, label = inject_holdout_text(
                copy,
                category,
                rng,
                pool=clean_items,
            )
            copy.translation = changed
            copy.labels = [label]
        built.append(copy)
    return built
