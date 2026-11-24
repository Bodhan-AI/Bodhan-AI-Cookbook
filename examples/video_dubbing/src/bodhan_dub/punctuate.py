"""Restore punctuation and casing on the English transcript (English only, words never change).

The LLM edits punctuation/case only. Every chunk's output is checked: its word sequence must equal
the input's (ignoring case and punctuation). Chunks that fail are retried, then fall back to rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .llm import chat

log = logging.getLogger(__name__)

PAD = 0.15  # padding chunking adds on each side; used to recover the real pause length

SYSTEM_PROMPT = """You restore punctuation and capitalization in speech-to-text transcripts.

The transcript comes in numbered lines (one per audio chunk) with no punctuation, and sometimes in ALL CAPS.
Output the same lines with punctuation and normal capitalization added.

Rules:
1. NEVER add, remove, replace, reorder or "correct" any word. Keep every word exactly as given; only case and punctuation may change.
2. Numbers written as words stay as words.
3. Use normal sentence case. Capitalize proper nouns. Keep acronyms uppercase (e.g. ATP, DNA).
4. Add commas, periods, question marks and exclamation marks as a careful human editor would.
5. Use . ? ! at the end of a line ONLY if the sentence really ends there. If the sentence continues in the next line, end the line without . ? ! (a comma or nothing).
5b. If the PREVIOUS line did not end its sentence (no . ? !), start this line in lowercase, because it continues that sentence. Keep capitals only for "I", proper nouns and acronyms.
6. Each line shows the pause (seconds) after it. A long pause often marks a sentence end, but not always; meaning decides.
7. Output exactly one line per input line, same ids, same order, as `<id>|<text>`. Do not merge or split lines. No extra commentary.
8. Lines under CONTEXT are for reference only. Do not output them."""


def norm_words(text: str) -> list[str]:
    """Lowercase words with punctuation and apostrophes removed, for the 'words unchanged' check."""
    t = text.lower().replace("’", "'").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", t).split()


def words_unchanged(original: str, edited: str) -> bool:
    """True if letters/digits are identical. Spaces are ignored so 'E R' -> 'ER' is allowed,
    but any changed, added or dropped word (e.g. 'a inner' -> 'an inner') is rejected."""
    return "".join(norm_words(original)) == "".join(norm_words(edited))


def rule_punctuate(text: str, pause_after: float) -> str:
    """Fallback: sentence-case, and end with '.' if a real pause follows."""
    t = text.strip()
    if not t:
        return t
    if t.isupper():
        t = t.lower()
    t = t[0].upper() + t[1:]
    t = re.sub(r"\bi\b", "I", t)
    if pause_after >= 0.6 and t[-1] not in ".?!":
        t += "."
    return t


def build_prompt(batch: list[dict], before: list[dict], after: list[dict], note: str = "") -> list[dict]:
    def fmt(c: dict) -> str:
        return f"{c['id']}|pause={c['pause_after']:.1f}|{c['text_raw']}"

    parts = []
    if before:
        parts.append("CONTEXT (before):\n" + "\n".join(fmt(c) for c in before))
    parts.append("LINES TO EDIT:\n" + "\n".join(fmt(c) for c in batch))
    if after:
        parts.append("CONTEXT (after):\n" + "\n".join(fmt(c) for c in after))
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]


def parse_reply(reply: str) -> dict[int, str]:
    out = {}
    for line in reply.splitlines():
        m = re.match(r"^\s*(\d+)\s*\|\s*(?:pause=[\d.]+\s*\|\s*)?(.*)$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def add_pauses(raw: list[dict]) -> list[dict]:
    """Attach text_raw and pause_after (real silence after the chunk; last chunk gets 9.9)."""
    items = []
    for i, c in enumerate(raw):
        if i + 1 >= len(raw):
            pause = 9.9
        else:
            n = raw[i + 1]
            if c.get("vad_end") is not None and n.get("vad_start") is not None:
                pause = round(max(0.0, n["vad_start"] - c["vad_end"]), 1)  # exact
            else:  # older files: recover from padded edges
                gap = n["start"] - c["end"]
                pause = round(gap + 2 * PAD, 1) if gap > 0 else 0.0
        items.append({**c, "text_raw": c.get("text", ""), "pause_after": pause})
    return items


def punctuate_chunks(
    raw: list[dict],
    work_dir: str | Path,
    llm: Callable[[list[dict]], str] = chat,
    batch_size: int = 20,
    context: int = 3,
    concurrency: int = 2,
    max_attempts: int = 3,
) -> list[dict]:
    """Return chunks with `text` (punctuated), `punct_method` ('llm' | 'rule' | 'empty'), `punct_attempts`."""
    work_dir = Path(work_dir)
    cache_dir = work_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    items = add_pauses(raw)
    by_pos = {c["id"]: i for i, c in enumerate(items)}
    results: dict[int, dict] = {}

    for c in items:
        if not c["text_raw"].strip():
            results[c["id"]] = {"text": "", "punct_method": "empty", "punct_attempts": 0}

    def chunk_cache(c: dict) -> Path:
        # key includes the prompt, so changing the prompt invalidates old results
        h = hashlib.sha256((SYSTEM_PROMPT + "\n" + c["text_raw"]).encode()).hexdigest()[:24]
        return cache_dir / f"punct_{h}.json"

    def call(batch: list[dict], attempt: int) -> dict[int, str]:
        first, last = by_pos[batch[0]["id"]], by_pos[batch[-1]["id"]]
        note = "" if attempt == 1 else "Your previous answer changed some words. Keep EVERY word exactly as given."
        msgs = build_prompt(batch, items[max(0, first - context) : first], items[last + 1 : last + 1 + context], note)
        return parse_reply(llm(msgs))

    def run_batch(batch: list[dict]) -> None:
        pending = []
        for c in batch:  # validated results are cached per chunk, so reruns cost nothing
            f = chunk_cache(c)
            if f.is_file():
                results[c["id"]] = {"text": json.loads(f.read_text(encoding="utf-8"))["text"],
                                    "punct_method": "llm", "punct_attempts": 0}
            else:
                pending.append(c)
        for attempt in range(1, max_attempts + 1):
            if not pending:
                return
            try:
                parsed = call(pending, attempt)
            except Exception as e:
                log.warning("punctuate: LLM call failed (attempt %d): %s", attempt, e)
                parsed = {}
            still = []
            for c in pending:
                out = parsed.get(c["id"])
                if out is not None and words_unchanged(c["text_raw"], out):
                    results[c["id"]] = {"text": out, "punct_method": "llm", "punct_attempts": attempt}
                    chunk_cache(c).write_text(json.dumps({"text": out}, ensure_ascii=False), encoding="utf-8")
                else:
                    log.warning("punctuate: chunk=%d rejected on attempt %d (%s)", c["id"], attempt,
                                "missing" if out is None else "words changed")
                    still.append(c)
            pending = still
        for c in pending:
            results[c["id"]] = {"text": rule_punctuate(c["text_raw"], c["pause_after"]),
                                "punct_method": "rule", "punct_attempts": max_attempts}

    todo = [c for c in items if c["id"] not in results]
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(run_batch, batches))

    out = [{**c, **results[c["id"]]} for c in items]
    (work_dir / "transcript_punct.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n_rule = sum(r["punct_method"] == "rule" for r in out)
    log.info("punctuate: %d chunks, %d via LLM, %d via rule fallback", len(out),
             sum(r["punct_method"] == "llm" for r in out), n_rule)
    return out


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    work = Path(sys.argv[1])
    punctuate_chunks(json.loads((work / "transcript_raw.json").read_text(encoding="utf-8")), work)
