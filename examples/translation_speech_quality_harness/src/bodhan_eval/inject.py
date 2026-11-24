import io
import random
import wave

import numpy as np

from .models import Item, Label


def inject_text(
    item: Item,
    category: str,
    rng: random.Random,
    translate=None,
) -> tuple[str, Label]:
    text = item.translation or item.source
    start = 0
    end = len(text)

    if category == "omission":
        parts = text.replace("।", "।\n").replace(".", ".\n").splitlines()
        if parts:
            removed = rng.choice(parts)
            start = text.find(removed)
            end = start + len(removed)
            changed = text[:start] + text[end:]
        else:
            changed = text[:-max(1, len(text) // 4)]
            start = len(changed)
            end = len(text)
        return changed, Label(category, "major", (start, min(end, len(changed))), "injector", {"method": "whole_sentence_delete"})

    if category == "addition":
        insertion = rng.choice([text, "यह अतिरिक्त वाक्य है।"])
        position = rng.choice([0, len(text)])
        changed = text[:position] + insertion + text[position:]
        start = position
        end = position + len(insertion)
        return changed, Label(category, "minor", (start, end), "injector", {"method": "duplicate_or_external_sentence"})

    if category == "mistranslation":
        perturbed_source = item.source
        replacements = (
            (" is ", " is not "),
            (" before ", " after "),
            (" increases ", " decreases "),
            (" no ", " "),
        )
        for old, new in replacements:
            if old in perturbed_source:
                perturbed_source = perturbed_source.replace(old, new, 1)
                break
        if callable(translate):
            translated = translate(perturbed_source, item.lang)
            if isinstance(translated, str) and translated:
                changed = translated
                start = 0
                end = min(len(changed), max(1, len(changed) // 4))
                method = "english_perturbation_retranslate"
                return changed, Label(category, "major", (start, end), "injector", {"method": method})
        words = text.split()
        if len(words) >= 2:
            index = rng.randrange(len(words))
            replacement = "परिवर्तित-अनुवाद"
            words[index] = replacement
            changed = " ".join(words)
            start = changed.find(replacement)
            end = start + len(replacement)
        else:
            replacement = "परिवर्तित-अनुवाद"
            changed = text + " " + replacement
            start = len(text) + 1
            end = len(changed)
        return changed, Label(category, "major", (start, end), "injector", {"method": "programmatic_indic_mistranslation"})

    if category == "number":
        digit_positions = [index for index, char in enumerate(text) if char.isdigit()]
        if not digit_positions:
            digit_positions = [
                index for index, char in enumerate(text)
                if "\u0966" <= char <= "\u096f" or "\u09e6" <= char <= "\u09ef"
            ]
        if digit_positions:
            position = rng.choice(digit_positions)
            char = text[position]
            replacement = "0" if char != "0" else "1"
            changed = text[:position] + replacement + text[position + 1:]
            return changed, Label(category, "major", (position, position + 1), "injector", {"method": "number_digit_change"})
        changed = text + " 1"
        return changed, Label(category, "major", (len(text), len(changed)), "injector", {"method": "number_insert"})

    if category == "term":
        words = [word for word in text.split() if word]
        if words:
            position = rng.randrange(len(words))
            words[position] = "परिवर्तित-" + words[position]
            changed = " ".join(words)
            start = changed.find(words[position])
        else:
            changed = text + " term"
            start = len(text)
        end = start + len(words[position]) if words else len(changed)
        return changed, Label(category, "major", (start, end), "injector", {"method": "repeated_term_variant"})

    if category == "format":
        changed = text.replace("# ", "", 1).replace("- ", "", 1).replace("|", "", 1)
        position = text.find("#") if "#" in text else text.find("-") if "-" in text else text.find("|")
        if position < 0:
            position = 0
        return changed, Label(category, "major", (position, min(position + 1, len(changed))), "injector", {"method": "markdown_marker_removal"})

    if category == "language":
        changed = item.source if item.source else "untranslated"
        return changed, Label(category, "major", (0, len(changed)), "injector", {"method": "untranslated_source"})

    raise ValueError(f"unknown text injector category: {category}")


def inject_speech(wav, category: str, rng: random.Random) -> tuple[object, Label]:
    if isinstance(wav, bytes):
        try:
            with wave.open(io.BytesIO(wav), "rb") as handle:
                sr = handle.getframerate()
                data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").copy()
        except wave.Error:
            data = np.array([], dtype="<i2")
            sr = 24000
    elif hasattr(wav, "samples"):
        sr = wav.sr
        data = (wav.samples * 32767).astype("<i2")
    else:
        sr = 24000
        data = np.array(wav, dtype="<i2")

    length = len(data)
    if category == "speech_omission":
        cut = max(1, int(length * 0.2))
        changed = data[:-cut]
        span = (max(0.0, (length - cut) / sr), length / sr)
        return changed, Label(category, "major", None, "injector", {"method": "tail_truncation", "audio_span_s": span})

    if category == "repetition":
        segment = max(1, int(sr))
        start = rng.randrange(0, max(1, length - segment))
        changed = np.concatenate([data, data[start:start + segment]])
        span = ((length + start) / sr, (length + start + segment) / sr)
        return changed, Label(category, "minor", None, "injector", {"method": "duplicate_voiced_segment", "audio_span_s": span})

    if category == "garbled":
        changed = data.copy()
        if length:
            start = rng.randrange(0, length)
            end = min(length, start + max(1, int(sr)))
            changed[start:end] = np.random.default_rng(rng.randrange(2**32)).integers(-32768, 32767, end - start, dtype="<i2")
        span = (start / sr, end / sr) if length else (0.0, 0.0)
        return changed, Label(category, "major", None, "injector", {"method": "noise_burst", "audio_span_s": span})

    raise ValueError(f"unknown speech injector category: {category}")


def build_dataset(clean_items: list[Item], seed: int, error_rate: float = 0.5, **kwargs) -> list[Item]:
    rng = random.Random(seed)
    categories = ("omission", "addition", "mistranslation", "number", "term", "format", "language")
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
            changed, label = inject_text(copy, category, rng)
            copy.translation = changed
            copy.labels = [label]
        built.append(copy)
    return built
