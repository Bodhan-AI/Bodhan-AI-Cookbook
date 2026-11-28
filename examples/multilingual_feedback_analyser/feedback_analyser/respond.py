"""Draft a reply in the customer's language, check it by translating it back, and voice it."""

from __future__ import annotations

import re

from .audio import join_wavs
from .languages import LANGUAGE_SCRIPT, TRANSLATE_LANGUAGES, TRANSLITERATE_LANGUAGES, VOICES, dominant_script

FIDELITY_THRESHOLD = 0.5
SPEECH_STYLE = "Customer Care"
MAX_SPEECH_CHARS = 250
SENTENCE_END = re.compile(r"(?<=[।.!?॥])\s+")
WORD = re.compile(r"[a-z0-9']+")
STOPWORDS = frozenset(
    "a an the is are was were be been am to of in on at for and or that this it its with by as from will would shall "
    "we you your our us i me my have has had do does did not no".split()
)


CONTRACTIONS = (("won't", "will not"), ("can't", "can not"), ("n't", " not"), ("'re", " are"), ("'ll", " will"), ("'ve", " have"), ("'m", " am"), ("'s", ""), ("'d", ""))


def content_words(text: str) -> list[str]:
    text = text.lower().replace("\u2019", "'")
    for short, full in CONTRACTIONS:
        text = text.replace(short, full)
    return [word for word in WORD.findall(text) if word not in STOPWORDS]


def fidelity(reference: str, back_translation: str) -> float:
    expected = content_words(reference)
    actual = content_words(back_translation)
    if not expected or not actual:
        return 0.0
    remaining = list(actual)
    overlap = 0
    for word in expected:
        if word in remaining:
            remaining.remove(word)
            overlap += 1
    if not overlap:
        return 0.0
    precision = overlap / len(actual)
    recall = overlap / len(expected)
    return round(2 * precision * recall / (precision + recall), 3)


def speech_chunks(text: str, limit: int = MAX_SPEECH_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for sentence in (part.strip() for part in SENTENCE_END.split(text.strip())):
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def draft_reply(client, english_reply: str, language: str, customer_script: str | None) -> dict:
    reply = {"english": english_reply, "language": language, "text": english_reply, "native": english_reply,
             "back_translation": None, "fidelity": None, "needs_review": False, "review_reasons": [], "audio": None}
    if language == "en":
        return reply
    if language not in TRANSLATE_LANGUAGES:
        reply["needs_review"] = True
        reply["review_reasons"].append(f"indic-translate does not cover '{language}', so the reply is left in English")
        return reply

    native = client.translate(english_reply, language)
    back = client.translate(native, "en")
    score = fidelity(english_reply, back)
    reply.update(native=native, text=native, back_translation=back, fidelity=score)
    if score < FIDELITY_THRESHOLD:
        reply["review_reasons"].append(f"back-translation overlap {score:.2f} is below {FIDELITY_THRESHOLD:.2f}")
    expected_script = LANGUAGE_SCRIPT.get(language)
    if expected_script and dominant_script(native) != expected_script:
        reply["review_reasons"].append(f"reply is not written in {expected_script} script")
    if customer_script == "Latin" and language in TRANSLITERATE_LANGUAGES:
        reply["text"] = client.transliterate(native, language, "roman")
    reply["needs_review"] = bool(reply["review_reasons"])
    return reply


def voice_reply(client, reply: dict) -> bytes | None:
    voice = VOICES.get(reply["language"])
    if not voice or reply["needs_review"]:
        return None
    clips = [client.speak(chunk, voice, reply["language"], SPEECH_STYLE) for chunk in speech_chunks(reply["native"])]
    return join_wavs(clips) if clips else None
