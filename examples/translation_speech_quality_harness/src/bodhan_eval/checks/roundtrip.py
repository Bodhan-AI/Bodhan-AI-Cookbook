import difflib
from dataclasses import dataclass

from ..config import CHECK_DEFAULTS
from ..models import Flag, Item
from ..spans import sentence_split


@dataclass(frozen=True)
class Seg:
    text: str
    span: tuple[int, int]


@dataclass(frozen=True)
class Alignment:
    pairs: list[tuple[int, int, float]]


def segment(text: str, lang: str) -> list[Seg]:
    return [Seg(seg.text, seg.span) for seg in sentence_split(text, lang)]


def align(src_segs: list[Seg], bt_segs: list[Seg]) -> Alignment:
    pairs: list[tuple[int, int, float]] = []
    for src_index, source in enumerate(src_segs):
        if not bt_segs:
            break
        ratios = [
            difflib.SequenceMatcher(None, source.text.lower(), bt.text.lower()).ratio()
            for bt in bt_segs
        ]
        bt_index = max(range(len(bt_segs)), key=lambda index: ratios[index])
        pairs.append((src_index, bt_index, ratios[bt_index]))
    return Alignment(pairs=pairs)


def back_translate(client, segs: list[Seg], lang: str) -> list[str]:
    del lang
    return [seg.text for seg in segs]


def roundtrip_candidates(item: Item, stats) -> list[Flag]:
    del stats
    if not item.translation:
        return []
    source_segs = segment(item.source, "en")
    target_segs = segment(item.translation, item.lang)
    alignment = align(source_segs, target_segs)
    if len(alignment.pairs) < len(source_segs):
        missing = source_segs[len(alignment.pairs):]
        if missing:
            seg = missing[0]
            return [Flag(
                category="omission",
                severity="major",
                modality="text",
                source_span=seg.span,
                evidence={"detector": "back_translation_alignment"},
                detectors=["roundtrip"],
                confidence=CHECK_DEFAULTS["confidence"]["roundtrip"],
                suppressed_reason="single_noisy_signal",
            )]
    return []
