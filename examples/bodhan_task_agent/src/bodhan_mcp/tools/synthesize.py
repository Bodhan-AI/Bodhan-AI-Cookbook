from __future__ import annotations

import asyncio
import difflib
import hashlib
import io
import re
import tempfile
import unicodedata
import wave
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import ClassVar

from bodhan_mcp.core.base_tool import BaseTool
from bodhan_mcp.core.languages import SPEAK_DEFAULT_VOICES, SPEAK_LANGUAGES, SPEAK_VOICES
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.schemas.synthesize import (
    AI_SPEECH_LABEL,
    SpeechVerification,
    SynthesizeInput,
    SynthesizeOutput,
)
from bodhan_mcp.utils.audio import join_audio, probe_duration, require_binaries
from bodhan_mcp.utils.files import read_text_file, resolve_input_file, resolve_output_file, write_bytes_atomic
from bodhan_mcp.utils.text import split_for_speech

SPEAK_CHUNK_CHARS = 250
MAX_VERIFIABLE_S = 29.0
RECALL_THRESHOLD = 0.8
EXTRA_WORDS_RATIO = 1.3
MAX_UNMATCHED = 10
MIN_VERIFY_BUDGET_S = 5.0
DEADLINE_GRACE_S = 1.0
MAX_NAME_ATTEMPTS = 100
_NUKTA = "़"
_CHANDRABINDU = "ँ"
_ANUSVARA = "ं"

_FENCED_BLOCK_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)
_FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~).*$", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"\*\*|__|\*|`")


def plain_text(text: str) -> str:
    text = _FENCED_BLOCK_RE.sub("", text)
    text = _FENCE_LINE_RE.sub("", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_RE.sub("", text)
    text = _TABLE_RULE_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _QUOTE_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") or stripped.endswith("|"):
            stripped = ", ".join(cell.strip() for cell in stripped.strip("|").split("|") if cell.strip())
        lines.append(stripped)
    return "\n".join(lines).strip()


def comparison_words(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    kept = []
    for char in decomposed:
        if char == _NUKTA:
            continue
        if char == _CHANDRABINDU:
            char = _ANUSVARA
        kept.append(char if unicodedata.category(char)[0] in "LMN" else " ")
    return unicodedata.normalize("NFC", "".join(kept)).split()


@dataclass(frozen=True)
class _Agreement:
    source_words: int
    matched_words: int
    heard_words: int
    unmatched: list[str]


def _agreement(source: str, heard: str) -> _Agreement:
    src = comparison_words(source)
    back = comparison_words(heard)
    matcher = difflib.SequenceMatcher(None, src, back, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    unmatched = [
        " ".join(src[i1:i2]) for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in ("delete", "replace") and i2 > i1
    ]
    return _Agreement(len(src), matched, len(back), unmatched)


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise wave.Error("expected mono 16-bit PCM")
        frames = handle.getnframes()
        rate = handle.getframerate()
    if frames <= 0 or rate <= 0:
        raise wave.Error("empty audio")
    return frames / float(rate)


@dataclass
class _Run:
    deadline: float
    voice: str
    language: str
    style: str | None
    pieces: dict[int, Path] = field(default_factory=dict)
    seconds: dict[int, float] = field(default_factory=dict)


class SynthesizeTool(BaseTool[SynthesizeInput, SynthesizeOutput]):
    name: ClassVar[str] = "synthesize_speech"
    description: ClassVar[str] = (
        "Read text aloud with Bodhan Indic-Speak and save it as WAV or MP3. Languages: "
        f"{', '.join(sorted(SPEAK_LANGUAGES))}. The text must already be in that language; this tool does not "
        "translate. Pass short text in `text` or a UTF-8 .txt/.md file in `text_path`; Markdown symbols and code "
        "blocks are removed. Choose an exact `voice`, or a `gender` to get the language's default voice. Text is "
        "split at sentence boundaries into pieces of about 250 characters (Bodhan speaks about 30 s per request), "
        "synthesized under Bodhan's low rate limit and joined. The audio is saved first; then, with `verify` "
        "(default), each piece is transcribed back and compared with the text to flag missing or repeated speech. "
        "Speech is the most expensive Bodhan model (Rs 6 per 10,000 characters), so avoid repeating calls. The "
        "file is labelled AI-generated. Errors are JSON with status, code, hint and retryable."
    )
    Input: ClassVar[type[SynthesizeInput]] = SynthesizeInput
    Output: ClassVar[type[SynthesizeOutput]] = SynthesizeOutput

    async def run(self, params: SynthesizeInput) -> SynthesizeOutput:
        settings = self.settings
        require_binaries()
        voice, gender = self._resolve_voice(params)
        run = _Run(
            deadline=monotonic() + settings.tool_deadline_s,
            voice=voice,
            language=params.language,
            style=params.style,
        )
        source, source_path = self._load_source(params)
        chunks = split_for_speech(plain_text(source), SPEAK_CHUNK_CHARS)
        if not chunks:
            raise BodhanToolError("invalid_input", "There is no readable text left after removing Markdown symbols.")
        self._check_capacity(len(chunks))
        output_path, explicit = self._output_path(params, "\n".join(chunks), voice, source_path)
        if explicit and not params.overwrite and output_path.exists():
            raise BodhanToolError(
                "file_exists",
                f"{output_path.name} already exists.",
                hint="Pass overwrite=true or choose a different output_path.",
            )

        warnings: list[str] = []
        with tempfile.TemporaryDirectory(prefix="bodhan-speak-") as workdir:
            work = Path(workdir)
            await self._synthesize_all(chunks, run, work)
            long_pieces = [i for i, seconds in sorted(run.seconds.items()) if seconds >= MAX_VERIFIABLE_S]
            if long_pieces:
                warnings.append(
                    f"{len(long_pieces)} piece(s) reached Bodhan's ~30 s limit and may have been cut off: "
                    f"{long_pieces}."
                )
            joined = await join_audio(
                [run.pieces[i] for i in range(len(chunks))],
                work / f"speech.{params.format}",
                deadline=run.deadline,
                fmt=params.format,
                comment=AI_SPEECH_LABEL,
            )
            duration = await probe_duration(joined, deadline=run.deadline)
            protected = [source_path] if source_path else []
            data = joined.read_bytes()
            if explicit:
                write_bytes_atomic(output_path, data, overwrite=params.overwrite, protected=protected)
            else:
                output_path = self._write_new(output_path, data, protected)

            if params.verify:
                verification = await self._verify(chunks, run, set(long_pieces))
            else:
                verification = SpeechVerification(status="skipped", reason="verify was false.")
        if verification.status in ("warning", "error") and verification.reason:
            warnings.append(f"Verification {verification.status}: {verification.reason}")

        return SynthesizeOutput(
            output_path=str(output_path),
            format=params.format,
            voice=voice,
            voice_gender=gender,
            language=params.language,
            style=params.style,
            duration_s=round(duration, 3),
            chars=sum(len(chunk) for chunk in chunks),
            chunks=len(chunks),
            verification=verification,
            warnings=warnings,
        )

    @staticmethod
    def _resolve_voice(params: SynthesizeInput) -> tuple[str, str]:
        if params.voice is not None:
            return params.voice, SPEAK_VOICES[params.voice][1]
        gender = params.gender or "female"
        return SPEAK_DEFAULT_VOICES[(params.language, gender)], gender

    def _load_source(self, params: SynthesizeInput) -> tuple[str, Path | None]:
        settings = self.settings
        if params.text_path is not None:
            path = resolve_input_file(params.text_path, settings.input_dir, settings.readable_roots)
            return read_text_file(path, settings.max_input_chars), path
        text = params.text or ""
        if len(text) > settings.max_input_chars:
            raise BodhanToolError(
                "input_too_large",
                f"text has {len(text)} characters, over the {settings.max_input_chars}-character limit.",
                hint="Shorten the text or raise BODHAN_MAX_INPUT_CHARS.",
            )
        return text, None

    def _check_capacity(self, pieces: int) -> None:
        settings = self.settings
        capacity = int(settings.speak_requests_per_minute * settings.tool_deadline_s / 60)
        if pieces > capacity:
            raise BodhanToolError(
                "input_too_large",
                f"The text needs {pieces} speech requests, but at {settings.speak_requests_per_minute} requests per "
                f"minute only about {capacity} fit in the {settings.tool_deadline_s:.0f}s tool deadline.",
                hint="Shorten or summarize the text, split it into several calls, or raise BODHAN_TOOL_DEADLINE_S.",
                details={"pieces": pieces, "estimated_capacity": capacity},
            )

    def _output_path(
        self, params: SynthesizeInput, spoken: str, voice: str, source_path: Path | None
    ) -> tuple[Path, bool]:
        settings = self.settings
        if params.output_path is not None:
            path = resolve_output_file(params.output_path, settings.output_dir, settings.writable_roots)
            return path, True
        key = f"{spoken}\0{voice}\0{params.language}\0{params.style}\0{params.format}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:10]
        stem = source_path.stem if source_path else "speech"
        name = f"{stem}.{params.language}-{voice.lower()}-{digest}.{params.format}"
        return resolve_output_file(name, settings.output_dir, settings.writable_roots), False

    async def _synthesize_all(self, chunks: list[str], run: _Run, work: Path) -> None:
        limiter = asyncio.Semaphore(self.settings.max_concurrency)

        async def piece(index: int, text: str) -> None:
            async with limiter:
                remaining = run.deadline - monotonic()
                if remaining <= 0:
                    raise self._deadline_error()
                try:
                    result = await self.client.speak(
                        text, run.voice, run.language, style=run.style, deadline_s=remaining
                    )
                except BodhanToolError as error:
                    if error.code == "timeout" and monotonic() >= run.deadline:
                        raise self._deadline_error() from error
                    raise
                try:
                    seconds = _wav_seconds(result.audio)
                except (wave.Error, EOFError) as exc:
                    raise BodhanToolError(
                        "upstream_error",
                        f"Bodhan returned unusable audio for piece {index + 1}: {exc}.",
                        hint="Retry later.",
                        retryable=True,
                    ) from exc
                path = work / f"piece_{index:04d}.wav"
                path.write_bytes(result.audio)
                run.pieces[index] = path
                run.seconds[index] = seconds

        tasks = {asyncio.ensure_future(piece(i, text)): i for i, text in enumerate(chunks)}
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
                "total_pieces": len(chunks),
                "completed_pieces": sorted(run.pieces),
                "failed_piece_indices": failed_indices,
                "unfinished_piece_indices": sorted(set(range(len(chunks))) - set(run.pieces) - set(failed_indices)),
            }
        )
        raise error

    async def _verify(self, chunks: list[str], run: _Run, too_long: set[int]) -> SpeechVerification:
        remaining = run.deadline - monotonic()
        if remaining < MIN_VERIFY_BUDGET_S:
            return SpeechVerification(status="error", reason="Not enough time was left to transcribe the audio back.")
        targets = [i for i in range(len(chunks)) if i not in too_long]
        if not targets:
            return SpeechVerification(
                status="error", reason="Every piece is too long (29 s or more) to transcribe back."
            )

        async def heard(index: int) -> str:
            result = await self.client.transcribe(
                run.pieces[index],
                run.seconds[index],
                language=run.language,
                deadline_s=max(run.deadline - monotonic(), 0.1),
            )
            return result.text

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(heard(i) for i in targets), return_exceptions=True), timeout=remaining
            )
        except asyncio.TimeoutError:
            return SpeechVerification(status="error", reason="Transcribing the audio back ran past the tool deadline.")
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, BodhanToolError):
                raise result
        errors = [r for r in results if isinstance(r, BodhanToolError)]
        if errors:
            return SpeechVerification(
                status="error",
                reason=f"{len(errors)} of {len(targets)} pieces could not be transcribed back ({errors[0].code}).",
            )

        source_words = matched = 0
        unmatched: list[str] = []
        repeated: list[int] = []
        for index, back in zip(targets, results, strict=True):
            agreement = _agreement(chunks[index], str(back))
            source_words += agreement.source_words
            matched += agreement.matched_words
            unmatched.extend(agreement.unmatched)
            if agreement.source_words and agreement.heard_words > EXTRA_WORDS_RATIO * agreement.source_words:
                repeated.append(index)
        if not source_words:
            return SpeechVerification(status="skipped", reason="The text has no words to compare.")
        recall = matched / source_words
        problems = []
        if recall < RECALL_THRESHOLD:
            problems.append(f"only {recall:.0%} of the text's words were heard back in order")
        if repeated:
            problems.append(f"piece(s) {repeated} were heard with many extra words (possible repetition)")
        if too_long:
            problems.append(f"piece(s) {sorted(too_long)} were too long to transcribe back and were not checked")
        return SpeechVerification(
            status="warning" if problems else "passed",
            word_recall=round(recall, 3),
            potentially_unmatched=unmatched[:MAX_UNMATCHED],
            reason="; ".join(problems) or None,
        )

    def _deadline_error(self) -> BodhanToolError:
        return BodhanToolError(
            "tool_deadline_exceeded",
            f"Speech synthesis did not finish within {self.settings.tool_deadline_s:.0f}s.",
            hint="Shorten the text or raise BODHAN_TOOL_DEADLINE_S.",
            retryable=True,
        )

    def _write_new(self, path: Path, data: bytes, protected: list[Path]) -> Path:
        for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
            candidate = path if attempt == 1 else path.with_name(f"{path.stem}-{attempt}{path.suffix}")
            try:
                return write_bytes_atomic(candidate, data, overwrite=False, protected=protected)
            except BodhanToolError as error:
                if error.code != "file_exists":
                    raise
        raise BodhanToolError(
            "file_exists",
            f"Could not find a free file name for {path.name} in the output folder.",
            hint="Pass an explicit output_path or clean up the output folder.",
        )
