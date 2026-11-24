import io
import json
import wave
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from backend import runner
from backend.translation.provider import ModelTranslation
from bodhan_lesson import main


def write_scan(path, color="white"):
    with Image.new("RGB", (200, 300), color) as image:
        image.save(path)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    for name in ("BODHAN_TRANSLATE_GGUF_URL", "BODHAN_SPEECH_MAX_CHARS",
                 "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    write_scan(scans / "p1.png")
    out = tmp_path / "out"
    calls = []

    def recognize(self, png, options):
        calls.append(("ocr", None))
        return {"markdown": "# Geometry\n\nAn angle has two rays."}

    def translate(self, text, target):
        calls.append(("translate", target))
        return ModelTranslation("ज्यामिति।")

    def speak(self, text, voice):
        calls.append(("speech", voice))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 6000)
        return buffer.getvalue()

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    monkeypatch.setattr(runner.LocalSpeak, "__call__", speak)

    def run(stage, *options, language="hi", source="en"):
        return main(["--input", str(scans), "--langs", language, "--out", str(out),
                     "--source-lang", source, "--until", stage, *options])

    return run, calls, scans, out


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_later_checkpoint_reuses_earlier_work_and_attempt_ids(pipeline):
    run, calls, _, out = pipeline
    assert run("translate") == 0
    before = calls[:]
    assert {service for service, _ in before} == {"ocr", "translate"}
    segments = read_json(out / "hi/segments.json")
    assert run("scripts") == 0
    assert calls == before
    assert run("speech") == 0
    first = read_json(out / "hi/chunks.json")
    assert calls[len(before):] == [("speech", "Kavya")] * len(first["chunks"])
    previous_id = int(segments["attempts"][-1]["id"][1:])
    assert first["attempts"][0]["id"] == f"a{previous_id + 1:06d}"

    calls.clear()
    assert run("speech") == 0
    assert calls == []
    assert read_json(out / "hi/chunks.json") == first


def test_existing_stage4_and_stage5_outputs_can_be_adopted(pipeline, tmp_path):
    run, calls, _, out = pipeline
    assert run("scripts") == 0
    state = out / "work/checkpoints"
    state.rename(tmp_path / "scripts-state")
    calls.clear()

    assert run("speech") == 0
    assert calls and {service for service, _ in calls} == {"speech"}
    first = read_json(out / "hi/chunks.json")
    state.rename(tmp_path / "speech-state")
    calls.clear()

    assert run("speech") == 0
    assert calls == []
    assert read_json(out / "hi/chunks.json") == first


@pytest.mark.parametrize(("language", "heading", "old", "spoken", "body"), [
    ("ta", "அறிமுகம்", "6.1", "ஆறு புள்ளி ஒன்று", "கோடுகளும் கோணங்களும்."),
    ("hi", "परिचय", "5.1", "पाँच दशमलव एक", "रेखाएँ और कोण।"),
    ("bn", "ভূমিকা", "৫.১", "পাঁচ দশমিক এক", "রেখা ও কোণ।"),
])
def test_number_policy_rebuilds_saved_script_and_synthesis(
    pipeline, monkeypatch, language, heading, old, spoken, body,
):
    run, calls, _, out = pipeline
    build_script = runner.build_script
    speak = runner.LocalSpeak.__call__
    requests = []

    def recognize(self, png, options):
        calls.append(("ocr", None))
        return {"markdown": f"# {old} {heading}\n\n{body}"}

    def speak_recorded(self, text, voice):
        requests.append(text)
        return speak(self, text, voice)

    def build_old_script(*args, **kwargs):
        script = build_script(*args, **kwargs)
        return replace(script, sections=tuple(
            replace(section, chunks=tuple(
                replace(chunk, text=chunk.text.replace(spoken, old), spoken_numbers=())
                for chunk in section.chunks
            ))
            for section in script.sections
        ))

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalSpeak, "__call__", speak_recorded)
    with monkeypatch.context() as legacy:
        legacy.setattr(runner, "SCRIPT_POLICY_VERSION", 2)
        legacy.setattr(runner, "build_script", build_old_script)
        assert run("speech", language=language, source=language) == 0
    assert f"{old} {heading}" in requests
    requests.clear()
    calls.clear()

    assert run("speech", language=language, source=language) == 0
    assert f"{spoken} {heading}" in requests
    assert all(old not in request for request in requests)
    assert {service for service, _ in calls} == {"speech"}
    sections = read_json(out / f"{language}/narration_script.{language}.json")["sections"]
    assert f"{spoken} {heading}" in [chunk["text"] for section in sections for chunk in section["chunks"]]


def test_changed_input_invalidates_previous_ocr_checkpoint(pipeline):
    run, calls, scans, _ = pipeline
    assert run("ocr") == 0
    assert calls == [("ocr", None)]
    write_scan(scans / "p1.png", "teal")

    assert run("ocr") == 0
    assert calls == [("ocr", None), ("ocr", None)]


def test_changed_voice_regenerates_speech_using_existing_translation(pipeline, tmp_path):
    run, calls, _, out = pipeline
    assert run("speech") == 0
    prior = read_json(out / "hi/chunks.json")
    calls.clear()
    voices = tmp_path / "voices.yaml"
    voices.write_text("hi: Amit\n", encoding="utf-8")

    assert run("speech", "--voices", str(voices)) == 0
    updated = read_json(out / "hi/chunks.json")
    assert calls == [("speech", "Amit")] * len(updated["chunks"])
    assert updated["voice"] == "Amit"
    assert all((out / chunk["audio_path"]).exists() for chunk in prior["chunks"])


def test_missing_candidate_reexecutes_speech_without_earlier_model_calls(pipeline):
    run, calls, _, out = pipeline
    assert run("speech") == 0
    artifact = read_json(out / "hi/chunks.json")
    (out / artifact["chunks"][0]["audio_path"]).unlink()
    calls.clear()

    assert run("speech") == 0
    assert calls and {service for service, _ in calls} == {"speech"}
    regenerated = read_json(out / "hi/chunks.json")
    assert all((out / chunk["audio_path"]).is_file() for chunk in regenerated["chunks"])


def test_force_clears_generated_output_and_keeps_unrelated_files(pipeline):
    run, calls, scans, out = pipeline
    assert run("speech") == 0
    artifact = read_json(out / "hi/chunks.json")
    note = out / "work/notes.txt"
    note.write_text("Keep this file", encoding="utf-8")
    calls.clear()

    assert run("ocr", "--force") == 0
    assert calls == [("ocr", None)]
    assert not (out / "hi/chunks.json").exists()
    assert not (out / artifact["chunks"][0]["audio_path"]).exists()
    assert not (out / "work/checkpoints/speech.json").exists()
    assert (out / "source/pages/p001.json").is_file()
    assert (scans / "p1.png").is_file()
    assert note.read_text(encoding="utf-8") == "Keep this file"


def test_force_rejects_output_that_is_also_the_input(pipeline, capsys):
    _, _, scans, _ = pipeline
    with pytest.raises(SystemExit) as failure:
        main(["--input", str(scans), "--langs", "hi", "--out", str(scans),
              "--source-lang", "en", "--until", "ocr", "--force"])
    assert failure.value.code == 2
    assert "would remove the pipeline input" in capsys.readouterr().err
    assert (scans / "p1.png").is_file()


@pytest.mark.parametrize("answer", ["y", "n"])
def test_interactive_resume_choice(pipeline, monkeypatch, capsys, answer):
    run, calls, _, out = pipeline
    assert run("speech") == 0
    calls.clear()
    capsys.readouterr()
    monkeypatch.setattr("bodhan_lesson.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda: answer)

    assert run("ocr") == 0
    progress = capsys.readouterr().err
    assert progress.count("Resume [Y/n]?") == 1
    if answer == "y":
        assert calls == []
        assert "[ocr] Reused saved checkpoint." in progress
        assert (out / "hi/chunks.json").is_file()
    else:
        assert calls == [("ocr", None)]
        assert "[ocr] Starting." in progress and "[ocr] Completed." in progress
        assert not (out / "hi/chunks.json").exists()


def test_interrupted_ocr_reuses_completed_pages(pipeline, monkeypatch, capsys):
    run, calls, scans, out = pipeline
    write_scan(scans / "p2.png", "teal")

    def interrupt_second_page(self, png, options):
        calls.append(("ocr", None))
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {"markdown": "# Geometry\n\nAn angle has two rays."}

    monkeypatch.setattr(runner.LocalOCR, "__call__", interrupt_second_page)
    assert run("ocr") == 130
    assert "Interrupted." in capsys.readouterr().err
    assert (out / "source/pages/p001.json").is_file()
    assert not (out / "work/checkpoints/ocr.json").exists()

    calls.clear()
    assert run("ocr") == 0
    assert calls == [("ocr", None)]
    assert "[ocr] Completed. Reused 1 saved item." in capsys.readouterr().err
    assert (out / "source/pages/p002.json").is_file()


def test_interrupted_translation_keeps_completed_model_responses(pipeline, monkeypatch):
    run, calls, _, out = pipeline
    count = 0

    def interrupt_second_unit(self, text, target):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt
        calls.append(("translate", text))
        return ModelTranslation("ज्यामिति।")

    monkeypatch.setattr(runner.LocalTranslate, "__call__", interrupt_second_unit)
    assert run("translate") == 130
    assert count == 2
    assert (out / "work/checkpoints/ocr.json").is_file()
    assert not (out / "work/checkpoints/translate.json").exists()

    calls.clear()
    assert run("translate") == 0
    assert count == 3
    assert [service for service, _ in calls] == ["translate"]
    assert len(read_json(out / "hi/segments.json")["attempts"]) == 2


def test_interrupted_qa_keeps_transcripts_and_repair_wavs(pipeline, monkeypatch, capsys):
    run, calls, _, out = pipeline
    assert run("speech") == 0
    initial = read_json(out / "hi/chunks.json")
    attempts = 0

    def interrupted_review(self, audio, language):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ""
        if attempts == 2:
            raise KeyboardInterrupt
        return "ज्यामिति।"

    monkeypatch.setattr(runner.LocalTranscribe, "__call__", interrupted_review)
    calls.clear()
    assert run("speech-qa") == 130
    assert calls == [("speech", "Kavya")]
    repair_files = set((out / "hi/work/audio").glob("*.wav"))
    assert len(repair_files) == len(initial["chunks"]) + 1
    capsys.readouterr()

    calls.clear()
    assert run("speech-qa") == 0
    assert calls == []
    assert attempts == 2 + len(initial["chunks"])
    assert set((out / "hi/work/audio").glob("*.wav")) == repair_files
    output = read_json(out / "hi/speech-qa.json")
    assert output["metrics"]["coverage"] == 1
    progress = capsys.readouterr().err
    assert "[speech] Reused saved checkpoint." in progress
    assert "[speech-qa] Resuming saved work." in progress
    assert "[speech-qa] Completed. Reused 2 saved items." in progress
