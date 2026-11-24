"""Publish normalized section audio and verify the chapter-marked lesson MP3."""

import json
import re
import subprocess
import wave
from collections.abc import Callable, Sequence
from dataclasses import replace
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from ..models import ChapterMarker, NarrationScript, ScriptSection, SectionAudio, TargetAudio, TargetSpeechQA

SAMPLE_RATE = 48_000
TARGET_LUFS = -16
LUFS_TOLERANCE = 1
CHAPTER_TOLERANCE_SECONDS = 0.08
ATTRIBUTION = "AI-generated narration"
AUDIO_EXPORT_POLICY_VERSION = 1
LOUDNORM = f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11"
PCM = ["-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le"]
MP3 = ["-c:a", "libmp3lame", "-b:a", "96k", "-id3v2_version", "3"]
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-y"]
RunMedia = Callable[[list[str]], subprocess.CompletedProcess[str]]


class AudioExportError(Exception):
    """An audio tool or its output could not be used."""


def run_media(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=True, timeout=600)
    except FileNotFoundError:
        raise AudioExportError(f"Install {command[0]} and ensure it is on PATH.") from None
    except subprocess.TimeoutExpired:
        raise AudioExportError(f"{command[0]} timed out.") from None
    except subprocess.CalledProcessError as error:
        last = next((line for line in reversed(error.stderr.splitlines()) if line.strip()), "")
        raise AudioExportError(f"{command[0]} failed: {last}" if last else f"{command[0]} failed.") from None


def _probe(path: Path, run: RunMedia) -> tuple[float, dict]:
    output = run(["ffprobe", "-v", "error", "-show_format", "-show_chapters", "-of", "json", str(path)])
    try:
        data = json.loads(output.stdout)
        duration = float(data["format"]["duration"])
        if not isfinite(duration) or duration <= 0 or data["format"]["format_name"] != "mp3":
            raise ValueError
        return duration, data
    except (ValueError, TypeError, KeyError):
        raise AudioExportError("ffprobe did not return a playable MP3 duration.") from None


def _loudness(path: Path, run: RunMedia) -> dict[str, float]:
    result = run([*FFMPEG, "-i", str(path), "-af", f"{LOUDNORM}:print_format=json", "-f", "null", "-"])
    for match in reversed(re.findall(r"\{[^{}]*\}", result.stderr)):
        if '"input_i"' in match:
            fields = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            try:
                data = json.loads(match)
                return {field: float(data[field]) for field in fields}
            except (ValueError, TypeError, KeyError):
                break
    raise AudioExportError("FFmpeg did not return loudness measurements.")


def _concat_chunks(paths: tuple[Path, ...], destination: Path, run: RunMedia) -> None:
    inputs = [argument for path in paths for argument in ("-i", str(path))]
    graph = [
        f"[{index}:a]aformat=sample_fmts=s16:sample_rates={SAMPLE_RATE}:channel_layouts=mono[a{index}]"
        for index in range(len(paths))
    ]
    streams = "".join(f"[a{index}]" for index in range(len(paths)))
    graph.append(f"{streams}concat=n={len(paths)}:v=0:a=1[out]")
    run([*FFMPEG, *inputs, "-filter_complex", ";".join(graph), "-map", "[out]", *PCM, str(destination)])


def assemble_section(
    section: ScriptSection, paths: tuple[Path, ...], destination: Path, pcm_track: Path, run: RunMedia = run_media,
) -> SectionAudio:
    """Normalize accepted chunks in playback order and measure the encoded section."""
    if not paths:
        raise AudioExportError("The section has no accepted speech candidates.")
    raw = pcm_track.with_suffix(".raw.wav")
    _concat_chunks(paths, raw, run)
    try:
        loudness = _loudness(raw, run)
    except AudioExportError:
        loudness = {}
    if loudness and all(isfinite(value) for value in loudness.values()):
        measured = ":".join(f"{option}={loudness[field]}" for option, field in (
            ("measured_I", "input_i"), ("measured_TP", "input_tp"), ("measured_LRA", "input_lra"),
            ("measured_thresh", "input_thresh"), ("offset", "target_offset"),
        ))
        run([*FFMPEG, "-i", str(raw), "-af", f"{LOUDNORM}:{measured}:linear=true", *PCM, str(pcm_track)])
    else:
        raw.replace(pcm_track)

    with wave.open(str(pcm_track), "rb") as audio:
        frames = audio.getnframes()
    if frames <= 0:
        raise AudioExportError("The assembled section contains no samples.")
    run([*FFMPEG, "-i", str(pcm_track), *MP3, "-metadata", f"comment={ATTRIBUTION}", str(destination)])
    duration, _ = _probe(destination, run)
    reason = None
    try:
        integrated = _loudness(destination, run)["input_i"]
    except AudioExportError as error:
        integrated, reason = None, str(error)
    if integrated is None or not isfinite(integrated):
        integrated = None
        status = "unavailable"
        reason = reason or "Integrated loudness is unavailable for silence or audio below the measurement gate."
    elif abs(integrated - TARGET_LUFS) > LUFS_TOLERANCE:
        status = "outside_target"
        reason = f"Encoded section is outside {TARGET_LUFS} ± {LUFS_TOLERANCE} LUFS."
    else:
        status = "within_target"
    return SectionAudio(section.section_id, _section_title(section), duration_seconds=duration, pcm_frames=frames,
                        loudness_lufs=integrated, loudness_status=status, loudness_reason=reason)


def build_chapter_markers(sections: Sequence[SectionAudio]) -> tuple[ChapterMarker, ...]:
    """Use cumulative PCM frames before the single full-track MP3 encode."""
    markers = []
    frames = 0
    for section in sections:
        if not section.pcm_frames:
            continue
        start = round(frames * 1000 / SAMPLE_RATE)
        frames += section.pcm_frames
        end = round(frames * 1000 / SAMPLE_RATE)
        if end <= start:
            raise AudioExportError(f"Section {section.section_id} is too short for MP3 chapter timing.")
        markers.append(ChapterMarker(section.section_id, section.title, start, end))
    return tuple(markers)


def _section_title(section: ScriptSection) -> str:
    title = " ".join(" ".join(chunk.text.split()) for chunk in section.chunks if chunk.policy == "heading")
    return title or f"Section {section.section_id}"


def _chapter_metadata(markers: tuple[ChapterMarker, ...]) -> str:
    blocks = [";FFMETADATA1"]
    for marker in markers:
        title = re.sub(r"([\\=;#])", r"\\\1", marker.title)
        blocks.extend(("[CHAPTER]", "TIMEBASE=1/1000", f"START={marker.start_ms}", f"END={marker.end_ms}",
                       f"title={title}"))
    return "\n".join(blocks) + "\n"


def _combine_pcm(paths: list[Path], destination: Path) -> None:
    with wave.open(str(destination), "wb") as combined:
        combined.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for path in paths:
            with wave.open(str(path), "rb") as section:
                if (section.getnchannels(), section.getsampwidth(), section.getframerate()) != (1, 2, SAMPLE_RATE):
                    raise AudioExportError("The normalized section format is incompatible with lesson assembly.")
                for block in iter(lambda: section.readframes(SAMPLE_RATE), b""):
                    combined.writeframesraw(block)


def _verify_chapters(path: Path, expected: tuple[ChapterMarker, ...], run: RunMedia) -> float:
    duration, data = _probe(path, run)
    try:
        tags = {key.casefold(): value for key, value in data["format"]["tags"].items()}
        if tags.get("comment") != ATTRIBUTION:
            raise AudioExportError("The lesson MP3 lacks AI-generated attribution.")
        last_start = -1.0
        for actual, marker in zip(data["chapters"], expected, strict=True):
            start, end = float(actual["start_time"]), float(actual["end_time"])
            if (actual["tags"]["title"] != marker.title or not last_start < start < end
                    or abs(start - marker.start_ms / 1000) > CHAPTER_TOLERANCE_SECONDS
                    or abs(end - marker.end_ms / 1000) > CHAPTER_TOLERANCE_SECONDS):
                raise AudioExportError("Lesson chapter titles, order or timing did not match the exported sections.")
            last_start = start
        if abs(duration - expected[-1].end_ms / 1000) > CHAPTER_TOLERANCE_SECONDS:
            raise AudioExportError("Lesson MP3 duration did not match the chapter timeline.")
    except (ValueError, TypeError, KeyError):
        raise AudioExportError("ffprobe did not return the expected chapter metadata.") from None
    return duration


def export_audio(
    script: NarrationScript, qa: TargetSpeechQA, out: Path, run: RunMedia = run_media,
) -> TargetAudio:
    """Publish available verified chunks in script order and encode the combined lesson once."""
    language = script.language
    out = out.resolve()
    narration = out / language / "narration"
    narration.mkdir(parents=True, exist_ok=True)
    lesson_path = out / language / f"lesson.{language}.mp3"
    work = out / language / "work"
    work.mkdir(parents=True, exist_ok=True)
    qa_sections = {section.section_id: section for section in qa.sections}
    sections = []
    tracks = []
    markers = ()
    duration = None
    reason = None
    exported_characters = 0
    with TemporaryDirectory(prefix="export-", dir=work) as directory:
        scratch = Path(directory)
        for section in script.sections:
            section_qa = qa_sections.get(section.section_id)
            reviews = {chunk.chunk_id: chunk for chunk in section_qa.chunks} if section_qa else {}
            paths = []
            missing = []
            section_characters = 0
            for chunk in section.chunks:
                review = reviews.get(chunk.id)
                candidates = review.accepted_candidates if review and review.status == "accepted" else ()
                chunk_paths = tuple(out / candidate.audio_path for candidate in candidates if candidate.audio_path)
                # A split repair is usable only when all accepted parts are available.
                if (review is None or not chunk_paths or len(chunk_paths) != len(review.accepted_candidate_ids)
                        or not all(path.is_file() for path in chunk_paths)):
                    missing.append(chunk.id)
                    continue
                paths.extend(chunk_paths)
                section_characters += review.reference_characters

            destination = narration / f"{section.section_id}.mp3"
            omission_reason = (
                f"Omitted chunks without usable verified audio: {', '.join(missing)}." if missing else None
            )
            if not paths:
                skipped = omission_reason or section.reason or "No script chunks."
                result = SectionAudio(section.section_id, _section_title(section), loudness_reason=skipped,
                                      reason=skipped, omitted_chunk_ids=tuple(missing))
            else:
                pcm_track = scratch / f"{section.section_id}.wav"
                encoded = scratch / destination.name
                try:
                    result = assemble_section(section, tuple(paths), encoded, pcm_track, run)
                    encoded.replace(destination)
                except (AudioExportError, OSError, wave.Error, EOFError) as error:
                    result = SectionAudio(section.section_id, _section_title(section), loudness_reason=str(error),
                                          reason=str(error), omitted_chunk_ids=tuple(chunk.id for chunk in section.chunks))
                else:
                    result = replace(result, audio_path=destination.relative_to(out).as_posix(),
                                     reason=omission_reason, omitted_chunk_ids=tuple(missing))
                    tracks.append(pcm_track)
                    exported_characters += section_characters
            sections.append(result)

        if tracks:
            try:
                markers = build_chapter_markers(sections)
                combined, tags, encoded = scratch / "lesson.wav", scratch / "chapters.txt", scratch / lesson_path.name
                _combine_pcm(tracks, combined)
                tags.write_text(_chapter_metadata(markers), encoding="utf-8")
                run([*FFMPEG, "-i", str(combined), "-f", "ffmetadata", "-i", str(tags), "-map", "0:a",
                     "-map_metadata", "1", "-map_chapters", "1", *MP3, "-metadata", f"comment={ATTRIBUTION}",
                     str(encoded)])
                duration = _verify_chapters(encoded, markers, run)
                encoded.replace(lesson_path)
            except (AudioExportError, OSError, wave.Error, EOFError) as error:
                reason = str(error)
        else:
            reason = "No section could be exported with verified speech."

    exported = {section.audio_path for section in sections if section.audio_path}
    for path in narration.glob("s*.mp3"):
        if re.fullmatch(r"s\d+\.mp3", path.name) and path.relative_to(out).as_posix() not in exported:
            path.unlink()
    total_characters = qa.metrics.total_reference_characters
    if reason is not None:
        lesson_path.unlink(missing_ok=True)
        return TargetAudio(language, "failed", tuple(sections), reason=reason,
                           coverage=0.0 if total_characters else None)
    coverage = exported_characters / total_characters if total_characters else None
    missed = qa.status != "completed" or any(
        section.coverage_status != "narrated" or section.loudness_status != "within_target" for section in sections
    )
    return TargetAudio(language, "completed_with_misses" if missed else "completed", tuple(sections),
                       lesson_path.relative_to(out).as_posix(), duration, markers,
                       "Some narration is missing or has quality findings." if missed else None, coverage)
