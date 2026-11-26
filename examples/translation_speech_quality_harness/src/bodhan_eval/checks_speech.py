"""Speech checks: back-transcription vs input text, plus audio sanity metrics.

ASR makes its own mistakes, so omission is judged on *contiguous deleted runs* and tail loss
(ASR errors are mostly scattered substitutions), not on raw WER.
"""
from __future__ import annotations

from .align import diff_ops, levenshtein, token_span
from .audio import decode, metrics
from .normalize import normalize_text, tokenize


def speech_signal(text: str, transcript: str | None, audio: bytes) -> dict:
    x, sr = decode(audio)
    n_chars = len(normalize_text(text).replace(" ", ""))
    sig: dict = {"audio": metrics(x, sr, n_chars)}
    if transcript is None:
        return sig
    tt, ht = tokenize(text), tokenize(transcript)
    a, b = [t.text for t in tt], [t.text for t in ht]
    ops = diff_ops(a, b)
    del_runs, tail = [], 0
    deleted = 0
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("delete", "replace") and (i2 - i1) > (j2 - j1):
            n = (i2 - i1) - (j2 - j1) if tag == "replace" else (i2 - i1)
            start = i1 if tag == "delete" else i1 + (j2 - j1)
            deleted += n
            del_runs.append({"tokens": n, "span": token_span(tt, start, i2)})
            if i2 == len(a):
                tail = n
    ref_c, hyp_c = normalize_text(text).replace(" ", ""), normalize_text(transcript).replace(" ", "")
    cer = levenshtein(ref_c, hyp_c) / max(1, len(ref_c)) if len(ref_c) < 3000 else None
    sig["transcript"] = transcript
    sig["cer"] = None if cer is None else round(min(cer, 5.0), 4)
    sig["n_text_tokens"] = len(a)
    sig["deleted_frac"] = round(deleted / max(1, len(a)), 4)
    sig["max_deleted_run"] = max((r["tokens"] for r in del_runs), default=0)
    sig["deleted_runs"] = sorted(del_runs, key=lambda r: -r["tokens"])[:3]
    sig["tail_deleted"] = tail
    sig["repetition"] = find_repetition(tt, ht)
    return sig


def find_repetition(text_toks, hyp_toks) -> dict | None:
    """An n-gram (n>=2) that appears back-to-back in the transcript but not in the input text."""
    a, b = [t.text for t in text_toks], [t.text for t in hyp_toks]

    def has_adjacent(seq, gram):
        n = len(gram)
        return any(seq[i:i + n] == gram and seq[i + n:i + 2 * n] == gram for i in range(len(seq) - 2 * n + 1))

    best = None
    for n in range(2, 9):
        for i in range(len(b) - 2 * n + 1):
            gram = b[i:i + n]
            if b[i + n:i + 2 * n] == gram and not has_adjacent(a, gram) and len(set(gram)) > 1:
                if best is None or n > best["n"]:
                    span = None
                    for k in range(len(a) - n + 1):
                        if a[k:k + n] == gram:
                            span = token_span(text_toks, k, k + n)
                            break
                    best = {"n": n, "gram": " ".join(gram), "span": span}
    return best
