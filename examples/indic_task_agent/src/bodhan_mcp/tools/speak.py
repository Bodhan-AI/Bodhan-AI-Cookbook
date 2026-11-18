"""speak_text: one audio file out, however long the text is."""
from __future__ import annotations

import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import api, cache, catalog, config, costs, media
from ..core import text as txt
from ..core.errors import BodhanError
from ..core.schemas import Speech


def speak_text(
    text_path: str,
    language: str,
    voice: str | None = None,
    gender: str | None = None,
    style: str | None = None,
    format: str = "wav",
    out_dir: str | None = None,
    max_cost_inr: float | None = None,
    force: bool = False,
) -> Speech:
    """Speak a UTF-8 text file as a single audio file in one of Bodhan's 23 synthesis languages. Voice is optional: pass a name, or `gender` (female/male) to pick one, or nothing for the default for the language. Any voice can read any language; English uses any voice. Long text is split into sentence groups and synthesised clip by clip, then joined sample-accurately, so any length works in one call. Style is one of a fixed list (e.g. news, "children's stories", "educational lecture") and is preview-quality. The model does not clone voices. Output is labelled AI-generated (Indic-Speak)."""
    src = Path(text_path)
    if not src.is_file():
        raise BodhanError("MISSING_FILE", f"{text_path} does not exist.", hint="Pass a path to an existing UTF-8 text file.")
    if language not in catalog.TTS_LANGUAGES:
        raise catalog.unsupported_language(language, "speech synthesis", catalog.TTS_LANGUAGES)
    if format not in ("wav", "mp3"):
        raise BodhanError("UNSUPPORTED_FORMAT", f"format must be wav or mp3, not '{format}'.")
    if style and style not in catalog.STYLES:
        raise BodhanError("UNSUPPORTED_STYLE", f"'{style}' is not a Bodhan speaking style.", hint="Valid: " + ", ".join(catalog.STYLES))
    chosen = catalog.resolve_voice(language, voice, gender)
    content = src.read_text(encoding="utf-8").strip()
    if not content:
        raise BodhanError("EMPTY_INPUT", f"{src.name} is empty.", hint="Nothing to speak.")
    out = Path(out_dir) if out_dir else config.default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    budget = costs.CallBudget(max_cost_inr)
    budget.reserve(costs.tts_cost(len(content)), f"Synthesising {len(content)} characters")

    clips = txt.pack_sentences(content, config.TTS_MAX_CLIP_CHARS)
    warnings: list[str] = []
    hits = 0
    wavs: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="bodhan-tts-") as tmp:
        tmpdir = Path(tmp)
        with ThreadPoolExecutor(max_workers=config.max_concurrency()) as pool:  # the client's semaphore enforces the real cap
            first = list(pool.map(lambda item: _clip(item[1], language, chosen, style, tmpdir, item[0], force), enumerate(clips)))
        for i, (clip, (wav, hit)) in enumerate(zip(clips, first)):
            hits += hit
            expected = len(clip) / config.TTS_CHARS_PER_S
            got = media.wav_duration(str(wav))
            if expected > 1.5 and not (0.65 * expected <= got <= 1.35 * expected):
                warnings.append(f"duration_off_target_clip_{i}")
                parts = txt.pack_sentences(clip, max(config.TTS_MAX_CLIP_CHARS // 2, 80))
                if len(parts) > 1:
                    split_paths = []
                    for j, part in enumerate(parts):
                        sub, _ = _clip(part, language, chosen, style, tmpdir, i * 100 + j, force)
                        split_paths.append(sub)
                    wav = tmpdir / f"rejoined_{i}.wav"
                    media.concat_wavs(split_paths, str(wav))
            wavs.append(wav)
        joined = tmpdir / "joined.wav"
        media.concat_wavs(wavs, str(joined))
        final = out / f"{src.stem}.{language}.{format}"
        if format == "mp3":
            media.encode_mp3(str(joined), str(final))
        else:
            final.write_bytes(joined.read_bytes())
        duration = media.ffprobe_duration(str(final))
    spent = costs.tts_cost(len(content) * (len(clips) - hits) / max(len(clips), 1))
    budget.add(spent)
    return Speech(
        audio_path=str(final.resolve()),
        duration_s=round(duration, 3),
        chars=len(content),
        clips=len(clips),
        voice=chosen,
        language=language,
        format=format,
        cost_inr=round(spent, 4),
        warnings=warnings,
    )


def _clip(clip: str, language: str, voice: str, style: str | None, tmpdir: Path, index: int, force: bool) -> tuple[Path, int]:
    """Synthesise one clip and decode whatever container comes back to 24 kHz mono WAV."""
    key = cache.digest("tts", language, voice, style, clip)
    target = tmpdir / f"clip_{index}.wav"
    cached = None if force else cache.get_bytes(key)
    if cached:
        target.write_bytes(cached)
        return target, 1
    blob = api.speak(clip, language, voice, style)
    raw = tmpdir / f"clip_{index}.raw"
    raw.write_bytes(blob)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(target)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        raise BodhanError("UNSUPPORTED_FORMAT", "The speech response could not be decoded as audio.") from None
    if not force:
        cache.put_bytes(key, target.read_bytes())
    return target, 0
