import random

from bodhan_eval.spans import overlap, sentence_split, to_original_span
from bodhan_eval.normalize import normalize


def test_to_original_span_maps_normalized_span():
    norm = normalize("१२,000\u200d रुपये", "hi")
    span = to_original_span(norm, (0, 6))
    assert 0 <= span[0] <= span[1] <= len("१२,000\u200d रुपये")


def test_overlap_modes():
    assert overlap((0, 10), (5, 15), "iou") == 0.3333333333333333
    assert overlap((0, 10), (5, 15), "min") == 0.5
    assert overlap((10, 10), (10, 10), "iou") == 1.0


def test_seeded_offset_property():
    rng = random.Random(20261119)
    alphabets = {
        "hi": ["\u0915", "\u093c", "\u0958", "\u093f", "\u200d", "1", " ", "\u0964"],
        "bn": ["\u0995", "\u09bf", "\u09e7", "\u200b", " ", "\u0964"],
        "ml": ["\u0d23", "\u0d4d", "\u200d", "\u0d7a", "\u0d66", " ", "."],
        "ur": ["\u064a", "\u0643", "\u064e", "\u0640", "\u06f1", "\u200f", " ", "\u061f"],
    }
    for lang, alphabet in alphabets.items():
        for _ in range(80):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            norm = normalize(text, lang)
            assert normalize(norm.text, lang).text == norm.text
            assert len(norm.map) == len(norm.text)
            checked_spans = set()
            for index, character in enumerate(norm.text):
                start, end = to_original_span(norm, (index, index + 1))
                assert 0 <= start < end <= len(text)
                mapped_span = norm.spans[index]
                if mapped_span in checked_spans:
                    continue
                checked_spans.add(mapped_span)
                if character == " ":
                    continue
                first = next(i for i, span in enumerate(norm.spans) if span == mapped_span)
                last = max(i for i, span in enumerate(norm.spans) if span == mapped_span) + 1
                source = text[mapped_span[0]:mapped_span[1]]
                assert normalize(source, lang).text == norm.text[first:last]


def test_markdown_aware_sentence_split():
    text = "# Heading\n\n- पहला item\n- दूसरा item\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    segments = sentence_split(text, "hi")
    assert any(seg.text.startswith("-") for seg in segments)
    assert any("|" in seg.text for seg in segments)
