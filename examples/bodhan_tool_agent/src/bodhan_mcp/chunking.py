import re

# Sentence endings: Hindi danda, full stop, ? and !
SENTENCE_END = re.compile(r"(?<=[।.!?])\s+")


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Cut a very long sentence at a comma or space, never in the middle of a word."""
    parts = []
    while len(sentence) > max_chars:
        window = sentence[:max_chars]
        cut = max(window.rfind(","), window.rfind(" "))
        cut = cut + 1 if cut > 0 else max_chars
        parts.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        parts.append(sentence)
    return parts


def split_for_tts(text: str, max_chars: int = 200) -> list[str]:
    """One sentence per chunk. Long sentences are cut at commas or spaces."""
    chunks = []
    for sentence in SENTENCE_END.split(text.strip()):
        sentence = sentence.strip()
        if sentence:
            chunks.extend(_split_long(sentence, max_chars))
    return chunks

def split_markdown(text: str, max_chars: int = 3000) -> list[str]:
    """Cut a document at blank lines into pieces of at most max_chars.
    A table or list stays whole because it has no blank lines inside."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    chunks, current = [], ""
    for block in blocks:
        if len(block) > max_chars:
            # a huge block: cut it at line breaks instead
            if current:
                chunks.append(current)
                current = ""
            piece = ""
            for line in block.split("\n"):
                if piece and len(piece) + len(line) + 1 > max_chars:
                    chunks.append(piece)
                    piece = ""
                piece = f"{piece}\n{line}" if piece else line
            if piece:
                chunks.append(piece)
        elif current and len(current) + len(block) + 2 > max_chars:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks