from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import ClassVar

from bodhan_mcp.core.base_tool import BaseTool
from bodhan_mcp.core.languages import TRANSLATE_TARGETS
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.schemas.translate import TranslateInput, TranslateOutput
from bodhan_mcp.utils.files import read_text_file, resolve_input_file, resolve_output_file, write_text_atomic
from bodhan_mcp.utils.text import (
    check_structure,
    check_tables,
    oversized_table_rows,
    preview_text,
    restore_table_delimiters,
    split_edges,
    split_text,
    table_shapes,
)

logger = logging.getLogger(__name__)

MAX_RECOVERY_DEPTH = 2
MIN_SPLIT_CHARS = 40
MIN_LENGTH_RATIO = 0.2
MAX_NAME_ATTEMPTS = 100
DEADLINE_GRACE_S = 1.0
_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})


@dataclass
class _Run:
    deadline: float
    target_language: str
    script: str
    recovery_splits: int = 0
    results: dict[int, str] = field(default_factory=dict)
    first_error: BodhanToolError | None = None


class TranslateTool(BaseTool[TranslateInput, TranslateOutput]):
    name: ClassVar[str] = "translate_text"
    description: ClassVar[str] = (
        "Translate text with Bodhan Indic-Translate into English or one of 22 Indian languages "
        f"({', '.join(sorted(TRANSLATE_TARGETS))}). Input can be any of those languages; the source language "
        "does not need to be stated. Pass short text in `text`, or a UTF-8 .txt/.md file in `text_path` for long "
        "documents. Long input is split at paragraph, sentence and table-row boundaries and translated in pieces; "
        "Markdown tables are kept as whole pieces where they fit and their shape is checked afterwards. "
        "The full translation is always written to `output_path`; the response carries it inline only when short "
        "(see translation_is_preview). Unsupported languages are rejected before any call is made. "
        "Errors are JSON with status, code, hint and retryable."
    )
    Input: ClassVar[type[TranslateInput]] = TranslateInput
    Output: ClassVar[type[TranslateOutput]] = TranslateOutput

    async def run(self, params: TranslateInput) -> TranslateOutput:
        settings = self.settings
        run = _Run(
            deadline=monotonic() + settings.tool_deadline_s,
            target_language=params.target_language,
            script=params.script,
        )
        source, source_path = self._load_source(params)
        output_path, explicit = self._output_path(params, source, source_path)
        if explicit and not params.overwrite and output_path.exists():
            raise BodhanToolError(
                "file_exists",
                f"{output_path.name} already exists.",
                hint="Pass overwrite=true or choose a different output_path.",
            )

        protected = [source_path] if source_path else []
        chunks = split_text(source, settings.translate_chunk_chars)
        await self._translate_all(chunks, run)

        if run.first_error is not None:
            self._raise_incomplete(chunks, run, run.first_error, output_path, protected)

        translated = "".join(run.results.get(i, chunk) for i, chunk in enumerate(chunks))
        translated = restore_table_delimiters(source, translated)
        warnings = self._verify(source, translated, settings.translate_chunk_chars)
        if explicit:
            write_text_atomic(output_path, translated, overwrite=params.overwrite, protected=protected)
        else:
            output_path = self._write_new(output_path, translated, protected)

        limit = settings.inline_text_limit
        return TranslateOutput(
            output_path=str(output_path),
            target_language=params.target_language,
            script=params.script,
            translation=preview_text(translated, limit),
            translation_is_preview=len(translated) > limit,
            source_chars=len(source),
            translated_chars=len(translated),
            chunks=len(chunks),
            recovery_splits=run.recovery_splits,
            verification_status="warning" if warnings else "passed",
            untrusted_content=source_path is not None,
            warnings=warnings,
        )

    def _load_source(self, params: TranslateInput) -> tuple[str, Path | None]:
        settings = self.settings
        if params.text_path is not None:
            path = resolve_input_file(params.text_path, settings.input_dir, settings.readable_roots)
            text = read_text_file(path, settings.max_input_chars)
            if not text.strip():
                raise BodhanToolError(
                    "invalid_input",
                    f"'{params.text_path}' contains no text to translate.",
                    details={"path": params.text_path},
                )
            return text, path
        text = params.text or ""
        if len(text) > settings.max_input_chars:
            raise BodhanToolError(
                "input_too_large",
                f"text has {len(text)} characters, over the {settings.max_input_chars}-character limit.",
                hint="Pass a file via text_path in smaller parts, or raise BODHAN_MAX_INPUT_CHARS.",
            )
        return text, None

    def _output_path(self, params: TranslateInput, source: str, source_path: Path | None) -> tuple[Path, bool]:
        settings = self.settings
        if params.output_path is not None:
            return resolve_output_file(params.output_path, settings.output_dir, settings.writable_roots), True
        digest = hashlib.sha256(f"{params.target_language}\0{params.script}\0{source}".encode()).hexdigest()[:10]
        if source_path is not None:
            stem = source_path.stem
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in _TEXT_SUFFIXES else ".txt"
        else:
            stem = "translation"
            suffix = ".md" if table_shapes(source) else ".txt"
        name = f"{stem}.{params.target_language}-{digest}{suffix}"
        return resolve_output_file(name, settings.output_dir, settings.writable_roots), False

    async def _translate_all(self, chunks: list[str], run: _Run) -> None:
        limiter = asyncio.Semaphore(self.settings.translate_parallel_chunks)

        async def work(index: int, chunk: str) -> None:
            async with limiter:
                run.results[index] = await self._translate_piece(chunk, run, depth=0)

        tasks = {
            asyncio.ensure_future(work(index, chunk)): index for index, chunk in enumerate(chunks) if chunk.strip()
        }
        if not tasks:
            return
        timeout = max(run.deadline - monotonic(), 0.0) + DEADLINE_GRACE_S
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        failed: list[tuple[int, BaseException]] = []
        for task, index in tasks.items():
            if task in done and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    failed.append((index, exc))
        failed.sort(key=lambda item: item[0])
        for _, exc in failed:
            if not isinstance(exc, BodhanToolError):
                raise exc
        if failed:
            run.first_error = failed[0][1]
        elif pending:
            run.first_error = self._deadline_error()

    async def _translate_piece(self, chunk: str, run: _Run, depth: int) -> str:
        lead, core, trail = split_edges(chunk)
        if not core:
            return chunk
        remaining = run.deadline - monotonic()
        if remaining <= 0:
            raise self._deadline_error()
        try:
            result = await self.client.translate(core, run.target_language, script=run.script, deadline_s=remaining)
        except BodhanToolError as error:
            if error.code == "timeout" and monotonic() >= run.deadline:
                raise self._deadline_error() from error
            raise
        if result.truncated:
            return lead + await self._recover(core, run, depth) + trail
        if not result.text.strip():
            raise BodhanToolError(
                "incomplete_result",
                "Bodhan returned an empty translation for part of the input.",
                hint="Retry later; if it persists, the text may be unsupported by the model.",
            )
        return lead + result.text.strip() + trail

    async def _recover(self, core: str, run: _Run, depth: int) -> str:
        if depth >= MAX_RECOVERY_DEPTH or len(core) < MIN_SPLIT_CHARS:
            raise BodhanToolError(
                "incomplete_result",
                "The model kept cutting its answer short for part of the input, even after splitting it.",
                hint="Lower BODHAN_TRANSLATE_CHUNK_CHARS and retry.",
            )
        halves = split_text(core, len(core) // 2 + 1)
        if len(halves) < 2:
            halves = [core[: len(core) // 2], core[len(core) // 2 :]]
        run.recovery_splits += 1
        parts = [await self._translate_piece(half, run, depth + 1) for half in halves]
        return "".join(parts)

    def _deadline_error(self) -> BodhanToolError:
        return BodhanToolError(
            "tool_deadline_exceeded",
            f"Translation did not finish within {self.settings.tool_deadline_s:.0f}s.",
            hint="Retry with a shorter input or raise BODHAN_TOOL_DEADLINE_S.",
            retryable=True,
        )

    def _write_new(self, path: Path, text: str, protected: list[Path]) -> Path:
        for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
            candidate = path if attempt == 1 else path.with_name(f"{path.stem}-{attempt}{path.suffix}")
            try:
                return write_text_atomic(candidate, text, overwrite=False, protected=protected)
            except BodhanToolError as error:
                if error.code != "file_exists":
                    raise
        raise BodhanToolError(
            "file_exists",
            f"Could not find a free file name for {path.name} in the output folder.",
            hint="Pass an explicit output_path or clean up the output folder.",
        )

    def _raise_incomplete(
        self,
        chunks: list[str],
        run: _Run,
        error: BodhanToolError,
        output_path: Path,
        protected: list[Path],
    ) -> None:
        work = [i for i, chunk in enumerate(chunks) if chunk.strip()]
        completed = [i for i in work if i in run.results]
        error.details["completed_chunks"] = completed
        error.details["failed_chunks"] = [i for i in work if i not in run.results]
        if not completed:
            raise error
        body = "".join(
            run.results.get(i, chunk if not chunk.strip() else f"\n[[chunk {i} not translated]]\n")
            for i, chunk in enumerate(chunks)
        )
        header = (
            f"[[PARTIAL TRANSLATION: {len(completed)} of {len(work)} pieces translated; stopped by {error.code}]]\n\n"
        )
        partial = output_path.with_name(f"{output_path.stem}.partial-{secrets.token_hex(4)}{output_path.suffix}")
        try:
            write_text_atomic(partial, header + body, overwrite=False, protected=protected)
        except (BodhanToolError, OSError) as write_error:
            logger.warning("partial translation could not be saved: %s", write_error)
            error.details["partial_write_error"] = str(write_error)
        else:
            error.details["partial_output_path"] = str(partial)
        raise error

    def _verify(self, source: str, translated: str, chunk_chars: int) -> list[str]:
        warnings = check_tables(source, translated) + check_structure(source, translated)
        if oversized_table_rows(source, chunk_chars):
            warnings.append("Some table rows were longer than one piece and were split; table layout may be damaged.")
        if len(translated.strip()) < MIN_LENGTH_RATIO * len(source.strip()):
            warnings.append("The translation is much shorter than the source; content may be missing.")
        return warnings
