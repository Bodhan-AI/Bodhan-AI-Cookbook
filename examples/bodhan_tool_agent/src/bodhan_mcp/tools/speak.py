"""synthesize_speech: any-length text -> one MP3 via Indic-Speak, verified by back-transcription.

Indic-Speak takes "a sentence or two" per request and does not split long text, so the text is
packed into pieces of about 250 characters at sentence boundaries, synthesised in parallel, and
joined with short pauses. With verify=True each piece is transcribed back with Indic-Transcribe and
compared with its source text. A piece that looks repeated, cut short or wrong is synthesised once
more (attempt=1, an explicit, traced retry), and the better take is kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re

from rapidfuzz import fuzz

from bodhan_core import audio, chunking, langs, workspace
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_core.ledger import cost_speak
from bodhan_mcp.runtime import get_client

PIECE_CHARS = 250
GAP_MS = 300
MIN_SIMILARITY = 60.0  # rapidfuzz ratio, 0-100, on normalised text (TTS reads digits as words, so not 100)
LENGTH_RATIO = (0.55, 1.7)  # back-transcript chars / source chars; outside means repeat or omission
AI_LABEL = "AI-generated speech (Bodhan Indic-Speak)"


def styles() -> list[str]:
    return list(langs.model_data()["models"]["indic-speak"].get("styles", []))


def pick_voice(lang: str, voice: str | None, gender: str | None) -> tuple[str, str]:
    """Return (voice, how it was chosen). Voice names are matched case-insensitively."""
    if voice:
        known = {v["voice"].lower(): v for v in langs.voices()}
        match = known.get(voice.strip().lower())
        if not match:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"Unknown voice {voice!r}.",
                            hint="Call list_voices(language=...) and pass one of the names it returns.")
        return match["voice"], "given"
    if gender and gender not in {"female", "male"}:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "gender must be 'female' or 'male'.")
    options = langs.voices(lang, gender)
    if not options and gender:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"No {gender} voice for {lang}.",
                        hint=f"Available: {[v['voice'] + ' (' + v['gender'] + ')' for v in langs.voices(lang)]}")
    if not options:  # e.g. English has no dedicated voice; any voice can read any language
        options = langs.voices("hi", gender)
    return options[0]["voice"], f"default {options[0]['gender']} voice for {lang}"


def markdown_to_speech(text: str) -> str:
    """Readable prose from Markdown: drop markup, code and comments; read table rows as comma lists."""
    lines = []
    for b in chunking.parse_markdown(text):
        if b.translatable:
            lines.append(re.sub(r"[*_`#>]+", "", b.text).strip())
        elif b.kind == "table":
            lines.extend(", ".join(c for c in row if c) for row in b.rows)
    spoken = "\n".join(x for x in lines if x)
    spoken = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", spoken)  # links -> their text
    return spoken


def _normalise(text: str) -> str:
    return re.sub(r"[\W_]+", " ", chunking.normalize_digits(text).lower()).strip()


def score(source: str, heard: str) -> dict:
    a, b = _normalise(source), _normalise(heard)
    similarity = fuzz.ratio(a, b) if a and b else 0.0
    ratio = len(b) / max(len(a), 1)
    ok = similarity >= MIN_SIMILARITY and LENGTH_RATIO[0] <= ratio <= LENGTH_RATIO[1]
    return {"similarity": round(similarity, 1), "length_ratio": round(ratio, 2), "passed": ok}


async def _take(client, text: str, voice: str, lang: str, style: str | None, attempt: int, verify: bool) -> dict:
    r = await client.speak(text, voice=voice, lang=lang, style=style, attempt=attempt)
    if not r.content or r.content[:4] != b"RIFF":
        raise ToolError(ErrorCode.UPSTREAM_ERROR, "Indic-Speak did not return WAV audio.", retryable=True)
    take = {"wav": r.content, "duration_s": round(audio.wav_duration(r.content), 2), "cost_inr": r.cost_inr,
            "cached": r.cached, "attempt": attempt}
    if verify:
        t = await client.transcribe(r.content, filename="verify.wav", duration_s=take["duration_s"], language=lang)
        heard = ((t.data or {}).get("text") or "").strip()
        take.update({"heard": heard, **score(text, heard)})
        take["cost_inr"] += t.cost_inr
    return take


async def _piece(client, text: str, voice: str, lang: str, style: str | None, verify: bool) -> dict:
    first = await _take(client, text, voice, lang, style, 0, verify)
    if not verify or first["passed"]:
        return {**first, "retried": False}
    second = await _take(client, text, voice, lang, style, 1, verify)
    best = second if (second["passed"], second["similarity"]) > (first["passed"], first["similarity"]) else first
    return {**best, "retried": True, "cost_inr": first["cost_inr"] + second["cost_inr"]}


async def synthesize_speech(
    language: str,
    text: str | None = None,
    path: str | None = None,
    voice: str | None = None,
    gender: str | None = None,
    style: str | None = None,
    verify: bool = True,
    max_chars: int = 5000,
    out_dir: str | None = None,
) -> dict:
    lang = langs.require_supported("indic-speak", language)
    if (text is None) == (path is None):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "Pass exactly one of `text` or `path`.")
    if path is not None:
        p = workspace.resolve_input(path)
        if workspace.media_kind(p) != "text":
            raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, "synthesize_speech reads text files only.",
                            hint="Pass a .txt/.md file, e.g. the output_path from translate_text.")
        raw, source_id = workspace.read_text_file(p), str(p)
    else:
        raw, source_id = text, "text-" + hashlib.sha256(text.encode()).hexdigest()[:10]
    spoken = markdown_to_speech(raw).strip()
    if not spoken:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "No speakable text.")
    if style is not None and style not in styles():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"Unknown style {style!r}.", hint=f"Styles: {styles()}")

    script = langs.detect_script(spoken)
    if script["script"] and lang not in script["candidates"] and script["share"] > 0.6:
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Text is in {script['script']} script but language={lang}: it would be read in the wrong language.",
            hint=f"Translate it first with translate_text(target_language='{lang}') and pass that output.",
        )
    if len(spoken) > max_chars:
        raise ToolError(ErrorCode.INVALID_ARGUMENT,
                        f"{len(spoken)} chars exceeds max_chars={max_chars} (≈₹{cost_speak(len(spoken)):.2f}).",
                        hint="Shorten the text, or raise max_chars deliberately; speech costs ₹6 per 10,000 chars.")

    chosen, voice_note = pick_voice(lang, voice, gender)
    pieces = chunking.pack_sentences(spoken, PIECE_CHARS)
    client = get_client()
    takes = await asyncio.gather(*(_piece(client, t, chosen, lang, style, verify) for t in pieces))

    out = workspace.output_dir(out_dir)
    key = f"{lang}|{chosen}|{style}|{verify}"
    wav_path = workspace.artifact_path(out / ".work", source_id, ".wav", key=key)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(audio.concat_wavs([t["wav"] for t in takes], gap_ms=GAP_MS))
    mp3_path = workspace.artifact_path(out, source_id, f".{lang}.mp3", key=key)
    await asyncio.to_thread(audio.wav_to_mp3, wav_path, mp3_path, title=f"{lang} speech ({chosen})",
                            comment=f"{AI_LABEL}, voice {chosen}")
    duration = audio.probe(mp3_path)["duration_s"]

    checks = ("heard", "similarity", "length_ratio", "passed") if verify else ()
    manifest = [{"piece": i + 1, "text": t_text, "duration_s": t["duration_s"], "attempt": t["attempt"],
                 "retried": t["retried"], **{k: t[k] for k in checks}}
                for i, (t_text, t) in enumerate(zip(pieces, takes, strict=True))]
    manifest_path = mp3_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps({"label": AI_LABEL, "voice": chosen, "language": lang, "style": style,
                                         "pieces": manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [m["piece"] for m in manifest if verify and not m["passed"]]
    return {
        "ok": True,
        "audio_path": str(mp3_path),
        "manifest_path": str(manifest_path),
        "label": AI_LABEL,
        "language": lang,
        "voice": chosen,
        "voice_choice": voice_note,
        "style": style,
        "duration_s": duration,
        "chars": len(spoken),
        "pieces": len(pieces),
        "verification": ({"checked": True, "passed": not failed, "failed_pieces": failed,
                          "retried_pieces": [m["piece"] for m in manifest if m["retried"]],
                          "min_similarity": min(m["similarity"] for m in manifest)}
                         if verify else {"checked": False}),
        "cost_inr": round(sum(t["cost_inr"] for t in takes), 4),
        "cached_pieces": sum(t["cached"] for t in takes),
    }
