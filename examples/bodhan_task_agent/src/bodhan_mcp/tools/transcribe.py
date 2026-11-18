from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import ClassVar

from bodhan_mcp.core.base_tool import BaseTool
from bodhan_mcp.core.languages import LANGUAGE_SCRIPTS, TRANSCRIBE_LANGUAGES
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.schemas.transcribe import (
    INLINE_SEGMENT_LIMIT,
    LanguageSource,
    TranscribeInput,
    TranscribeOutput,
    TranscriptSegment,
)
from bodhan_mcp.utils.audio import (
    check_audio_extension,
    detect_silences,
    extract_chunks,
    plan_chunks,
    probe_duration,
    require_binaries,
)
from bodhan_mcp.utils.files import resolve_input_file, resolve_output_file, write_pair_atomic
from bodhan_mcp.utils.script import dominant_script, language_from_script
from bodhan_mcp.utils.text import preview_text

SILENCE_EDGE_TOLERANCE_S = 0.2
DEADLINE_GRACE_S = 1.0
MAX_NAME_ATTEMPTS = 100
SEGMENTS_SUFFIX = ".segments.json"
MAX_LISTED_WARNINGS = 8
_PHRASE_LOOP_RE = re.compile(r"(?:^|\s)(\S+(?:\s+\S+){0,3}?)(?:\s+\1){6,}(?=\s|$)")
_CHAR_RUN_RE = re.compile(r"(\S)\1{19,}")


def is_repetitive(text: str) -> bool:
    return bool(_PHRASE_LOOP_RE.search(text) or _CHAR_RUN_RE.search(text))


def _is_silent(span: tuple[float, float], silences: list[tuple[float, float]]) -> bool:
    start, end = span
    return any(s <= start + SILENCE_EDGE_TOLERANCE_S and e >= end - SILENCE_EDGE_TOLERANCE_S for s, e in silences)


def _window(span: tuple[float, float]) -> str:
    return f"{span[0]:.1f}-{span[1]:.1f} s"


@dataclass
class _Run:
    deadline: float
    language: str | None
    vad: bool
    results: dict[int, str] = field(default_factory=dict)


class TranscribeTool(BaseTool[TranscribeInput, TranscribeOutput]):
    name: ClassVar[str] = "transcribe_audio"
    description: ClassVar[str] = (
        "Transcribe speech in an audio or video file with Bodhan Indic-Transcribe "
        f"({', '.join(sorted(TRANSCRIBE_LANGUAGES))}). Pass the file by path in `audio_path`. Audio is split at "
        "pauses into windows of at most 25 s, transcribed in parallel and joined; windows that are entirely "
        "silence are skipped because the model invents text for silence. The transcript has no punctuation. "
        "The full transcript is written to `output_path` (.txt) with timed segments beside it (.segments.json); "
        "segment times are processing windows, not subtitle-accurate timings. Without `language`, the model picks "
        "the language and the tool reports the transcript's script, naming the language only when that script "
        "belongs to a single supported language. Segments flagged by heuristic checks (looping repetition, "
        "unexpected script) are kept but marked suspect. Errors are JSON with status, code, hint and retryable."
    )
    Input: ClassVar[type[TranscribeInput]] = TranscribeInput
    Output: ClassVar[type[TranscribeOutput]] = TranscribeOutput

    async def run(self, params: TranscribeInput) -> TranscribeOutput:
        settings = self.settings
        run = _Run(deadline=monotonic() + settings.tool_deadline_s, language=params.language, vad=params.vad)
        require_binaries()
        audio = resolve_input_file(params.audio_path, settings.input_dir, settings.readable_roots)
        check_audio_extension(audio)
        self._check_size(audio)
        txt_path, json_path, explicit = self._output_paths(params, audio)
        if explicit and not params.overwrite:
            for path in (txt_path, json_path):
                if path.exists():
                    raise BodhanToolError(
                        "file_exists",
                        f"{path.name} already exists.",
                        hint="Pass overwrite=true or choose a different output_path.",
                    )

        duration = await probe_duration(audio, deadline=run.deadline)
        if duration > settings.transcribe_max_duration_s:
            raise BodhanToolError(
                "input_too_large",
                f"{audio.name} is {duration:.0f} s long, over the {settings.transcribe_max_duration_s:.0f} s limit.",
                hint="Trim the audio or raise BODHAN_TRANSCRIBE_MAX_DURATION_S.",
                details={"duration_s": round(duration, 3)},
            )
        silences = await detect_silences(audio, deadline=run.deadline)
        spans = plan_chunks(duration, silences)
        silent = [_is_silent(span, silences) for span in spans]
        work = [i for i, is_silent in enumerate(silent) if not is_silent]

        with tempfile.TemporaryDirectory(prefix="bodhan-transcribe-") as workdir:
            wavs = await extract_chunks(audio, [spans[i] for i in work], Path(workdir), deadline=run.deadline)
            await self._transcribe_all(dict(zip(work, wavs, strict=True)), spans, run)

        segments, warnings = self._segments(spans, silent, run)
        transcript = " ".join(seg.text for seg in segments if seg.text)
        language, source, script, candidates = self._language(transcript, params.language)
        if not work:
            warnings.insert(0, "The audio appears to be silent or very quiet; nothing was sent for transcription.")
        elif not transcript:
            warnings.insert(0, "The model returned no text for any window.")
        if params.language and script and script not in LANGUAGE_SCRIPTS[params.language]:
            warnings.append(
                f"The transcript is mostly in {script} script, which is not used for "
                f"'{params.language}'; the audio may be in another language."
            )

        document = {
            "source": audio.name,
            "duration_s": round(duration, 3),
            "language": language,
            "language_source": source,
            "script": script,
            "timing_note": "Times are processing windows cut at pauses, not subtitle-accurate timings.",
            "segments": [seg.model_dump() for seg in segments],
        }
        txt_path, json_path = write_pair_atomic(
            txt_path,
            transcript,
            SEGMENTS_SUFFIX,
            json.dumps(document, ensure_ascii=False, indent=2),
            explicit=explicit,
            overwrite=params.overwrite,
            protected=[audio],
            max_attempts=MAX_NAME_ATTEMPTS,
        )

        limit = settings.inline_text_limit
        suspect = sum(1 for seg in segments if seg.status == "suspect")
        return TranscribeOutput(
            output_path=str(txt_path),
            segments_path=str(json_path),
            transcript=preview_text(transcript, limit),
            transcript_is_preview=len(transcript) > limit,
            language=language,
            language_source=source,
            script=script,
            candidate_languages=candidates,
            duration_s=round(duration, 3),
            chunks=len(spans),
            silent_chunks=len(spans) - len(work),
            suspect_chunks=suspect,
            segments=segments if len(segments) <= INLINE_SEGMENT_LIMIT else None,
            verification_status="warning" if warnings else "passed",
            warnings=warnings,
        )

    def _check_size(self, audio: Path) -> None:
        size = audio.stat().st_size
        limit = self.settings.transcribe_max_file_bytes
        if size > limit:
            raise BodhanToolError(
                "input_too_large",
                f"{audio.name} is {size / 1_048_576:.0f} MB, over the {self.settings.transcribe_max_file_mb} MB limit.",
                hint="Compress or trim the audio, or raise BODHAN_TRANSCRIBE_MAX_FILE_MB.",
                details={"bytes": size},
            )

    def _output_paths(self, params: TranscribeInput, audio: Path) -> tuple[Path, Path, bool]:
        settings = self.settings
        if params.output_path is not None:
            txt = resolve_output_file(params.output_path, settings.output_dir, settings.writable_roots)
            explicit = True
        else:
            stat = audio.stat()
            key = f"{audio}\0{stat.st_size}\0{stat.st_mtime_ns}\0{params.language}\0{params.vad}"
            digest = hashlib.sha256(key.encode()).hexdigest()[:10]
            name = f"{audio.stem}.transcript-{digest}.txt"
            txt = resolve_output_file(name, settings.output_dir, settings.writable_roots)
            explicit = False
        json_name = f"{txt.stem}{SEGMENTS_SUFFIX}"
        segments = resolve_output_file(json_name, txt.parent, settings.writable_roots)
        return txt, segments, explicit

    async def _transcribe_all(self, wavs: dict[int, Path], spans: list[tuple[float, float]], run: _Run) -> None:
        if not wavs:
            return
        limiter = asyncio.Semaphore(self.settings.max_concurrency)

        async def work(index: int, wav: Path) -> None:
            async with limiter:
                remaining = run.deadline - monotonic()
                if remaining <= 0:
                    raise self._deadline_error()
                start, end = spans[index]
                try:
                    result = await self.client.transcribe(
                        wav, end - start, language=run.language, vad=run.vad, deadline_s=remaining
                    )
                except BodhanToolError as error:
                    if error.code == "timeout" and monotonic() >= run.deadline:
                        raise self._deadline_error() from error
                    raise
                run.results[index] = result.text.strip()

        tasks = {asyncio.ensure_future(work(index, wav)): index for index, wav in wavs.items()}
        timeout = max(run.deadline - monotonic(), 0.0) + DEADLINE_GRACE_S
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        failed: list[tuple[int, BodhanToolError]] = []
        for task, index in sorted(tasks.items(), key=lambda item: item[1]):
            if task in done and not task.cancelled():
                exc = task.exception()
                if exc is None:
                    continue
                if not isinstance(exc, BodhanToolError):
                    raise exc
                failed.append((index, exc))
        if not failed and not pending:
            return
        error = failed[0][1] if failed else self._deadline_error()
        failed_indices = [index for index, _ in failed]
        error.details.update(
            {
                "total_chunks": len(spans),
                "chunks_to_transcribe": len(wavs),
                "completed_chunks": sorted(run.results),
                "failed_chunk_indices": failed_indices,
                "unfinished_chunk_indices": sorted(set(wavs) - set(run.results) - set(failed_indices)),
            }
        )
        raise error

    def _segments(
        self, spans: list[tuple[float, float]], silent: list[bool], run: _Run
    ) -> tuple[list[TranscriptSegment], list[str]]:
        segments: list[TranscriptSegment] = []
        notes: list[str] = []
        expected = LANGUAGE_SCRIPTS.get(run.language or "")
        for index, span in enumerate(spans):
            if silent[index]:
                segments.append(
                    TranscriptSegment(start_s=round(span[0], 3), end_s=round(span[1], 3), text="", status="silent")
                )
                continue
            text = run.results.get(index, "")
            reasons = []
            if not text:
                reasons.append("no text returned")
            elif is_repetitive(text):
                reasons.append("looping repetition")
            if text and expected:
                script = dominant_script(text)
                if script and script not in expected:
                    reasons.append(f"{script} script")
            status = "suspect" if reasons else "transcribed"
            segments.append(
                TranscriptSegment(start_s=round(span[0], 3), end_s=round(span[1], 3), text=text, status=status)
            )
            if reasons:
                notes.append(f"{_window(span)}: {', '.join(reasons)}")
        warnings: list[str] = []
        if notes:
            listed = "; ".join(notes[:MAX_LISTED_WARNINGS])
            more = f"; and {len(notes) - MAX_LISTED_WARNINGS} more" if len(notes) > MAX_LISTED_WARNINGS else ""
            warnings.append(f"{len(notes)} window(s) flagged as suspect ({listed}{more}); verify their text.")
        return segments, warnings

    @staticmethod
    def _language(transcript: str, requested: str | None) -> tuple[str | None, LanguageSource, str | None, list[str]]:
        script = dominant_script(transcript)
        if requested:
            return requested, "requested", script, []
        language, candidates = language_from_script(script)
        if language is not None:
            return language, "detected", script, []
        return None, "undetermined", script, candidates

    def _deadline_error(self) -> BodhanToolError:
        return BodhanToolError(
            "tool_deadline_exceeded",
            f"Transcription did not finish within {self.settings.tool_deadline_s:.0f}s.",
            hint="Retry with shorter audio or raise BODHAN_TOOL_DEADLINE_S.",
            retryable=True,
        )
