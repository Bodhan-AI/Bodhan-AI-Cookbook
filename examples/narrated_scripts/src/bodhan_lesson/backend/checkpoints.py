"""Persist completed runner stages and protect their shared output directory."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, fields, is_dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from types import UnionType
from typing import Literal, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import uuid4

import pypdfium2 as pdfium
from PIL import Image

from .audio.spoken_numbers import has_speech_numerals
from .config import LANGUAGES, BackendMode, LessonRequest
from .models import (
    Attempt, Glossary, LanguageDecision, NarrationScript, NarrationScripts, Page, PreprocessedPage, SpeechRun,
    TargetSpeech, TranslationPlan,
)
from .ocr.inputs import png_bytes, render_page
from .ocr.preprocess import EXIF_ORIENTATION
from .report import AttemptLog

STATE_DIRECTORY = "work/checkpoints"
STATE_VERSION = 1
T = TypeVar("T")


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_identity(input_path: Path, pages: tuple[Page, ...]) -> tuple:
    digests: dict[Path, str | None] = {}
    entries = []
    for page in pages:
        if page.path not in digests:
            try:
                digests[page.path] = file_digest(page.path)
            except OSError:
                # Preprocessing reports the unreadable page. A readable file gets
                # a different identity on the next run and invalidates this result.
                digests[page.path] = None
        entries.append((str(page.path.resolve()), page.pdf_offset, digests[page.path]))
    return str(input_path.resolve()), tuple(entries)


def _generated_paths(out: Path) -> Iterator[Path]:
    shared = {
        "source": ("language.json", "pages", "lesson.src.md"),
        "work": ("images", "originals", "checkpoints", "layout.json", "boundaries.json", "chapter.json",
                 "translation-plan.json", "pivot.en.json", "narration-plan.json"),
    }
    for directory, names in shared.items():
        parent = out / directory
        if parent.is_symlink():
            yield parent
        else:
            yield from (parent / name for name in names)
    yield out / "report.json"
    yield out / "events.jsonl"
    for language in LANGUAGES:
        target = out / language
        if target.is_dir() and not target.is_symlink():
            for name in (
                "segments.json", "translation-qa.json", "chunks.json", "speech-qa.json",
                "narration", "work", f"narration_script.{language}.json",
                f"lesson.{language}.md", f"lesson.{language}.mp3",
            ):
                yield target / name


def clear_output(request: LessonRequest, pages: tuple[Page, ...]) -> None:
    out = request.out.resolve()
    if request.out.is_symlink() or Path.cwd().is_relative_to(out):
        raise ValueError("Starting over requires a dedicated output directory.")
    inputs = (request.input_path, request.glossary, request.voices, *(page.path for page in pages))
    protected = tuple(
        location for path in inputs if path is not None
        for location in (path.absolute(), path.resolve())
    )
    generated = tuple(_generated_paths(out))
    if any(path == out or any(
        path.is_relative_to(artifact) for artifact in generated
    ) for path in protected):
        raise ValueError("Starting over would remove the pipeline input or configuration.")
    for artifact in generated:
        if artifact.is_symlink() or artifact.is_file():
            artifact.unlink()
        elif artifact.is_dir():
            rmtree(artifact)


@contextmanager
def output_lock(request: LessonRequest, pages: tuple[Page, ...]) -> Iterator[None]:
    """Hold one writer lock across checkpoint restoration, generation and cleanup."""
    import fcntl

    out = request.out.resolve()
    if request.force and (request.out.is_symlink() or Path.cwd().is_relative_to(out)):
        raise ValueError("--force requires a dedicated output directory.")
    if request.force and out == request.input_path.resolve():
        raise ValueError("--force would remove the pipeline input or configuration.")
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".pipeline.lock").open("ab") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another lesson run is using this output directory.") from None
        if request.force:
            clear_output(request, pages)
        yield


@cache
def _record_fields(record: type) -> tuple:
    hints = get_type_hints(record)
    return tuple((field.name, hints[field.name]) for field in fields(record) if field.init)


def restore_record(value, expected):
    """Read JSON into the declared record types without importing anything from the file."""
    origin, args = get_origin(expected), get_args(expected)
    if origin in (Union, UnionType):
        for choice in args:
            try:
                return restore_record(value, choice)
            except (TypeError, ValueError, KeyError):
                continue
        raise ValueError("Checkpoint value does not match its schema.")
    if origin is Literal and value in args:
        return value
    if origin is tuple and isinstance(value, list):
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(restore_record(item, args[0]) for item in value)
        return tuple(restore_record(item, item_type) for item, item_type in zip(value, args, strict=True))
    if origin is dict and isinstance(value, dict):
        return {restore_record(key, args[0]): restore_record(item, args[1]) for key, item in value.items()}
    if is_dataclass(expected) and isinstance(value, dict):
        return expected(**{name: restore_record(value[name], hint) for name, hint in _record_fields(expected)})
    if expected is object or type(value) is expected or expected is float and type(value) is int:
        return value
    raise ValueError("Checkpoint value does not match its schema.")


class LegacyCheckpointUnavailable(Exception):
    """The older inspection artifacts do not establish a compatible checkpoint."""


def _legacy_attempts(evidence: list[dict], mode: BackendMode, first_index: int) -> list[Attempt]:
    saved: dict[str, Attempt] = {}
    for record in evidence:
        for attempt in restore_record(record["attempts"], tuple[Attempt, ...]):
            if attempt.id in saved and saved[attempt.id] != attempt:
                raise LegacyCheckpointUnavailable
            if attempt.service.startswith("indic-") and (
                (mode == "hosted" and attempt.backend != "hosted")
                or (mode == "local-first" and attempt.backend == "hosted" and not attempt.fallback_reason)
            ):
                raise LegacyCheckpointUnavailable
            saved[attempt.id] = attempt
    return [saved[f"a{index:06d}"] for index in range(first_index, first_index + len(saved))]


def import_legacy_scripts(
    request: LessonRequest, pages: tuple[Page, ...], override: LanguageDecision | None, glossary: Glossary | None,
    context_limit: int, mode: BackendMode, voices: dict[str, str | None], max_chars: int, attempts: AttemptLog,
) -> NarrationScripts:
    """Recover Stage 4 output written before the runner began saving checkpoint state."""
    out = request.out

    def read(name: str):
        return json.loads((out / name).read_text(encoding="utf-8"))

    try:
        scripts = []
        evidence = []
        for language in request.target_languages:
            record = read(f"{language}/narration_script.{language}.json")
            for section in record["sections"]:
                for chunk in section["chunks"]:
                    if has_speech_numerals(chunk["text"]):
                        raise LegacyCheckpointUnavailable
                    chunk.setdefault("spoken_numbers", [])
            script = restore_record(record, NarrationScript)
            if (script.language, script.voice, script.max_chars) != (language, voices[language], max_chars):
                raise LegacyCheckpointUnavailable
            scripts.append(script)
            evidence.extend((record, read(f"{language}/segments.json"), read(f"{language}/translation-qa.json")))
        expected = {out / language / f"narration_script.{language}.json" for language in request.target_languages}
        if set(out.glob("*/narration_script.*.json")) != expected:
            raise LegacyCheckpointUnavailable
        language_record = read("source/language.json")
        language = restore_record(language_record, LanguageDecision)
        if (language.method == "override") != (override is not None) or (
            override is not None and override.code != language.code
        ):
            raise LegacyCheckpointUnavailable
        plan = restore_record(read("work/translation-plan.json"), TranslationPlan)
        if (plan.source_lang, list(plan.glossary.items()), plan.context_limit) != (
            language.code, list((glossary or {}).items()), context_limit
        ):
            raise LegacyCheckpointUnavailable
        evidence.extend((language_record, read("work/narration-plan.json")))
        if (out / "work/pivot.en.json").is_file():
            evidence.append(read("work/pivot.en.json"))
        transforms = out / "work/images"
        if set(transforms.glob("*.transform.json")) != {
            transforms / f"{page.page_id}.transform.json" for page in pages
        }:
            raise LegacyCheckpointUnavailable
        for page in pages:
            prepared = restore_record(read(f"work/images/{page.page_id}.transform.json"), PreprocessedPage)
            with render_page(page) as pixels:
                orientation = pixels.getexif().get(EXIF_ORIENTATION)
                original_digest = sha256(png_bytes(pixels)).hexdigest()
            if (prepared.page_id != page.page_id or prepared.input_sha256 != original_digest
                    or prepared.transform is None or prepared.transform.operations[0].exif_orientation != orientation):
                raise LegacyCheckpointUnavailable
            evidence.append(read(f"source/pages/{page.page_id}.json"))
        restored = _legacy_attempts(evidence, mode, first_index=1)
    except (OSError, ValueError, TypeError, KeyError, IndexError, SyntaxError,
            Image.DecompressionBombError, pdfium.PdfiumError):
        raise LegacyCheckpointUnavailable from None
    attempts.attempts.extend(restored)
    return NarrationScripts(tuple(scripts))


def import_legacy_speech(scripts: NarrationScripts, out: Path, mode: BackendMode, attempts: AttemptLog) -> SpeechRun:
    """Recover the initial speech candidates associated with imported Stage 4 scripts."""
    evidence = []
    targets = []
    try:
        for script in scripts.scripts:
            language = script.language
            record = json.loads((out / language / "chunks.json").read_text(encoding="utf-8"))
            target = restore_record(record, TargetSpeech)
            voice = script.voice if script.voice is not None else LANGUAGES[language].voices[0]
            if (target.language, target.voice) != (language, voice):
                raise LegacyCheckpointUnavailable
            expected = [(chunk.id, chunk.section_id, len(chunk.text))
                        for section in script.sections for chunk in section.chunks]
            observed = [(chunk.chunk_id, chunk.section_id, chunk.requested_characters) for chunk in target.chunks]
            if expected != observed:
                raise LegacyCheckpointUnavailable
            for chunk in target.chunks:
                if chunk.status == "ok":
                    audio = Path(chunk.audio_path or "")
                    directory = Path(language) / "work/audio"
                    if audio.parent != directory or not (out / audio).is_file():
                        raise LegacyCheckpointUnavailable
            evidence.append(record)
            targets.append(target)
        restored = _legacy_attempts(evidence, mode, first_index=len(attempts.attempts) + 1)
    except (OSError, ValueError, TypeError, KeyError):
        raise LegacyCheckpointUnavailable from None
    attempts.attempts.extend(restored)
    return SpeechRun(tuple(targets))


def write_atomic_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


class CheckpointStore:
    def __init__(self, out: Path, root_identity: tuple, attempts: AttemptLog,
                 progress: Callable[[str], None] | None = None):
        self.out = out
        self.directory = out / STATE_DIRECTORY
        self.identity = self._key(root_identity)
        self.attempts = attempts
        self.progress = progress
        self._in_progress: Path | None = None
        self._reused_items = 0

    @staticmethod
    def _key(value) -> str:
        return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _fingerprint(self, settings: object) -> str:
        return self._key((STATE_VERSION, self.identity, settings))

    def _load(self, path: Path, fingerprint: str, result_type: type[T],
              artifacts: Callable[[T], Iterator[str]]) -> tuple[T, tuple[Attempt, ...], str] | None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["fingerprint"] == fingerprint and isinstance(record["generation"], str):
                result = restore_record(record["result"], result_type)
                attempts = restore_record(record["attempts"], tuple[Attempt, ...])
                if all((self.out / name).is_file() for name in artifacts(result)):
                    return result, attempts, record["generation"]
        except (OSError, TypeError, ValueError, KeyError):
            pass
        return None

    def has_checkpoint(
        self, stage: str, result_type: type[T], settings: object, artifacts: Callable[[T], Iterator[str]],
    ) -> bool:
        return self._load(self.directory / f"{stage}.json", self._fingerprint(settings),
                          result_type, artifacts) is not None

    def previous_checkpoint(
        self, stage: str, result_type: type[T], settings: object, artifacts: Callable[[T], Iterator[str]],
    ) -> tuple[T, tuple[Attempt, ...]] | None:
        """Keep compatible prior evidence available through an interrupted policy update."""
        path = self.directory / f"{stage}.json"
        archive = self.directory / f"{stage}.previous.json"
        fingerprint = self._fingerprint(settings)
        saved = self._load(path, fingerprint, result_type, artifacts)
        if saved is not None:
            path.replace(archive)
        else:
            saved = self._load(archive, fingerprint, result_type, artifacts)
        return (saved[0], saved[1]) if saved is not None else None

    def item(
        self, key: object, result_type: type[T], compute: Callable[[], T],
        artifacts: Callable[[T], Iterator[str]] = lambda _: iter(()),
    ) -> T:
        if self._in_progress is None:
            return compute()
        first_attempt = len(self.attempts.attempts)
        # Stage fingerprints bind the request to its upstream data. The attempt
        # position prevents replaying IDs over a changed prefix of calls.
        fingerprint = self._key((key, first_attempt))
        path = self._in_progress / f"{fingerprint}.json"
        saved = self._load(path, fingerprint, result_type, artifacts)
        if saved is not None:
            result, attempts, _ = saved
            self.attempts.attempts.extend(attempts)
            self._reused_items += 1
            return result
        result = compute()
        write_atomic_json(path, {
            "fingerprint": fingerprint, "generation": fingerprint, "result": asdict(result),
            "attempts": [asdict(item) for item in self.attempts.attempts[first_attempt:]],
        })
        return result

    def run(
        self, stage: str, result_type: type[T], settings: object, compute: Callable[[], T],
        artifacts: Callable[[T], Iterator[str]],
    ) -> T:
        path = self.directory / f"{stage}.json"
        fingerprint = self._fingerprint(settings)
        journal = self.directory / "in-progress" / stage / fingerprint
        label = "scripts" if stage == "legacy-scripts" else stage
        saved = self._load(path, fingerprint, result_type, artifacts)
        if saved is not None:
            result, attempts, self.identity = saved
            self.attempts.attempts.extend(attempts)
            if self.progress is not None:
                self.progress(f"[{label}] Reused saved checkpoint.")
            return result
        if self.progress is not None and stage != "legacy-scripts":
            action = "Resuming saved work." if any(journal.glob("*.json")) else "Starting."
            self.progress(f"[{label}] {action}")
        path.unlink(missing_ok=True)
        first_attempt = len(self.attempts.attempts)
        self._in_progress, self._reused_items = journal, 0
        try:
            result = compute()
        finally:
            self._in_progress = None
        generation = uuid4().hex
        write_atomic_json(path, {
            "fingerprint": fingerprint,
            "generation": generation,
            "result": asdict(result) if is_dataclass(result) else [asdict(item) for item in result],
            "attempts": [asdict(item) for item in self.attempts.attempts[first_attempt:]],
        })
        if journal.is_dir():
            rmtree(journal, ignore_errors=True)
        self.identity = generation
        if self.progress is not None:
            if stage == "legacy-scripts":
                self.progress("[scripts] Reused compatible legacy artifacts.")
            else:
                unit = "item" if self._reused_items == 1 else "items"
                reused = f" Reused {self._reused_items} saved {unit}." if self._reused_items else ""
                self.progress(f"[{label}] Completed.{reused}")
        return result
