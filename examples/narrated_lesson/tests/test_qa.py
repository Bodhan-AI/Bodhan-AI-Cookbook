"""Unit tests for Transcribe QA helpers."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest.mock import patch

from bodhan_lesson.qa import (
    CER_FAIL,
    char_error_rate,
    detect_omit,
    detect_repeat,
    evaluate_transcript,
    qa_lesson_audio,
    qa_spoken_lessons,
)


def _write_silent_wav(path: Path, *, seconds: float = 0.4, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = max(1, int(rate * seconds))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * nframes)
    return path


def test_char_error_rate_exact() -> None:
    assert char_error_rate("hello world", "hello world") == 0.0


def test_char_error_rate_one_edit() -> None:
    # "abcd" vs "abxd" → 1/4
    assert abs(char_error_rate("abcd", "abxd") - 0.25) < 1e-9


def test_detect_repeat_consecutive_window() -> None:
    words = "one two three four five six " * 2
    assert detect_repeat("short", words.strip()) is True


def test_detect_omit_short_transcript() -> None:
    ref = "a" * 100
    hyp = "a" * 20
    assert detect_omit(ref, hyp) is True


def test_detect_omit_ignores_spoken_digits_on_short_titles() -> None:
    # "6" spoken as "सिक्स" must not trip omit on short headings.
    assert detect_omit("अध्याय 6", "अध्याय सिक्स") is False


def test_evaluate_high_cer_is_soft_warning() -> None:
    ev = evaluate_transcript("abcdefghijklmnopqrstuvwxyz", "zzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert ev["cer"] > CER_FAIL
    assert f"cer>{CER_FAIL}" in ev["reasons"]
    # CER alone does not hard-fail the section.
    assert ev["failed"] is False


def test_evaluate_fail_on_repeat() -> None:
    words = "alpha beta gamma delta epsilon zeta " * 2
    ev = evaluate_transcript("short", words.strip())
    assert ev["repeat"] is True
    assert ev["failed"] is True


def test_qa_lesson_audio_ok_with_mock_transcribe(tmp_path: Path) -> None:
    lang = "hi"
    narr = tmp_path / lang / "narration"
    audio = _write_silent_wav(narr / "01-sec_001.hi.wav")
    script = {
        "lang": lang,
        "voice": "Kavya",
        "sections": [
            {
                "id": "sec_001",
                "slug": "sec_001",
                "status": "ok",
                "speak_status": "ok",
                "text_for_tts": "नमस्ते कक्षा",
                "audio_path": str(audio),
            }
        ],
        "lesson_mp3": str(tmp_path / lang / f"lesson.{lang}.mp3"),
    }
    lesson_mp3 = tmp_path / lang / f"lesson.{lang}.mp3"
    lesson_mp3.write_bytes(b"ID3fake")
    script_path = tmp_path / lang / f"narration_script.{lang}.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")

    def fake_post(path, **kwargs):  # noqa: ANN001
        assert path == "/v1/audio/transcriptions"
        return {"text": "नमस्ते कक्षा"}

    with patch("bodhan_lesson.qa.loudnorm_mp3", return_value={"status": "skipped"}):
        result = qa_lesson_audio(
            script_path,
            tmp_path,
            lang,
            api_key="test-key",
            post_fn=fake_post,
        )

    assert result["status"] == "ok"
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert result["mean_cer"] == 0.0
    updated = json.loads(script_path.read_text(encoding="utf-8"))
    assert updated["sections"][0]["qa"]["status"] == "ok"


def test_qa_retries_speak_on_omit(tmp_path: Path) -> None:
    lang = "hi"
    narr = tmp_path / lang / "narration"
    audio = _write_silent_wav(narr / "01-sec_001.hi.wav")
    # Long reference so length-based omit can fire on a short first transcript.
    reference = ("exact reference text here with more words to lengthen " * 4).strip()
    script = {
        "lang": lang,
        "voice": "Kavya",
        "sections": [
            {
                "id": "sec_001",
                "slug": "sec_001",
                "status": "ok",
                "speak_status": "ok",
                "text_for_tts": reference,
                "audio_path": str(audio),
            }
        ],
        "lesson_mp3": str(tmp_path / lang / f"lesson.{lang}.mp3"),
    }
    (tmp_path / lang / f"lesson.{lang}.mp3").write_bytes(b"ID3fake")
    script_path = tmp_path / lang / f"narration_script.{lang}.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")

    calls = {"n": 0}

    def fake_post(path, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "tiny"}
        return {"text": reference}

    def fake_speak_section(text, **kwargs):  # noqa: ANN001
        # Keep audio_path pointing at a valid wav for the re-transcribe pass.
        _write_silent_wav(Path(audio))
        return {
            "status": "ok",
            "path": str(kwargs["mp3_path"]),
            "duration_s": 1.0,
            "chunks": 1,
            "retries": 0,
            "error": None,
        }

    with patch("bodhan_lesson.qa.loudnorm_mp3", return_value={"status": "skipped"}):
        with patch("bodhan_lesson.qa.speak_section_text", side_effect=fake_speak_section):
            with patch("bodhan_lesson.qa.concat_mp3s"):
                result = qa_lesson_audio(
                    script_path,
                    tmp_path,
                    lang,
                    api_key="t",
                    speak_key="s",
                    post_fn=fake_post,
                    speak_post_fn=lambda *a, **k: b"",
                )

    assert result["retries"] == 1
    assert result["status"] == "ok"
    updated = json.loads(script_path.read_text(encoding="utf-8"))
    assert updated["sections"][0]["qa"]["retries"] == 1
    assert updated["sections"][0]["qa"]["status"] == "ok"
    assert calls["n"] == 2


def test_qa_spoken_lessons_skips_failed_speak(tmp_path: Path) -> None:
    rows = qa_spoken_lessons(
        tmp_path,
        [{"lang": "hi", "status": "error", "error": "boom"}],
        api_key="x",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"
