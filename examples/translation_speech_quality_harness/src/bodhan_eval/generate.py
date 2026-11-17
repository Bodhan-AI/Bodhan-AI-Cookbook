"""--generate: create missing translations with Indic-Translate and missing audio with Indic-Speak.

- Translations are greedy (temperature 0), so a rerun reproduces them from the cache.
- Speech is synthesised in pieces of at most MAX_SPEAK_CHARS (one call must stay under about 30 s of audio) and joined
  with a short pause. Generated audio is written to <out>/audio/ and never needs to be committed.
- Items are visited in a fixed order (hash of id and language), so when the budget runs out, the same items have audio.
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .checks_speech import read_samples, wav_bytes
from .normalize import sentence_spans

MAX_SPEAK_CHARS = 250  # about 25 s at the 0.09-0.11 s per character measured on day 1
GAP_S = 0.15  # pause inserted between pieces; well under the 2 s silence the garbled check looks for
SPEECH_BUDGET_SHARE = 0.5  # speech generation may use at most this share of --max-cost-inr, leaving the rest for checks


def order_key(item) -> str:
    return hashlib.sha256(f"{item['id']}/{item['lang']}".encode()).hexdigest()


def pieces(text: str, limit: int = MAX_SPEAK_CHARS) -> list[str]:
    """Pack whole sentences into pieces of at most `limit` characters; split a longer sentence at spaces."""
    out, cur = [], ""
    for s, e in sentence_spans(text):
        sent = text[s:e]
        while len(sent) > limit:
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return [p for p in out if p]


def speak_long(text: str, lang: str, bodhan, ctx=None) -> bytes:
    parts, sr = [], None
    for p in pieces(text):
        x, rate = read_samples(bodhan.speak(p, lang, ctx=ctx))
        if sr is not None and rate != sr:
            raise ValueError(f"sample rate changed between pieces ({sr} -> {rate})")
        sr = rate
        if parts:
            parts.append(np.zeros(int(GAP_S * sr), dtype=np.float32))
        parts.append(x)
    return wav_bytes(np.concatenate(parts), sr)


def fill(items: list[dict], results: list[dict], bodhan, audio_dir: Path, run_check, concurrency: int, max_cost_inr):
    """Fill in item['translation'] and item['audio'] where missing. Failures are recorded per item, never raised."""

    def translate(i):
        item, res = items[i], results[i]
        meta = run_check("generate_translation", lambda: bodhan.translate(item["source"], item["lang"],
                                                                          {"item": f"{item['id']}/{item['lang']}", "check": "generate"}), item, res)
        if meta and meta["text"].strip():
            item["translation"], item["generated"] = meta["text"], True

    todo = [i for i, it in enumerate(items) if not it["translation"]]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(translate, todo))

    # Speech: fixed order, and a cap so generation cannot use up the money the checks need.
    cap = None if max_cost_inr is None else bodhan.budget.spent + SPEECH_BUDGET_SHARE * (max_cost_inr - bodhan.budget.spent)
    audio_dir.mkdir(parents=True, exist_ok=True)

    def speak(i):
        item, res = items[i], results[i]
        if cap is not None and bodhan.budget.spent >= cap:
            res["checks_run"]["generate_audio"] = "not_run: speech share of --max-cost-inr used"
            return
        ctx = {"item": f"{item['id']}/{item['lang']}", "check": "generate"}
        wav = run_check("generate_audio", lambda: speak_long(item["translation"], item["lang"], bodhan, ctx), item, res)
        if wav:
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in item["id"])
            path = audio_dir / f"{safe}.{item['lang']}.wav"
            path.write_bytes(wav)
            item["audio"], item["audio_generated"] = str(path), True

    todo = sorted((i for i, it in enumerate(items) if it["translation"] and not it.get("audio")), key=lambda i: order_key(items[i]))
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(speak, todo))
