"""Audio tools: transcribe_audio, create_subtitles, synthesize_speech, verify_speech."""

from __future__ import annotations

import asyncio
import json
import statistics
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import errors as E
from ..documents import classify
from ..languages import (
    ALL_VOICES,
    ENGLISH_DEFAULT_VOICES,
    KNOWN_UNSUPPORTED,
    LANGUAGE_NAMES,
    SPEAK_LANGUAGES,
    SPEAK_STYLES,
    TRANSCRIBE_LANGUAGES,
    VOICES,
    detect_language,
    normalize_language,
    script_matches_language,
    supported_list,
)
from ..media import ASR_RATE, chunk_audio, concat_wavs, decode_pcm, encode_audio, probe, read_wav, wav_bytes
from ..runtime import Artifact, RunContext, price_speak, price_transcribe, settle
from ..textutil import extract_numbers, letters_only, pack_segments, similarity
from .common import frame_untrusted, output_name, resolve_text
from .subtitles import render_srt
from .translation import Unit, _translate_piece, check_target, verify_units

MAX_AUDIO_SECONDS = 60 * 60
TTS_SEGMENT_CHARS = 220  # "a sentence or two" per Indic-Speak request
SPEECH_PASS, SPEECH_FAIL = 0.72, 0.5


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _asr_language(lang: str | None) -> str | None:
    if not lang or lang == "auto":
        return None
    code = normalize_language(lang)
    if code not in TRANSCRIBE_LANGUAGES:
        name = KNOWN_UNSUPPORTED.get(code, lang)
        raise E.ToolError(E.UNSUPPORTED_LANGUAGE, f"Indic-Transcribe does not support {name}.",
                          hint=f"Supported: {supported_list(TRANSCRIBE_LANGUAGES)}, or 'auto' to detect.")
    return code


async def transcribe_file(ctx: RunContext, path: Path, language: str | None, *, max_segment_seconds: float = 28.0,
                          min_segment_seconds: float = 2.0) -> dict:
    kind = classify(path)
    if kind not in ("audio", "video"):
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"{path.name} is a {kind} file, not audio or video.",
                          hint="Use ocr_document for images/PDFs and read_text for text files.")
    info = await asyncio.to_thread(probe, path)  # ffmpeg work off the event loop, bounded by the run deadline
    if not info.has_audio:
        raise E.ToolError(E.MEDIA_ERROR, f"{path.name} has no audio track.", hint="Nothing to transcribe.")
    if info.duration and info.duration > MAX_AUDIO_SECONDS:
        raise E.ToolError(E.INVALID_ARGUMENT, f"{path.name} is {info.duration / 60:.0f} minutes; the limit is {MAX_AUDIO_SECONDS // 60}.")
    x = await asyncio.to_thread(decode_pcm, path, ASR_RATE)
    total = len(x) / ASR_RATE
    chunks = chunk_audio(x, ASR_RATE, max_seconds=min(max_segment_seconds, 28.0), min_seconds=min_segment_seconds)
    if not chunks:
        return {"text": "", "segments": [], "duration_s": round(total, 2), "detection": None, "notes": ["no speech detected (silence only)"]}
    ctx.emit({"type": "chunking", "tool": "transcribe", "file": path.name, "duration_s": round(total, 2), "chunks": len(chunks),
              "billed_seconds": round(sum(c.duration for c in chunks), 2)})

    lang = _asr_language(language)
    notes: list[str] = []
    texts: list[str | None] = [None] * len(chunks)
    if lang is None:
        first, _ = await ctx.client.transcribe(wav_bytes(chunks[0].samples, ASR_RATE), chunks[0].duration, None, purpose="transcribe-autodetect")
        texts[0] = first["text"].strip()
        guess = detect_language(texts[0])
        if guess.language in TRANSCRIBE_LANGUAGES and guess.confidence >= 0.5:
            lang = guess.language
            notes.append(f"language auto-detected from first segment: {LANGUAGE_NAMES.get(lang, lang)} ({guess.note})")
        else:
            notes.append(f"language not confidently identified from first segment ({guess.note}); model auto-detection used for all segments")

    async def one(i: int) -> None:
        if texts[i] is not None:
            return
        c = chunks[i]
        meta, _ = await ctx.client.transcribe(wav_bytes(c.samples, ASR_RATE), c.duration, lang)
        texts[i] = meta["text"].strip()

    await settle(*[one(i) for i in range(len(chunks))])
    segments = [{"index": i, "start": round(c.start, 2), "end": round(c.end, 2), "text": t or ""} for i, (c, t) in enumerate(zip(chunks, texts))]
    empty = [s["index"] for s in segments if not s["text"]]
    if empty:
        notes.append(f"{len(empty)} segment(s) returned no text (music/noise?): {empty[:10]}")
    text = " ".join(s["text"] for s in segments if s["text"])
    detection = detect_language(text).as_dict() if text else None
    if lang and detection and detection["language"] and detection["language"] != lang:
        notes.append(f"transcript script suggests {detection['language']} although {lang} was used")
    return {"text": text, "segments": segments, "duration_s": round(total, 2), "language_used": lang or "auto",
            "detection": detection, "notes": notes}


# ---------------------------------------------------------------------------
class TranscribeIn(_Strict):
    path: str = Field(description="Audio or video file (wav, mp3, m4a, ogg, flac, mp4, mkv, ...). Any length up to 60 min; it is split into <=28 s pieces at pauses.")
    language: str = Field("auto", description="Spoken language code (e.g. 'hi', 'te') if known; 'auto' detects it from the first segment's script.")
    max_segment_seconds: float = Field(28.0, ge=4.0, le=28.0, description="Longest piece sent to the model. Lower values give finer timestamps.")
    output_name: str | None = Field(None, description="Base file name for the transcript artifacts.")


async def transcribe_audio(ctx: RunContext, a: TranscribeIn) -> dict:
    p = ctx.resolve_input(a.path)
    r = await transcribe_file(ctx, p, a.language, max_segment_seconds=a.max_segment_seconds)
    base = output_name(a.output_name, str(p), "_transcript")
    txt = ctx.artifact_path(base, ".txt")
    txt.write_text(r["text"], encoding="utf-8")
    js = ctx.artifact_path(base, ".json")
    js.write_text(json.dumps({"source": p.name, **r}, ensure_ascii=False, indent=1), encoding="utf-8")
    lang = (r["detection"] or {}).get("language")
    ctx.register(Artifact(txt, "transcript", "transcribe_audio", lang, "indic-transcribe", None, r["notes"], origin=ctx.origin_of(str(p))))
    ctx.register(Artifact(js, "json", "transcribe_audio", lang, "indic-transcribe", None, origin=ctx.origin_of(str(p))))
    ctx.transcripts[str(p)] = js
    if ctx.provenance_guard is not None:
        ctx.provenance_guard.add(r["text"])
    return {"source": str(p), "transcript_path": str(txt), "segments_path": str(js), "duration_s": r["duration_s"],
            "segments": len(r["segments"]), "language_used": r.get("language_used"), "detection": r["detection"], "notes": r["notes"],
            "timing_note": "Segment boundaries are where the audio was cut at pauses; the model returns no word timestamps, so speech inside a segment is not aligned.",
            "transcript": frame_untrusted(r["text"], str(txt), 1500)}


def split_cues(segments: list[dict], max_seconds: float) -> list[dict]:
    """Split segments longer than `max_seconds` at sentence/clause boundaries, giving
    each piece a share of the segment's time proportional to its length."""
    out: list[dict] = []
    for s in segments:
        dur = s["end"] - s["start"]
        text = s.get("text") or ""
        if dur <= max_seconds or len(text.split()) < 2:
            out.append(s)
            continue
        pieces = pack_segments(text, max(8, int(len(text) * max_seconds / dur)))
        total = sum(len(x) for x in pieces) or 1
        t = s["start"]
        for x in pieces:
            d = dur * len(x) / total
            out.append({"start": round(t, 2), "end": round(t + d, 2), "text": x})
            t += d
    return out


# ---------------------------------------------------------------------------
class SubtitlesIn(_Strict):
    path: str = Field(description="Audio/video file, or the segments JSON from transcribe_audio (reuses it instead of re-transcribing).")
    subtitle_language: str = Field("en", description="Language of the subtitles, e.g. 'en'.")
    source_language: str = Field("auto", description="Spoken language code if known, else 'auto'.")
    max_cue_seconds: float = Field(7.0, ge=3.0, le=28.0, description="Target maximum length of one cue. Very sparse speech can give a longer cue; the result notes it.")
    output_name: str | None = Field(None, description="Base file name for the .srt artifacts.")


async def create_subtitles(ctx: RunContext, a: SubtitlesIn) -> dict:
    target = check_target(a.subtitle_language)
    p = ctx.resolve_input(a.path)
    reuse = p if p.suffix.lower() == ".json" else ctx.transcripts.get(str(p))
    if reuse is not None and not (str(reuse) in ctx.artifacts and ctx.artifacts[str(reuse)].producer == "transcribe_audio"):
        # Only transcripts this server produced may be reused; anything else (e.g. JSON written by a planner)
        # would otherwise be labelled as Indic-Transcribe output without any model call.
        raise E.ToolError(E.INVALID_ARGUMENT, f"{p.name} is not a transcript produced by transcribe_audio in this session.",
                          hint="Pass the audio/video file itself, or the *_transcript.json returned by transcribe_audio.")
    if reuse is not None:
        data = json.loads(reuse.read_text(encoding="utf-8"))
        segments, detection = data.get("segments") or [], data.get("detection")
        if not segments:
            raise E.ToolError(E.INVALID_ARGUMENT, f"{reuse.name} has no `segments`.", hint="Pass the *_transcript.json from transcribe_audio, or the audio file.")
        segments = split_cues(segments, a.max_cue_seconds)
        notes = ["reused this run's transcript instead of transcribing the audio again; long segments were split into cues "
                 "with times proportional to text length (approximate)"]
        long_cues = sum(1 for s in segments if s["end"] - s["start"] > a.max_cue_seconds + 0.05)
        if long_cues:
            notes.append(f"{long_cues} cue(s) are longer than {a.max_cue_seconds:g}s because the speech there has too few words to split")
    else:
        r = await transcribe_file(ctx, p, a.source_language, max_segment_seconds=a.max_cue_seconds, min_segment_seconds=1.5)
        segments, detection, notes = r["segments"], r["detection"], r["notes"]
    segments = [s for s in segments if s.get("text")]
    if not segments:
        raise E.ToolError(E.EMPTY_RESULT, "No speech was recognised, so there is nothing to subtitle.")
    src_lang = (detection or {}).get("language")
    if ctx.provenance_guard is not None:
        ctx.provenance_guard.add(" ".join(s["text"] for s in segments))
    base = output_name(a.output_name, str(p), "")
    src_srt = ctx.artifact_path(f"{base}_{src_lang or 'source'}", ".srt")
    src_srt.write_text(render_srt(segments), encoding="utf-8")
    ctx.register(Artifact(src_srt, "transcript", "create_subtitles", src_lang, "indic-transcribe", None, origin=ctx.origin_of(str(p))))

    verification = None
    if src_lang == target:
        out_cues, out_path = segments, src_srt
        notes.append("speech is already in the subtitle language; no translation")
        verification = {"status": "pass", "method": "same-language subtitles: transcript cues, no translation to verify", "problems": [], "warnings": []}
        ctx.artifacts[str(src_srt)].kind = "subtitles"
        ctx.artifacts[str(src_srt)].record_verification(verification)
    else:
        outs = await settle(*[_translate_piece(ctx, s["text"], target, "native") for s in segments])
        # ASR output has no punctuation, so English cues often arrive lower-case; capitalise the first letter (English only).
        fix = (lambda t: t[:1].upper() + t[1:]) if target == "en" else (lambda t: t)
        out_cues = [{**s, "text": fix(o)} for s, (o, _) in zip(segments, outs)]
        units = [Unit("text", s["text"], o) for s, (o, _) in zip(segments, outs)]
        source_all, output_all = "\n".join(u.source for u in units), "\n".join(u.output for u in units)
        verification = await verify_units(ctx, units, source_all, output_all, target, "native", src_lang)
        ctx.emit({"type": "verification", "tool": "create_subtitles", "status": verification["status"],
                  "problems": verification["problems"], "warnings": verification["warnings"]})
        out_path = ctx.artifact_path(f"{base}_{target}", ".srt")
        out_path.write_text(render_srt(out_cues), encoding="utf-8")
        ctx.register(Artifact(out_path, "subtitles", "create_subtitles", target, "indic-transcribe + indic-translate", verification, notes,
                              origin=ctx.origin_of(str(p))))
    sample = render_srt(out_cues[:4])
    return {"subtitles_path": str(out_path), "source_subtitles_path": str(src_srt), "cues": len(out_cues), "source_detection": detection,
            "verification": verification, "notes": notes, "timing_note": "Cue boundaries are pause-based cuts (or proportional splits of a reused transcript); speech inside a cue is not aligned.",
            "first_cues": frame_untrusted(sample, str(out_path), 900)}


# ---------------------------------------------------------------------------
def _resolve_voice(lang: str, voice: str | None, gender: str | None) -> tuple[str, list[str]]:
    notes = []
    if voice:
        match = next((v for v in ALL_VOICES if v.lower() == voice.strip().lower()), None)
        if not match:
            options = VOICES.get(lang, {})
            raise E.ToolError(E.UNSUPPORTED_VOICE, f"Unknown voice {voice!r}. Voices are a fixed library; cloning is not supported.",
                              hint=f"Voices for {lang}: {options or ENGLISH_DEFAULT_VOICES}. Or pass gender='female'/'male'.",
                              details={"voices_for_language": options})
        native, _ = ALL_VOICES[match]
        if native != lang:
            notes.append(f"voice {match} is a {LANGUAGE_NAMES.get(native, native)} voice reading {LANGUAGE_NAMES.get(lang, lang)}")
        return match, notes
    g = (gender or "female").lower()
    if g not in ("female", "male"):
        raise E.ToolError(E.INVALID_ARGUMENT, f"gender must be 'female' or 'male', not {gender!r}.")
    if lang == "en":
        notes.append(f"no native English voice; used {ENGLISH_DEFAULT_VOICES[g]}")
        return ENGLISH_DEFAULT_VOICES[g], notes
    if not gender:
        notes.append("no voice or gender requested; defaulted to a female voice")
    return VOICES[lang][g][0], notes


async def _verify_clip(ctx: RunContext, wav: bytes, expected: str, lang: str) -> dict:
    x, rate = read_wav(wav)
    seconds = len(x) / rate
    asr_lang = lang if lang in TRANSCRIBE_LANGUAGES else None
    if seconds <= 28.0:
        meta, _ = await ctx.client.transcribe(wav, seconds, asr_lang, purpose="verify-back-transcribe")
        heard = meta["text"].strip()
    else:  # slow or looping synthesis can exceed the ASR window: verify it in pieces
        pieces = chunk_audio(x, rate, max_seconds=28.0, trim_silence=False)
        metas = await settle(*[ctx.client.transcribe(wav_bytes(c.samples, rate), c.duration, asr_lang, purpose="verify-back-transcribe")
                               for c in pieces])
        heard = " ".join(m["text"].strip() for m, _ in metas)
    sim = similarity(expected, heard)
    return {"similarity": round(sim, 3), "heard": heard, "seconds": round(seconds, 2),
            "chars_per_second": round(len(letters_only(expected)) / max(seconds, 0.1), 1),
            "number_issue": speech_number_issue(expected, heard)}


def speech_number_issue(expected: str, heard: str) -> str | None:
    """Letter similarity ignores digits, so numbers are compared on their own. Numbers
    the ASR did not return as digits cannot be confirmed (they may be spoken as words or
    missing), so they are a warning, never a silent pass."""
    exp, got = extract_numbers(expected), extract_numbers(heard)
    if not exp and not got:
        return None
    if exp and not got:
        return f"numbers {exp[:8]} in the text are not confirmed by back-transcription (missing, or transcribed as words)"
    if sorted(exp) != sorted(got):
        return f"numbers heard {got[:8]} differ from the text {exp[:8]}"
    return None


class SpeakIn(_Strict):
    language: str = Field(description="Language the text is written in and should be spoken in, e.g. 'bn'. The text must already be in this language (translate first).")
    path: str | None = Field(None, description="Text/Markdown file or artifact to read aloud (preferred).")
    text: str | None = Field(None, description="Short inline text to read aloud.")
    voice: str | None = Field(None, description="Voice name from list_voices (case-insensitive), e.g. 'Ishita'. Overrides gender.")
    gender: Literal["female", "male"] | None = Field(None, description="Pick the language's default female or male voice.")
    style: Literal[tuple(sorted(SPEAK_STYLES))] | None = Field(None, description="Optional delivery style from the fixed list; omit for the voice's neutral delivery.")  # type: ignore[valid-type]
    format: Literal["mp3", "wav"] = Field("mp3", description="Output audio format.")
    verify: bool = Field(True, description="Back-transcribe every segment with Indic-Transcribe and re-synthesise failures (recommended).")
    target_duration_seconds: float | None = Field(None, gt=0, description="If the task asks for a length (e.g. 120 for two minutes), report how close the audio is.")
    output_name: str | None = Field(None, description="File name for the audio artifact.")

    @field_validator("style", mode="before")
    @classmethod
    def _canonical_style(cls, v):  # accept 'News' or 'children’s stories' spelled with any case
        if isinstance(v, str):
            return next((s for s in SPEAK_STYLES if s.lower() == v.strip().lower().replace("’", "'")), v)
        return v


async def synthesize_speech(ctx: RunContext, a: SpeakIn) -> dict:
    lang = normalize_language(a.language)
    if lang not in SPEAK_LANGUAGES:
        name = KNOWN_UNSUPPORTED.get(lang, a.language)
        raise E.ToolError(E.UNSUPPORTED_LANGUAGE, f"Indic-Speak cannot speak {name}.",
                          hint=f"Speech languages: {supported_list(SPEAK_LANGUAGES)}. Report the task as unsupported rather than substituting.")
    text, src_label = resolve_text(ctx, a.text, a.path)
    text = text.replace("#", " ").replace("*", " ").replace("|", " ")  # do not read Markdown syntax aloud
    share = script_matches_language(text, lang)
    if letters_only(text) and share < 0.6:
        found = detect_language(text)
        raise E.ToolError(E.INVALID_ARGUMENT,
                          f"The text looks like {LANGUAGE_NAMES.get(found.language or '', found.script or 'another language')}, "
                          f"not {LANGUAGE_NAMES.get(lang, lang)} ({share:.0%} of letters in the expected script).",
                          hint=f"Translate it first with translate_text(target_language='{lang}') and pass that artifact's path.")
    voice, notes = _resolve_voice(lang, a.voice, a.gender)
    style = a.style
    segments = pack_segments(text, TTS_SEGMENT_CHARS)
    if not segments:
        raise E.ToolError(E.EMPTY_RESULT, "There is no text to speak.")
    total_chars = sum(len(s) for s in segments)
    # Budget only what will actually be sent: segments already synthesised (cache) cost nothing.
    new_chars = sum(len(s) for s in set(segments) if not ctx.client.speak_cached(s, voice, lang, style))
    est = price_speak(new_chars) + (price_transcribe(new_chars / 8) if a.verify else 0)
    if not ctx.ledger.can_afford(est):
        raise E.ToolError(E.BUDGET_EXCEEDED, f"Speaking {total_chars} characters (+ verification) needs about ₹{est:.3f}; "
                          f"only ₹{max(ctx.ledger.remaining or 0, 0):.3f} remains.",
                          hint="Shorten the text (e.g. summarise in fewer words before translating) or stop and report.",
                          details={"needed_inr": round(est, 4), "remaining_inr": round(max(ctx.ledger.remaining or 0, 0), 4)})

    async def synth(seg: str, v: str) -> bytes:
        data, info = await ctx.client.speak(seg, v, lang, style)
        if info.get("format") != "wav":
            tmp = ctx.artifact_path("_tmp_tts", f".{info.get('format', 'bin')}")
            tmp.write_bytes(data)
            try:
                data = wav_bytes(decode_pcm(tmp, 24000), 24000)
            finally:
                tmp.unlink(missing_ok=True)
        return data

    clips = list(await settle(*[synth(s, voice) for s in segments]))
    seg_reports: list[dict] = [{"index": i, "chars": len(s)} for i, s in enumerate(segments)]
    verification = None
    if a.verify:
        checks = await settle(*[_verify_clip(ctx, c, s, lang) for c, s in zip(clips, segments)])
        for rep, chk in zip(seg_reports, checks):
            rep.update(chk)
        cps = [c["chars_per_second"] for c in checks]
        med = statistics.median(cps)
        retried = 0
        for i, (rep, seg) in enumerate(zip(seg_reports, segments)):
            odd_rate = len(segments) > 2 and (rep["chars_per_second"] < 0.45 * med or rep["chars_per_second"] > 2.2 * med)
            if rep["similarity"] >= SPEECH_FAIL and not odd_rate:
                continue
            # One repair attempt with a *different* input (split text or alternate voice); never resend the same request.
            pieces = pack_segments(seg, max(60, len(seg) // 2 + 1))
            alt_voices = [v for v in VOICES.get(lang, {}).get(ALL_VOICES.get(voice, ("", "female"))[1], []) if v != voice]
            ctx.emit({"type": "recovery", "action": "resynthesise_segment", "segment": i, "similarity": rep["similarity"],
                      "strategy": "split" if len(pieces) > 1 else ("alternate_voice" if alt_voices else "none")})
            if len(pieces) > 1:
                parts = [await synth(p, voice) for p in pieces]
                new_clip, _ = concat_wavs(parts, gap_ms=120)
            elif alt_voices:
                new_clip = await synth(seg, alt_voices[0])
            else:
                rep["repair"] = "no alternative input available"
                continue
            chk = await _verify_clip(ctx, new_clip, seg, lang)
            retried += 1
            rep["repair"] = {"first_similarity": rep["similarity"], **chk}
            if chk["similarity"] > rep["similarity"]:
                clips[i] = new_clip
                rep.update(chk)
        sims = [r["similarity"] for r in seg_reports]
        failed = [r["index"] for r in seg_reports if r["similarity"] < SPEECH_FAIL]
        weak = [r["index"] for r in seg_reports if SPEECH_FAIL <= r["similarity"] < SPEECH_PASS]
        numbers_off = [r["index"] for r in seg_reports if r.get("number_issue")]
        problems = [f"segments {failed} did not match the text when back-transcribed (possible omission/repetition)"] if failed else []
        warnings = [f"segments {weak} matched only partly (similarity {SPEECH_FAIL}-{SPEECH_PASS})"] if weak else []
        if numbers_off:
            warnings.append(f"segments {numbers_off}: " + "; ".join(r["number_issue"] for r in seg_reports if r.get("number_issue"))[:400])
        verification = {"status": "fail" if failed else ("warn" if weak or numbers_off else "pass"),
                        "method": "back-transcription of every segment with Indic-Transcribe; letter-level similarity; speaking-rate outliers",
                        "mean_similarity": round(sum(sims) / len(sims), 3), "min_similarity": round(min(sims), 3),
                        "segments_repaired": retried, "problems": problems, "warnings": warnings}

    audio, duration = concat_wavs(clips)
    if verification and a.target_duration_seconds:
        dev = (duration - a.target_duration_seconds) / a.target_duration_seconds
        verification["duration"] = {"target_s": a.target_duration_seconds, "actual_s": round(duration, 1), "deviation": f"{dev:+.0%}"}
        if abs(dev) > 0.25:
            verification["warnings"].append(f"duration {duration:.0f}s is {dev:+.0%} from the requested {a.target_duration_seconds:.0f}s")
            if verification["status"] == "pass":
                verification["status"] = "warn"
    if verification:
        ctx.emit({"type": "verification", "tool": "synthesize_speech", "status": verification["status"],
                  "problems": verification["problems"], "warnings": verification["warnings"]})
    label = f"AI-generated speech (Bodhan Indic-Speak, voice {voice}, {LANGUAGE_NAMES.get(lang, lang)})"
    dest = ctx.artifact_path(output_name(a.output_name, src_label, f"_{lang}_speech"), f".{a.format}")
    encode_audio(audio, dest, a.format, title=dest.stem, comment=label)
    ctx.register(Artifact(dest, "audio", "synthesize_speech", lang, f"indic-speak ({voice})", verification, notes + [label],
                          depends_on=[src_label] if src_label in ctx.artifacts else [], origin=ctx.origin_of(src_label)))
    return {"path": str(dest), "duration_s": round(duration, 1), "language": lang, "voice": voice, "style": style, "segments": len(segments),
            "characters": total_chars, "label": label, "notes": notes, "verification": verification,
            "segment_report": [{k: v for k, v in r.items() if k != "heard"} for r in seg_reports][:40]}


# ---------------------------------------------------------------------------
class VerifySpeechIn(_Strict):
    audio_path: str = Field(description="Generated audio file to check.")
    language: str = Field(description="Language the audio should be in.")
    expected_path: str | None = Field(None, description="Text file with what the audio should say.")
    expected_text: str | None = Field(None, description="Inline expected text (short only).")


async def verify_speech(ctx: RunContext, a: VerifySpeechIn) -> dict:
    p = ctx.resolve_input(a.audio_path)
    expected, _ = resolve_text(ctx, a.expected_text, a.expected_path, "expected_text")
    r = await transcribe_file(ctx, p, a.language)
    sim = similarity(expected, r["text"])
    status = "pass" if sim >= SPEECH_PASS else ("warn" if sim >= SPEECH_FAIL else "fail")
    warnings = []
    issue = speech_number_issue(expected, r["text"])
    if issue:
        warnings.append(issue)
        status = "warn" if status == "pass" else status
    verification = {"status": status, "method": "back-transcription with Indic-Transcribe; letter-level similarity; digit comparison",
                    "similarity": round(sim, 3), "duration_s": r["duration_s"],
                    "problems": [] if status != "fail" else ["audio does not match the expected text"], "warnings": warnings}
    art = ctx.artifacts.get(str(p))
    if art is not None:
        verification = {**verification, "effective_status": art.record_verification(verification)["status"]}  # never upgrades history
    return {"audio_path": str(p), "verification": verification, "heard": frame_untrusted(r["text"], str(p), 800)}
