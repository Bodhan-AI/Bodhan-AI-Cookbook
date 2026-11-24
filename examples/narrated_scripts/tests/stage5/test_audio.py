import json
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest

from backend import runner
from backend.audio.export import (
    ATTRIBUTION, CHAPTER_TOLERANCE_SECONDS, SAMPLE_RATE, build_chapter_markers, export_audio, run_media,
)
from backend.audio.speech_qa import compare_speech, summarize_speech
from backend.models import (
    ChunkSpeechQA, NarrationScript, NarrationScripts, ScriptChunk, ScriptSection, SectionAudio, SectionSpeechQA,
    SpeechCandidate, SpeechQAAttempt, SpeechQARun, TargetSpeechQA,
)
from backend.report import AttemptLog, RunReport


def test_chapter_markers_use_script_order_and_exact_sample_boundaries():
    sections = tuple(SectionAudio(f"s{number:03d}", str(number), pcm_frames=SAMPLE_RATE + 3)
                     for number in range(1, 13))
    skipped = SectionAudio("s003a", "Skipped", reason="No accepted speech.")
    markers = build_chapter_markers((*sections[:3], skipped, *sections[3:]))

    assert [marker.section_id for marker in markers] == [section.section_id for section in sections]
    # Three extra samples per section cross the half-millisecond rounding boundary by section ten.
    assert [marker.start_ms for marker in markers] == [
        0, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9001, 10001, 11001,
    ]
    assert [marker.end_ms for marker in markers[:-1]] == [marker.start_ms for marker in markers[1:]]
    assert markers[-1].end_ms == 12001


def _write_clip(path: Path, *, amplitude: int, rate: int = 24_000, seconds: float = 1.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = round(seconds * rate)
    frames = [round(amplitude * math.sin(2 * math.pi * 440 * index / rate)) for index in range(count)]
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack(f"<{count}h", *frames))


def _qa_for(section, paths, *, unavailable=()):
    reviews = []
    for chunk in section.chunks:
        path = paths.get(chunk.id)
        attempt_id = f"{chunk.id}-test"
        if chunk.id in unavailable:
            reviews.append(ChunkSpeechQA(chunk.id, len(chunk.text), (), (), "unavailable",
                                         "Endpoint returned HTTP 429."))
            continue
        if path is None:
            reviews.append(ChunkSpeechQA(chunk.id, len(chunk.text), (), (), "exhausted", "Test QA miss."))
            continue
        candidate = SpeechCandidate(
            chunk.id, section.section_id, "Kavya", len(chunk.text), "ok", (attempt_id,), attempt_id, path,
            "wav", 1.5,
        )
        comparison = compare_speech(chunk.text, chunk.text)
        attempt = SpeechQAAttempt("original", candidate, comparison, (), "passed")
        reviews.append(ChunkSpeechQA(chunk.id, comparison.reference_characters, (attempt,), (attempt_id,), "accepted"))
    chunks = tuple(reviews)
    reason = "Test QA miss." if any(chunk.status != "accepted" for chunk in chunks) else section.reason
    return SectionSpeechQA(section.section_id, chunks, summarize_speech(chunks), reason)


def _section(number, heading, *, extra=False):
    section_id = f"s{number:03d}"
    chunks = [ScriptChunk(f"{section_id}-c01", section_id, ("p001-b001",), (), heading, "heading")]
    if extra:
        chunks.append(ScriptChunk(f"{section_id}-c02", section_id, ("p001-b002",), (), "Next sentence.", "paragraph"))
    return ScriptSection(section_id, tuple(chunks), ())


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg and ffprobe are required")
def test_export_measures_encoded_loudness_and_probes_chapters(tmp_path):
    first = _section(1, "அறிமுகம் #1; A=B", extra=True)
    partial_section = _section(2, "Partial section")
    figure, prose, closing = (
        ScriptChunk(f"s002-c{index:02d}", "s002", (f"p002-b{index:03d}",), (), text, policy)
        for index, text, policy in (
            (2, "Figure caption.", "figure_caption"),
            (3, "This paragraph stays in the script.", "paragraph"),
            (4, "This paragraph is verified.", "paragraph"),
        )
    )
    partial_section = ScriptSection(partial_section.section_id, (*partial_section.chunks, figure, prose, closing), ())
    silence = _section(10, "Last section")
    unusable = _section(11, "Missing clip")
    skipped = ScriptSection("s012", (), (), "OCR text was quarantined.")
    script = NarrationScript("ta", None, 300, "completed_with_misses",
                             (first, partial_section, silence, unusable, skipped))
    paths = {
        first.chunks[0].id: "ta/work/audio/first.wav",
        first.chunks[1].id: "ta/work/audio/second.wav",
        partial_section.chunks[0].id: "ta/work/audio/partial-heading.wav",
        closing.id: "ta/work/audio/partial-closing.wav",
        silence.chunks[0].id: "ta/work/audio/silence.wav",
        unusable.chunks[0].id: "ta/work/audio/missing.wav",
    }
    _write_clip(tmp_path / paths[first.chunks[0].id], amplitude=3500, rate=24_000)
    _write_clip(tmp_path / paths[first.chunks[1].id], amplitude=3500, rate=16_000)
    _write_clip(tmp_path / paths[partial_section.chunks[0].id], amplitude=3500)
    _write_clip(tmp_path / paths[closing.id], amplitude=3500)
    _write_clip(tmp_path / paths[silence.chunks[0].id], amplitude=0)
    obsolete = tmp_path / "ta/narration/s011.mp3"
    obsolete.parent.mkdir()
    obsolete.write_bytes(b"prior artifact")
    sections = tuple(_qa_for(section, paths, unavailable=(figure.id,)) for section in script.sections)
    metrics = summarize_speech(tuple(chunk for section in sections for chunk in section.chunks))
    qa = TargetSpeechQA("ta", 0.15, "completed_with_misses", sections, metrics)
    artifact = tmp_path / "ta/narration_script.ta.json"
    artifact.write_text('{"attempts": []}', encoding="utf-8")
    markdown = tmp_path / "ta/lesson.ta.md"
    markdown.write_text(prose.text, encoding="utf-8")

    [result] = runner._run_audio(NarrationScripts((script,)), SpeechQARun((qa,)), tmp_path).targets

    assert result.status == "completed_with_misses"
    tone, partial, silent, no_clip, missing = result.sections
    assert tone.loudness_status == "within_target"
    assert tone.loudness_lufs == pytest.approx(-16, abs=1)
    assert tone.duration_seconds == pytest.approx(3.0, abs=CHAPTER_TOLERANCE_SECONDS)
    assert silent.loudness_status == "unavailable" and silent.loudness_lufs is None and silent.loudness_reason
    assert partial.coverage_status == "partial" and (tmp_path / partial.audio_path).is_file()
    assert partial.duration_seconds == pytest.approx(3.0, abs=CHAPTER_TOLERANCE_SECONDS)
    assert partial.omitted_chunk_ids == (figure.id, prose.id)
    assert no_clip.audio_path is None and no_clip.omitted_chunk_ids == (unusable.chunks[0].id,)
    assert not obsolete.exists()
    assert missing.audio_path is None and missing.reason == skipped.reason
    assert [marker.section_id for marker in result.chapters] == ["s001", "s002", "s010"]

    probe = run_media(["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json",
                       str(tmp_path / result.audio_path)])
    metadata = json.loads(probe.stdout)
    assert metadata["format"]["tags"]["comment"] == ATTRIBUTION
    assert [item["tags"]["title"] for item in metadata["chapters"]] == [
        "அறிமுகம் #1; A=B", "Partial section", "Last section",
    ]
    starts = [float(item["start_time"]) for item in metadata["chapters"]]
    assert starts == pytest.approx([0, 3.0, 6.0], abs=CHAPTER_TOLERANCE_SECONDS)
    assert float(metadata["format"]["duration"]) == pytest.approx(7.5, abs=CHAPTER_TOLERANCE_SECONDS)

    script_with_audio = json.loads(artifact.read_text(encoding="utf-8"))
    expected = (metrics.covered_reference_characters - sections[3].metrics.covered_reference_characters
                ) / metrics.total_reference_characters
    assert script_with_audio["audio"]["coverage"] == expected
    assert script_with_audio["audio"]["speech_qa_coverage"] == metrics.coverage
    assert script_with_audio["sections"][0]["speech_qa"] == "ta/speech-qa.json"
    assert script_with_audio["sections"][0]["chunks"][0]["accepted_candidate_ids"]
    assert script_with_audio["sections"][1]["omitted_chunk_ids"] == [figure.id, prose.id]
    assert [chunk["text"] for chunk in script_with_audio["sections"][1]["chunks"]] == [
        chunk.text for chunk in partial_section.chunks
    ]
    assert markdown.read_text(encoding="utf-8") == prose.text

    report = RunReport((), ("ta",), "hosted", AttemptLog())
    report.record_audio(result)
    [tamil] = report.snapshot(0)["languages"]
    assert tamil["audio_coverage"] == expected
    assert tamil["sections"][1]["narration_coverage"]["status"] == "partial"
    assert tamil["sections"][1]["narration_coverage"]["omitted_chunk_ids"] == [figure.id, prose.id]


def test_missing_verified_sections_produce_no_lesson(tmp_path):
    script = NarrationScript("ta", None, 300, "completed", (_section(1, "Heading"),))
    qa_section = _qa_for(script.sections[0], {})
    qa = TargetSpeechQA("ta", 0.15, "completed_with_misses", (qa_section,), qa_section.metrics)
    result = export_audio(script, qa, tmp_path, lambda _: pytest.fail("Media tool should not be called."))
    assert result.status == "failed"
    assert result.reason and result.sections[0].reason
    assert result.audio_path is None and result.chapters == ()
    assert result.coverage == 0
