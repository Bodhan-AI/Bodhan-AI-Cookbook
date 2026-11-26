import re


def split_paragraphs(text):
    """Split text into paragraphs at blank lines."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def split_sentences(text):
    """Split after a sentence end mark, keeping the mark.

    Marks: . ? ! and the danda and double danda (Devanagari, Bengali, Odia ...),
    the Urdu full stop and question mark, the Meitei cheikhei, the Ol Chiki marks.
    """
    parts = re.split(r"(?<=[.?!\u0964\u0965\u06d4\u061f\uabeb\u1c7e\u1c7f])\s+", text.strip())
    return [p for p in parts if p]

def chunk_sentence(sentence, limit=250):
    """Split one long sentence into pieces of at most `limit` characters.

    First try to cut after commas or semicolons; if a piece is still too long,
    cut between words; as a last resort, cut by characters.
    """
    sentence = sentence.strip()
    if len(sentence) <= limit:
        return [sentence] if sentence else []

    pieces = re.split(r"(?<=[,;])\s+", sentence)
    chunks = []
    current = ""
    for piece in pieces:
        for part in _split_by_words(piece, limit):
            if not current:
                current = part
            elif len(current) + 1 + len(part) <= limit:
                current = current + " " + part
            else:
                chunks.append(current)
                current = part
    if current:
        chunks.append(current)
    return chunks


def _split_by_words(text, limit):
    """Break text between words so each part is at most `limit` characters."""
    if len(text) <= limit:
        return [text]
    parts = []
    current = ""
    for word in text.split():
        while len(word) > limit:  # a single huge word: cut it by characters
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:limit])
            word = word[limit:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current += " " + word
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


_EMOJI = "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\u231A\u231B\u23E9-\u23FA]"
EMOJI_PATTERN = re.compile(f"{_EMOJI}\ufe0f?(?:\u200d{_EMOJI}\ufe0f?)*|[\ufe0f\u20e3]")
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S*[^\s.,;:!?)\]\"'\u0964\u0965]", re.IGNORECASE)


def clean_for_speech(text):
    """Remove emoji and web links, which should not be read aloud."""
    text = URL_PATTERN.sub(" ", text)
    text = EMOJI_PATTERN.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?\u0964\u0965])", r"\1", text)
    return text.strip()


_DIGIT_RUN = re.compile(r"(?<![A-Za-z0-9])[0-9]+(?![A-Za-z0-9])")


def to_native_digits(text, digits):
    """Write 0-9 with a language's own digits (a string of ten) so the voice reads them in that language.

    Numbers stuck to English letters (5G, COVID19, A4) are left alone.
    """
    if not digits:
        return text
    table = str.maketrans("0123456789", digits)
    return _DIGIT_RUN.sub(lambda match: match.group().translate(table), text)