"""Unit tests for Indic-Speak helpers (HTTP/ffmpeg mocked)."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from unittest.mock import patch

from bodhan_lesson.speak import (
    chunk_for_tts,
    concat_wavs,
    default_voice_for,
    load_voices_file,
    speak_lesson_script,
    speak_narrated_lessons,
)


def _tiny_wav_bytes(*, frames: int = 2400, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def test_chunk_for_tts_splits_sentences() -> None:
    text = "One. Two. Three."
    chunks = chunk_for_tts(text, max_chars=8)
    assert chunks == ["One.", "Two.", "Three."]
    packed = chunk_for_tts(text, max_chars=100)
    assert packed == ["One. Two. Three."]


def test_default_voice_for() -> None:
    assert default_voice_for("hi") == "Kavya"
    assert default_voice_for("ta") == "Anitha"


def test_load_voices_file_simple_yaml(tmp_path: Path) -> None:
    path = tmp_path / "voices.yml"
    path.write_text("hi: Suhani\nta: Arun\n", encoding="utf-8")
    assert load_voices_file(path) == {"hi": "Suhani", "ta": "Arun"}


def test_concat_wavs(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(_tiny_wav_bytes(frames=2400))
    b.write_bytes(_tiny_wav_bytes(frames=2400))
    out = tmp_path / "out.wav"
    dur = concat_wavs([a, b], out)
    assert out.is_file()
    assert dur > 0.15


def test_speak_lesson_script_mocked(tmp_path: Path) -> None:
    lang = "hi"
    script = {
        "lang": lang,
        "sections": [
            {
                "id": "sec_001",
                "slug": "intro",
                "title": "Intro",
                "status": "ok",
                "text_for_tts": "नमस्ते। यह पाठ है।",
                "notes": [],
                "qa": {},
            },
            {
                "id": "sec_002",
                "slug": "skip",
                "title": "Skip",
                "status": "skipped",
                "skip_reason": "empty_section",
                "text_for_tts": None,
                "notes": [],
                "qa": {},
            },
        ],
    }
    script_path = tmp_path / lang / "narration_script.hi.json"
    script_path.parent.mkdir(parents=True)
    script_path.write_text(json.dumps(script), encoding="utf-8")

    def fake_post(path, body, api_key):  # noqa: ANN001
        assert path == "/v1/audio/speech"
        assert body["model"] == "indic-speak"
        assert body["voice"] == "Kavya"
        return _tiny_wav_bytes()

    def fake_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
        mp3_path.write_bytes(b"ID3fake")

    def fake_concat(paths: list[Path], out_path: Path) -> None:
        out_path.write_bytes(b"ID3full")

    with patch("bodhan_lesson.speak.wav_to_mp3", side_effect=fake_wav_to_mp3):
        with patch("bodhan_lesson.speak.concat_mp3s", side_effect=fake_concat):
            with patch("bodhan_lesson.speak.get_speak_api_key", return_value="k"):
                row = speak_lesson_script(
                    script_path, tmp_path, lang, post_fn=fake_post
                )

    assert row["status"] == "ok"
    assert row["sections_ok"] == 1
    assert row["sections_skipped"] == 1
    assert (tmp_path / lang / "lesson.hi.mp3").is_file()
    assert (tmp_path / lang / "narration" / "01-intro.hi.mp3").is_file()
    updated = json.loads(script_path.read_text(encoding="utf-8"))
    assert updated["ai_generated"] is True
    assert updated["sections"][0]["audio_path"]
    assert updated["sections"][0]["duration_s"] is not None


def test_speak_narrated_lessons_isolates_failure(tmp_path: Path) -> None:
    good_script = tmp_path / "hi" / "narration_script.hi.json"
    good_script.parent.mkdir(parents=True)
    good_script.write_text(
        json.dumps(
            {
                "lang": "hi",
                "sections": [
                    {
                        "id": "sec_001",
                        "slug": "a",
                        "status": "ok",
                        "text_for_tts": "Hello.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    narrations = [
        {"lang": "hi", "status": "ok", "path": str(good_script)},
        {"lang": "ta", "status": "error", "path": None, "error": "boom"},
    ]

    def fake_post(path, body, api_key):  # noqa: ANN001
        return _tiny_wav_bytes()

    with patch("bodhan_lesson.speak.wav_to_mp3", side_effect=lambda w, m: m.write_bytes(b"x")):
        with patch(
            "bodhan_lesson.speak.concat_mp3s", side_effect=lambda ps, o: o.write_bytes(b"y")
        ):
            with patch("bodhan_lesson.speak.get_speak_api_key", return_value="k"):
                rows = speak_narrated_lessons(
                    tmp_path, narrations, post_fn=fake_post
                )
    assert rows[0]["status"] == "ok"
    assert rows[1]["status"] == "skipped"
