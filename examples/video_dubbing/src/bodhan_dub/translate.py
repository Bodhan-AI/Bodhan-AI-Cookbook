"""Translate dubbing units with Indic-Translate (hosted chat route), with checks and caching.

Only Indic-Translate writes target-language text. No LLM is involved here.
--code-mixed asks for "<Language> (code-mixed)" (indic-translate model card): English technical words stay in
Latin script, which Indic-Speak reads as English words (checked live for hi/ta/bn, 2026-11-20).
--glossary: every translation is checked for the glossary's renderings; a unit missing one is re-translated
once with the rendering written into the English, and the retry is kept only if it helps (see glossary.py).
Modes:  "unit"  - one unit per request (safe alignment, no context)
        "batch" - several units per request, one per line (gives context); if the number of output
                  lines differs from the input, those units are re-translated one by one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import glossary as glossary_mod
from .api import post_with_retry
from .config import BASE_URL, get_key

log = logging.getLogger(__name__)

MODEL = "indic-translate"
URL = f"{BASE_URL}/v1/chat/completions"

# Indic-Speak's 13 production-tier languages (indic-speak model card). Prompt names are the "Prompt name"
# column of the indic-translate model card; each was checked live (translate -> TTS -> ASR, 2026-11-20).
# English is a pass-through: the source text is spoken as is (re-voicing), no translation call is made.
LANG_NAMES = {"hi": "Hindi", "bn": "Bengali", "mr": "Marathi", "te": "Telugu", "ta": "Tamil", "gu": "Gujarati",
              "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese", "ur": "Urdu",
              "en": "English"}
_DEVANAGARI, _BENGALI = [(0x0900, 0x097F)], [(0x0980, 0x09FF)]   # Assamese is written in the Bengali block
SCRIPT_RANGES = {"hi": _DEVANAGARI, "mr": _DEVANAGARI, "bn": _BENGALI, "as": _BENGALI,
                 "pa": [(0x0A00, 0x0A7F)], "gu": [(0x0A80, 0x0AFF)], "or": [(0x0B00, 0x0B7F)],
                 "ta": [(0x0B80, 0x0BFF)], "te": [(0x0C00, 0x0C7F)], "kn": [(0x0C80, 0x0CFF)],
                 "ml": [(0x0D00, 0x0D7F)],
                 "ur": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],  # Perso-Arabic
                 "en": [(0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F)]}
PASS_THROUGH = {"en"}

# Provisional bounds on len(output)/len(input); to be calibrated from real runs.
LENGTH_RATIO_BOUNDS = (0.4, 3.5)
MIN_SCRIPT_SHARE = 0.5
# Code-mixed output is mostly Latin: 9 live outputs (hi/ta/bn, 2026-11-20) had 24-44% native-script letters and
# the rest Latin. So: some native script (an untranslated English line has none) and nothing but native + Latin.
CODE_MIXED_MIN_NATIVE = 0.10
CODE_MIXED_MIN_KNOWN = 0.90
SOFT = "glossary_ok"   # the one check that never rejects a translation (a glossary is a best effort)


def lang_name(code: str) -> str:
    if code not in LANG_NAMES:
        raise ValueError(f"Language '{code}' is not supported. Supported (Indic-Speak production tier): "
                         f"{','.join(sorted(LANG_NAMES))}")
    return LANG_NAMES[code]


def prompt_name(code: str, code_mixed: bool = False) -> str:
    name = lang_name(code)
    return f"{name} (code-mixed)" if code_mixed and code not in PASS_THROUGH else name


# --------------------------------------------------------------------------- checks
def script_share(text: str, code: str) -> float:
    """Share of letters that belong to the language's script (letters of any other script count against it)."""
    ranges = SCRIPT_RANGES[code]
    letters = [ch for ch in text if ch.isalpha()]
    inside = sum(any(lo <= ord(ch) <= hi for lo, hi in ranges) for ch in letters)
    return inside / len(letters) if letters else 0.0


def latin_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    return sum(ch.isascii() for ch in letters) / len(letters) if letters else 0.0


def script_ok(text: str, code: str, code_mixed: bool = False) -> bool:
    native = script_share(text, code)
    if not code_mixed or code in PASS_THROUGH:
        return native >= MIN_SCRIPT_SHARE
    return native >= CODE_MIXED_MIN_NATIVE and native + latin_share(text) >= CODE_MIXED_MIN_KNOWN


def has_repetition(text: str, min_repeats: int = 4) -> bool:
    """True if a run of 1-4 words repeats back-to-back at least min_repeats times (looping output)."""
    w = text.split()
    for n in range(1, 5):
        for i in range(len(w) - n * min_repeats + 1):
            seg = w[i : i + n]
            if all(w[i + k * n : i + (k + 1) * n] == seg for k in range(1, min_repeats)):
                return True
    return False


def check_translation(src: str, out: str, code: str, finish: str | None, glossary: dict | None = None,
                      code_mixed: bool = False) -> dict:
    """Return {checks: {...}, failures: [names]}. A missing glossary term is a failure named "glossary_ok";
    callers treat it as a flag, not as a rejected translation (see hard_failures)."""
    ratio = len(out) / max(1, len(src))
    checks = {
        "finish_ok": finish == "stop",
        "nonempty_ok": bool(out.strip()),
        "script_ok": script_ok(out, code, code_mixed),
        "no_repetition_ok": not has_repetition(out),
        "length_ok": LENGTH_RATIO_BOUNDS[0] <= ratio <= LENGTH_RATIO_BOUNDS[1],
        "length_ratio": round(ratio, 2),
    }
    if glossary:
        miss = glossary_mod.missing_terms(src, out, glossary, code)
        checks["glossary_ok"] = not miss
        if miss:
            checks["glossary_missing"] = miss
    failures = [k for k, v in checks.items() if v is False]
    return {"checks": checks, "failures": failures}


def hard_failures(chk: dict) -> list[str]:
    return [f for f in chk["failures"] if f != SOFT]


def retry_missing_terms(src: str, text: str, finish: str | None, chk: dict, code: str, key: str,
                        cache_dir: Path | None, glossary: dict | None, code_mixed: bool = False):
    """One re-translation with the missing glossary renderings written into the English. Returns
    (text, finish, chk, outcome); outcome is None (nothing missing), 'fixed', 'partly fixed', 'not fixed' or
    'no retry' (only Latin renderings were missing, which the English already contains)."""
    missing = chk["checks"].get("glossary_missing") or []
    if not missing:
        return text, finish, chk, None
    want = {t: r for t, r in glossary_mod.renderings_for(glossary, code).items() if t in missing}
    forced = glossary_mod.force_terms(src, want)
    if forced == src:
        return text, finish, chk, "no retry"
    tr = translate_text(forced, code, key, cache_dir, code_mixed=code_mixed)
    if not tr["ok"]:
        return text, finish, chk, "not fixed"
    chk2 = check_translation(src, tr["text"], code, tr.get("finish"), glossary, code_mixed)
    still = chk2["checks"].get("glossary_missing") or []
    if len(still) < len(missing) and set(hard_failures(chk2)) <= set(hard_failures(chk)):
        return tr["text"], tr.get("finish"), chk2, "fixed" if not still else "partly fixed"
    return text, finish, chk, "not fixed"


# --------------------------------------------------------------------------- API
def _prompt(text: str, name: str) -> str:
    return f"Translate the following text into {name}:\n\n{text}"


def translate_text(text: str, code: str, key: str, cache_dir: Path | None = None, code_mixed: bool = False) -> dict:
    """One request. Returns {ok, text, finish, tokens, cached, sec, error?}."""
    if code in PASS_THROUGH:
        lang_name(code)
        return {"ok": True, "text": text.strip(), "finish": "stop", "tokens": 0, "sec": 0.0, "cached": False}
    prompt = _prompt(text, prompt_name(code, code_mixed))
    cache = None
    if cache_dir:
        cache = cache_dir / ("tr_" + hashlib.sha256((MODEL + prompt).encode()).hexdigest()[:24] + ".json")
        if cache.is_file():
            return {**json.loads(cache.read_text(encoding="utf-8")), "cached": True}
    t0 = time.time()
    try:
        r = post_with_retry(URL, key, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]})
    except Exception as e:
        return {"ok": False, "text": "", "cached": False, "sec": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}
    sec = round(time.time() - t0, 2)
    if not r.ok:
        return {"ok": False, "text": "", "cached": False, "sec": sec, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    j = r.json()
    ch = j["choices"][0]
    res = {"ok": True, "text": (ch["message"].get("content") or "").strip(), "finish": ch.get("finish_reason"),
           "tokens": (j.get("usage") or {}).get("completion_tokens"), "sec": sec}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return {**res, "cached": False}


# --------------------------------------------------------------------------- orchestration
def _segment(u: dict, tr: dict, via: str, code: str, glossary: dict | None, code_mixed: bool = False) -> dict:
    out = tr.get("text", "") if tr.get("ok") else ""
    if not tr.get("ok"):
        status, chk = "failed", {"checks": {}, "failures": ["api_error"]}
    else:
        chk = check_translation(u["text"], out, code, tr.get("finish"), glossary, code_mixed)
        status = "flagged" if chk["failures"] else "ok"
    return {"id": u["id"], "sentence_id": u.get("sentence_id"), "start": u["start"], "end": u["end"],
            "timing": u.get("timing"), "inner_pause": u.get("inner_pause", 0.0),
            "src": u["text"], "translation": out, "status": status,
            "failures": chk["failures"], "checks": chk["checks"], "finish_reason": tr.get("finish"),
            "tokens": tr.get("tokens"), "via": via, "cached": tr.get("cached", False), "error": tr.get("error")}


def translate_units(
    units: list[dict],
    code: str,
    work_dir: str | Path,
    mode: str = "batch",
    batch_size: int = 5,
    concurrency: int = 3,
    glossary: dict | None = None,
    out_name: str | None = None,
    code_mixed: bool = False,
) -> list[dict]:
    """Translate all units. A failed unit is recorded, never raised. Writes <work_dir>/<code>/segments.<code>.json."""
    work_dir = Path(work_dir)
    cache_dir = work_dir / "cache"
    lang_name(code)  # validate early
    key = None if code in PASS_THROUGH else get_key("translate")   # English needs no API call, so no key

    def one(u: dict, via: str) -> dict:
        return _segment(u, translate_text(u["text"], code, key, cache_dir, code_mixed=code_mixed), via, code,
                        glossary, code_mixed)

    def fix_terms(s: dict) -> dict:
        if s["status"] == "failed" or not s["checks"].get("glossary_missing"):
            return s
        chk = {"checks": s["checks"], "failures": s["failures"]}
        text, finish, chk, outcome = retry_missing_terms(s["src"], s["translation"], s["finish_reason"], chk,
                                                         code, key, cache_dir, glossary, code_mixed)
        if outcome in ("fixed", "partly fixed"):
            s = {**s, "translation": text, "finish_reason": finish, "checks": chk["checks"],
                 "failures": chk["failures"], "status": "flagged" if chk["failures"] else "ok",
                 "via": s["via"] + "+glossary_retry", "tokens": None}
        return {**s, "glossary_retry": outcome}

    def batch(us: list[dict]) -> list[dict]:
        if len(us) == 1:
            return [one(us[0], "unit")]
        tr = translate_text("\n".join(u["text"] for u in us), code, key, cache_dir, code_mixed=code_mixed)
        lines = [ln.strip() for ln in tr.get("text", "").split("\n") if ln.strip()] if tr.get("ok") else []
        if len(lines) != len(us):
            log.warning("translate: batch of units %s-%s returned %d lines for %d, falling back to one-by-one",
                        us[0]["id"], us[-1]["id"], len(lines), len(us))
            return [one(u, "unit_fallback") for u in us]
        # tokens/finish belong to the whole batch; every line shares the batch finish reason
        return [_segment(u, {**tr, "text": ln, "tokens": None}, "batch", code, glossary, code_mixed)
                for u, ln in zip(us, lines)]

    if mode == "unit":
        groups = [[u] for u in units]
    elif mode == "batch":
        groups = [units[i : i + batch_size] for i in range(0, len(units), batch_size)]
    else:
        raise ValueError("mode must be 'unit' or 'batch'")

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        segs = [s for part in ex.map(batch, groups) for s in part]
        if glossary:
            segs = list(ex.map(fix_terms, segs))

    out_dir = work_dir / code
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / (out_name or f"segments.{code}.json")).write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {k: sum(s["status"] == k for s in segs) for k in ("ok", "flagged", "failed")}
    if glossary:
        counts["glossary_retry"] = {o: sum(s.get("glossary_retry") == o for s in segs)
                                    for o in ("fixed", "partly fixed", "not fixed", "no retry")}
    log.info("translate[%s/%s%s]: %d units %s", code, mode, "/code-mixed" if code_mixed else "", len(segs), counts)
    return segs


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    work, code = Path(sys.argv[1]), sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "batch"
    us = json.loads((work / "units.json").read_text(encoding="utf-8"))
    translate_units(us, code, work, mode=mode)
