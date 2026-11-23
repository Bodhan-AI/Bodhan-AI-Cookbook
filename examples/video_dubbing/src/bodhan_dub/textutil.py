"""Text helpers: splitting target text for TTS, glossary handling, subtitle wrapping."""
import re
import textwrap
from pathlib import Path

import yaml

_SENT = re.compile(r"(?<=[.!?।॥])\s+")
_COMMA = re.compile(r"(?<=[,;:،])\s+")


def split_for_tts(text: str, max_chars: int = 220) -> list[str]:
    """Split at sentence ends, then commas, then spaces, so pieces stay short and no word is cut."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    out = []
    for s in _SENT.split(text):
        if len(s) <= max_chars:
            out.append(s)
            continue
        for c in _COMMA.split(s):
            if len(c) <= max_chars:
                out.append(c)
            else:
                out += textwrap.wrap(c, max_chars, break_long_words=False)
    merged = []  # merge tiny neighbours back together
    for piece in out:
        if merged and len(merged[-1]) + 1 + len(piece) <= max_chars and (len(merged[-1]) < 30 or len(piece) < 30):
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return [m.strip() for m in merged if m.strip()]


def load_yaml(path: str | Path | None) -> dict:
    if not path:
        return {}
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_glossary(path: str | Path | None) -> list[dict]:
    """glossary.yaml:  terms: [{term: "Pythagoras", pin: true, translations: {hi: "..."}}]  (or a plain list)."""
    data = load_yaml(path)
    items = data.get("terms", []) if isinstance(data, dict) else data
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"term": it, "pin": True, "translations": {}, "source": "user"})
        elif isinstance(it, dict) and it.get("term"):
            out.append({"term": it["term"], "pin": it.get("pin", True), "kind": it.get("kind", "term"),
                        "translations": it.get("translations") or {}, "source": "user"})
    return out


def merge_glossary(user: list[dict], auto: list[dict]) -> list[dict]:
    seen = {g["term"].lower() for g in user}
    return user + [dict(a, translations={}, source="llm") for a in auto if a["term"].lower() not in seen]


def terms_in(text: str, glossary: list[dict]) -> list[dict]:
    low = text.lower()
    return [g for g in glossary if re.search(r"(?<!\w)" + re.escape(g["term"].lower()) + r"(?!\w)", low)]


def apply_user_translations(text: str, glossary: list[dict], lang: str) -> tuple[str, list[str]]:
    """Swap English terms for the glossary author's own target-language rendering before translation."""
    used = []
    for g in sorted(terms_in(text, glossary), key=lambda g: -len(g["term"])):
        tr = g.get("translations", {}).get(lang)
        if tr:
            text = re.sub(r"(?<!\w)" + re.escape(g["term"]) + r"(?!\w)", tr, text, flags=re.I)
            used.append(g["term"])
    return text, used


def wrap_subtitle(text: str, width: int = 42) -> str:
    return "\n".join(textwrap.wrap(text, width, break_long_words=False)) or text


def atomic_write(path, text: str) -> None:
    """Write via temp file + rename so concurrent readers never see a partial file."""
    import os
    from pathlib import Path
    p = Path(path)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
