import io
import json
import wave
from pathlib import Path

import pytest
from PIL import Image

from backend import checkpoints, inference, runner
from backend.inference import Inference, ProviderError
from backend.models import Page, SectionAudio, SourceBlock, SourceChapter, SourceSection, TargetAudio, Usage
from backend.report import AttemptLog, RunReport
from backend.translation.provider import ModelTranslation, TranslationResponse
from backend.translation.segments import plan_units
from backend.translation.translate import translate_plan
from bodhan_lesson import main

HINDI = "ज्यामिति की परिभाषा।"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    for name in ("BODHAN_TRANSLATE_GGUF_URL", "BODHAN_SPEECH_MAX_CHARS",
                 "BODHAN_SPEECH_CER_THRESHOLD", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runner, "which", lambda command: command)
    scans, out = tmp_path / "scans", tmp_path / "out"
    scans.mkdir()
    for number in (1, 2):
        with Image.new("RGB", (200, 300), "white") as image:
            image.save(scans / f"p{number}.png")

    def recognize(self, png, options):
        return {"markdown": "# Geometry\n\nAn angle has two rays."}

    def translate(self, text, target):
        if target == "ta":
            raise ProviderError("local_failure", "The test Tamil translation is unavailable.")
        return ModelTranslation(HINDI)

    def speak(self, text, voice):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 6000)
        return buffer.getvalue()

    def export(script, qa, root):
        if qa.status == "failed":
            return TargetAudio(script.language, "failed", tuple(
                SectionAudio(section.section_id, "Geometry", reason="No verified speech.")
                for section in script.sections
            ), reason=qa.reason)
        sections = []
        for section in script.sections:
            if not section.chunks:
                sections.append(SectionAudio(section.section_id, "Geometry", reason=section.reason))
                continue
            relative = f"{script.language}/narration/{section.section_id}.mp3"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio export double")
            sections.append(SectionAudio(section.section_id, "Geometry", relative, 0.5,
                                         loudness_lufs=-19, loudness_status="outside_target",
                                         loudness_reason="Measured loudness is outside tolerance."))
        relative = f"{script.language}/lesson.{script.language}.mp3"
        (root / relative).write_bytes(b"audio export double")
        return TargetAudio(script.language, "completed_with_misses", tuple(sections), relative, 1.0)

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    monkeypatch.setattr(runner.LocalSpeak, "__call__", speak)
    monkeypatch.setattr(runner.LocalTranscribe, "__call__", lambda self, audio, language: HINDI)
    monkeypatch.setattr(runner, "export_audio", export)

    def run(languages="hi", *options):
        return main(["--input", str(scans), "--langs", languages, "--out", str(out),
                     "--source-lang", "en", *options])

    return run, out


def test_failed_tamil_leaves_hindi_and_audit_links(pipeline, capsys):
    run, out = pipeline
    assert run("ta,hi") == 1
    report = read_json(out / "report.json")
    tamil, hindi = report["languages"]
    assert report["status"] == "partial"
    assert tamil["language"] == "ta" and tamil["status"] == "failed"
    assert tamil["sections"][0]["narration_coverage"]["reason"]
    assert hindi["status"] == "completed_with_misses"
    assert (out / hindi["lesson_audio_path"]).is_file()
    section = next(section for section in hindi["sections"] if section["script"]["chunks"])
    assert section["source_block_ids"][0] in report["pages"][0]["ordered_block_ids"]
    attempts = {attempt["id"]: attempt for attempt in report["attempts"]}
    assert any(attempts[attempt_id]["service"] == "indic-translate"
               for attempt_id in section["translation_attempt_ids"])
    assert any(attempts[attempt_id]["service"] == "indic-transcribe"
               for chunk in section["content_fidelity"]["chunks"] for attempt_id in chunk["attempt_ids"])
    assert section["content_fidelity"]["metrics"]["cer"] == 0
    assert section["loudness"]["measured_lufs"] == -19
    assert section["loudness"]["reason"]
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    assert any(event["event"] == "section_outcome" and event["language"] == "hi" for event in events)
    assert any(event["event"] == "attempt" and event["stage"] == "translate" for event in events)


@pytest.mark.parametrize("unreadable_file", [False, True])
def test_failed_page_does_not_stop_later_pages(pipeline, monkeypatch, unreadable_file):
    run, out = pipeline
    original = runner.LocalOCR.__call__
    page_count = 0

    def recognize(self, png, options):
        nonlocal page_count
        page_count += 1
        if page_count == 1 and not unreadable_file:
            raise ProviderError("local_failure", "The first test page could not be read.")
        return original(self, png, options)

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    if unreadable_file:
        digest = checkpoints.file_digest
        open_image = Image.open

        def file_digest(path):
            if path.name == "p1.png":
                raise PermissionError(path)
            return digest(path)

        def open_page(path, *args, **kwargs):
            if isinstance(path, Path) and path.name == "p1.png":
                raise PermissionError(path)
            return open_image(path, *args, **kwargs)

        monkeypatch.setattr(checkpoints, "file_digest", file_digest)
        monkeypatch.setattr(Image, "open", open_page)

    assert run() == 0
    report = read_json(out / "report.json")
    first, second = report["pages"]
    assert first["status"] == "failed" and first["reason"]
    assert bool(first["attempt_ids"]) is not unreadable_file
    assert second["status"] == "ok" and second["ordered_block_ids"]
    assert report["quality_targets"]["structure_capture"]["failed_page_ids"] == ["p001"]
    assert (out / "hi/lesson.hi.mp3").is_file()


def test_shared_attempt_ids_retries_and_oom_fallback_costs(monkeypatch):
    ticks = iter(range(20))
    monkeypatch.setattr(inference, "perf_counter", lambda: next(ticks) / 2)
    page = Page(1, Path("scan.png"))
    log = AttemptLog()
    router = Inference(log, wait=lambda _: None)
    ocr_usage = Usage(1, "image", 0.20, 0.20, "estimated", "One submitted image per request")

    def oom():
        raise RuntimeError("CUDA out of memory")

    requests = 0

    def hosted_ocr():
        nonlocal requests
        requests += 1
        if requests == 1:
            raise ProviderError("timeout", "Timed out.", retryable=True)
        return "OCR"

    router.run_indic(mode="local-first", model="indic-ocr", entity_ids=("p001",),
                     local=oom, release_local=lambda: None, hosted=hosted_ocr, hosted_usage=ocr_usage)
    block = SourceBlock("p001-b001", "paragraph", ("p001-b001",), HINDI)
    plan = plan_units(SourceChapter("hi", (SourceSection("s001", None, None, None, (block,)),)))
    targets = ("ta", "bn")

    def translate(unit_id, source, target, text):
        translated = router.run_remote(
            "indic-translate", "indic-translate", (unit_id, f"{source}->{target}"),
            lambda: {"en": "Geometry", "ta": "வடிவியல்", "bn": "জ্যামিতি"}[target],
            Usage(100, "output_token", 0.00002, 0.002, "measured"),
        )
        return TranslationResponse(translated, (log.attempts[-1].id,))

    translation = translate_plan(plan, targets, translate)
    router.run_remote("llm", "test", ("p001",), lambda: "ok",
                      Usage(10, "token", None, None, "measured", "Price unavailable."))
    audit = RunReport((page,), targets, "local-first", log)
    audit.record_stage("segments", plan, 0, False)
    audit.record_stage("translate", translation, 0, False)
    report = audit.snapshot(0.25)
    tamil, bengali = report["languages"]
    shared = set(tamil["sections"][0]["translation_attempt_ids"]) & set(
        bengali["sections"][0]["translation_attempt_ids"]
    )
    assert len(shared) == 1
    pivot_attempt = next(attempt for attempt in report["attempts"] if attempt["id"] in shared)
    assert pivot_attempt["entity_ids"][-1] == "hi->en"
    assert report["wall_time_seconds"] == 0.25
    assert report["attempt_elapsed_ms"] == 3500
    assert report["attempt_elapsed_ms"] > report["wall_time_seconds"] * 1000
    assert len(report["attempts"]) == 7
    local, retry, successor, *_ = report["attempts"]
    assert local["error_category"] == "cuda_oom"
    assert local["usage"]["cost_inr"] == 0
    assert retry["result"] == "failed" and successor["number"] == 2
    assert retry["fallback_reason"] == successor["fallback_reason"] == local["reason"]
    assert report["backend"]["hosted_fallback_attempts"] == 2
    hosted = report["cost"]["hosted"]
    assert hosted["cost_inr"] is None and hosted["known_cost_inr"] == pytest.approx(0.406)
    translation_cost = next(row for row in report["cost"]["by_service"] if row["service"] == "indic-translate")
    assert translation_cost["quantity"] == 300 and translation_cost["attempt_count"] == 3
    ocr_cost = next(row for row in report["cost"]["by_service"]
                    if row["service"] == "indic-ocr" and row["backend"] == "hosted")
    assert ocr_cost["quantity"] == 2 and ocr_cost["basis"] == "estimated"


def test_later_handled_failure_keeps_report_and_prior_artifacts(pipeline, monkeypatch):
    run, out = pipeline

    def stop(*args):
        intermediate = read_json(out / "report.json")
        assert intermediate["status"] == "running"
        assert intermediate["stages"]["speech"]["status"] == "completed"
        assert intermediate["languages"][0]["translation"]["status"] == "completed"
        assert intermediate["attempts"]
        raise OSError("Speech QA storage unavailable")

    monkeypatch.setattr(runner, "_run_speech_qa", stop)
    with pytest.raises(SystemExit) as failure:
        run()
    assert failure.value.code != 0
    report = read_json(out / "report.json")
    assert report["status"] == "failed"
    assert report["failure"]["stage"] == "speech-qa"
    assert report["languages"][0]["status"] == "failed"
    assert report["quality_targets"]["structure_capture"]["observed_ocr_blocks"] > 0
    assert (out / "source/lesson.src.md").is_file()
    assert (out / "hi/chunks.json").is_file()


def test_full_command_reuses_checkpoints_and_distinguishes_wall_time(pipeline, monkeypatch):
    run, out = pipeline
    assert run("hi", "--until", "ocr") == 0

    def unused_ocr(*args):
        pytest.fail("Saved OCR checkpoint should be reused.")

    times = iter((0.0, 1.0))
    monkeypatch.setattr(runner, "perf_counter", lambda: next(times, 1.0))
    monkeypatch.setattr(runner.LocalOCR, "__call__", unused_ocr)
    assert run() == 0
    report = read_json(out / "report.json")
    assert report["stages"]["ocr"]["reused"] is True
    assert report["wall_time_seconds"] == 1.0
    ocr = [attempt for attempt in report["attempts"] if attempt["service"] == "indic-ocr"]
    assert ocr and all(attempt["reused"] for attempt in ocr)
    assert report["attempt_elapsed_ms"] == sum(attempt["elapsed_ms"] for attempt in report["attempts"])
    assert report["invocation_attempt_elapsed_ms"] == sum(
        attempt["elapsed_ms"] for attempt in report["attempts"] if not attempt["reused"]
    )
