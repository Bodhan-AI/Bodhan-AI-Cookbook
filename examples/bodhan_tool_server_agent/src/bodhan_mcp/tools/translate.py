"""translate_text (chunked, truncation-safe, markdown-preserving) and translate_segments."""
from __future__ import annotations

import asyncio
import json
import re

from .. import core
from ..core import PRICES, ToolError
from ..languages import TRANSLATE, detect_language, norm

CHUNK_CHARS = 1200  # conservative; tune after measuring the real context window
MIN_CHARS = 40


def split_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on sentence boundaries into pieces <= max_chars (hard split if a sentence is longer)."""
    if len(text) <= max_chars:
        return [text]
    sents = [s for s in re.split(r"(?<=[।.?!॥])\s+", text) if s]
    out, cur = [], ""
    for s in sents:
        while len(s) > max_chars:  # one monster sentence
            cut = s.rfind(" ", 0, max_chars) or max_chars
            cut = cut if cut > 0 else max_chars
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut])
            s = s[cut:].lstrip()
        if cur and len(cur) + 1 + len(s) > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def validate_target(lang: str) -> str:
    code = norm(lang)
    if code not in TRANSLATE:
        extra = " (Bhojpuri is not supported by indic-translate)" if code in ("bho", "bhb") else ""
        raise ToolError("UNSUPPORTED_LANGUAGE", f"'{lang}' is not a supported translation target{extra}",
                        f"Supported: {', '.join(sorted(TRANSLATE))}. Do not substitute another system.")
    return code


async def _one(text: str, target: str, script: str, stats: dict) -> str:
    """Translate one piece; re-split if the API says truncated."""
    key = core.key_of("translate", target, script, text)
    cached = core.cache_get(key)
    if cached is not None:
        stats["cached"] += 1
        return json.loads(cached)["t"]
    est = max(1, int(len(text) * 1.5)) / 10000 * PRICES["translate_per_10k_tokens"]
    core.reserve(est)
    try:
        # The documented POST /translate returned 404 on the live host; the chat route gives the same output.
        body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], "target_language": target}
        if script != "native":
            body["script"] = script
        r = await core.post("translate", "/v1/chat/completions", json=body)
        j = r.json()
        ch = j["choices"][0]
        toks = (j.get("usage") or {}).get("completion_tokens") or int(len(text) * 1.5)
        cost = toks / 10000 * PRICES["translate_per_10k_tokens"]
        core.record("translate", cost, target)
    finally:
        core.release(est)
    stats["cost"] += cost
    if ch.get("finish_reason") == "length" or j.get("truncated"):
        stats["resplit"] += 1
        if len(text) > MIN_CHARS:
            parts = split_text(text, max(MIN_CHARS, len(text) // 2))
            if len(parts) == 1:  # no sentence boundary: cut at a space near the middle
                mid = text.rfind(" ", 0, len(text) // 2) or len(text) // 2
                parts = [text[:mid], text[mid:].lstrip()]
            res = await asyncio.gather(*[_one(p, target, script, stats) for p in parts])
            out = " ".join(res)
            core.cache_put(key, json.dumps({"t": out}).encode())
            return out
        stats["warnings"].append(f"output still truncated for a {len(text)}-char piece")
    out = ch["message"]["content"] or ""
    core.cache_put(key, json.dumps({"t": out}).encode())
    return out


async def _long(text: str, target: str, script: str, stats: dict) -> str:
    if not text.strip():
        return text
    res = await asyncio.gather(*[_one(p, target, script, stats) for p in split_text(text.strip())])
    return " ".join(res)


async def _same(x: str) -> str:
    return x


async def _plain(text: str, target: str, script: str, stats: dict) -> str:
    """Keep lines and blank lines. Consecutive lines go in one request (the API preserves newlines, measured);
    if a batch comes back with a different line count, fall back to one request per line."""
    batches, cur, size = [], [], 0
    for ln in text.split("\n"):
        if cur and size + len(ln) + 1 > CHUNK_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        batches.append(cur)

    async def per_line(lines: list[str]) -> list[str]:
        return list(await asyncio.gather(*[_long(x, target, script, stats) if x.strip() else _same(x) for x in lines]))

    async def one(batch: list[str]) -> list[str]:
        lead = next((i for i, x in enumerate(batch) if x.strip()), len(batch))
        if lead == len(batch):
            return batch
        trail = next(i for i, x in enumerate(reversed(batch)) if x.strip())
        core_lines = batch[lead:len(batch) - trail]
        if len(core_lines) == 1:
            got = [await _long(core_lines[0], target, script, stats)]
        else:
            got = (await _one("\n".join(core_lines), target, script, stats)).split("\n")
            if len(got) != len(core_lines):
                stats["warnings"].append("a batch lost line breaks; retranslated line by line")
                got = await per_line(core_lines)
        return batch[:lead] + got + batch[len(batch) - trail:]

    res = await asyncio.gather(*[one(bt) for bt in batches])
    return "\n".join(x for bt in res for x in bt)


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


async def _markdown(text: str, target: str, script: str, stats: dict) -> str:
    """Translate text spans only; keep headings markers, list markers, table pipes, fences, comments."""
    lines = text.split("\n")
    jobs: dict[str, str] = {}  # unique source -> filled later
    plan: list = []
    fence = False

    def want(s: str) -> str:
        s = s.strip()
        if s and re.search(r"[^\W\d_]", s):  # has letters; skip pure numbers/symbols
            jobs.setdefault(s, "")
        return s

    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
            plan.append(("raw", ln))
        elif fence or not ln.strip() or ln.strip().startswith("<!--") or _TABLE_SEP.match(ln):
            plan.append(("raw", ln))
        elif ln.lstrip().startswith("|"):
            cells = ln.strip().strip("|").split("|")
            plan.append(("table", ln[:len(ln) - len(ln.lstrip())], [want(c) for c in cells]))
        else:
            m = re.match(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)*)(.*)$", ln)
            plan.append(("line", m.group(1), want(m.group(2))))
    keys = list(jobs)
    res = await asyncio.gather(*[_long(k, target, script, stats) for k in keys])
    jobs = dict(zip(keys, res))
    out = []
    for p in plan:
        if p[0] == "raw":
            out.append(p[1])
        elif p[0] == "table":
            out.append(p[1] + "| " + " | ".join(jobs.get(c, c) for c in p[2]) + " |")
        else:
            out.append(p[1] + jobs.get(p[2], p[2]))
    return "\n".join(out)


def _structure(md: str) -> tuple:
    rows = [l for l in md.split("\n") if l.lstrip().startswith("|")]
    return (len(rows), sum(l.count("|") for l in rows), sum(1 for l in md.split("\n") if l.lstrip().startswith("#")))


async def translate_text(text: str | None = None, text_ref: str | None = None, target_language: str = "en",
                         source_language: str | None = None, script: str = "native", mode: str = "plain",
                         out_name: str | None = None) -> dict:
    if bool(text) == bool(text_ref):
        raise ToolError("INVALID_ARGUMENT", "provide exactly one of text or text_ref")
    target = validate_target(target_language)
    if script not in ("native", "roman", "codemix"):
        raise ToolError("INVALID_ARGUMENT", "script must be native, roman or codemix")
    if mode not in ("plain", "markdown"):
        raise ToolError("INVALID_ARGUMENT", "mode must be plain or markdown")
    src = text if text else core.read_text(core.resolve_in(text_ref))
    if not src.strip():
        raise ToolError("EMPTY_INPUT", "nothing to translate")
    det = detect_language(src)
    stats = {"cost": 0.0, "cached": 0, "resplit": 0, "warnings": []}
    same = (source_language and norm(source_language) == target) or (det["language"] == target and det["confidence"] >= 0.8)
    if same:
        out = src
        stats["warnings"].append(f"source already looks like '{target}'; returned unchanged (no API call)")
    else:
        core.check_budget(max(1, int(len(src) * 1.5)) / 10000 * PRICES["translate_per_10k_tokens"])
        out = await (_markdown if mode == "markdown" else _plain)(src, target, script, stats)
        if mode == "markdown" and _structure(src) != _structure(out):
            stats["warnings"].append(f"markdown structure changed: {_structure(src)} -> {_structure(out)}")
    name = out_name or f"translation_{core.key_of(src, target)[:10]}.{'md' if mode == 'markdown' else 'txt'}"
    p = core.write_text(name, out)
    return {"translation_ref": str(p), "preview": core.preview(out),
            "translation": out if len(out) <= 400 else None, "target_language": target, "script": script,
            "source_language_detected": det["language"], "chars_in": len(src), "chars_out": len(out),
            "resplit_pieces": stats["resplit"], "cached_calls": stats["cached"],
            "cost_inr": round(stats["cost"], 4), "warnings": stats["warnings"]}


async def translate_segments(segments_ref: str, target_language: str, out_name: str | None = None) -> dict:
    """Translate each subtitle cue separately (keeps count and timing)."""
    target = validate_target(target_language)
    segs = json.loads(core.read_text(core.resolve_in(segments_ref)))
    stats = {"cost": 0.0, "cached": 0, "resplit": 0, "warnings": []}
    uniq = list(dict.fromkeys(s["text"] for s in segs))  # identical cue texts are translated once
    done = dict(zip(uniq, await asyncio.gather(*[_long(t, target, "native", stats) for t in uniq])))
    new = [{**s, "text": done[s["text"]]} for s in segs]
    p = core.write_text(out_name or f"segments_{target}_{core.key_of(segments_ref)[:8]}.json",
                        json.dumps(new, ensure_ascii=False, indent=1))
    return {"segments_ref": str(p), "cues": len(new), "target_language": target,
            "cost_inr": round(stats["cost"], 4), "warnings": stats["warnings"]}
