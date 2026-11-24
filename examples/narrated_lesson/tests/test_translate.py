"""Unit tests for Indic-Translate helpers (HTTP mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bodhan_lesson.translate import (
    chunk_paragraphs,
    needs_pivot,
    protect_spans,
    restore_spans,
    structure_ok,
    translate_lesson_file,
    translate_lesson_markdown,
)


def test_protect_and_restore_math_and_table() -> None:
    text = (
        "Angle $x$ and display $$a+b$$.\n\n"
        "<table><tr><td>1</td></tr></table>\n\n"
        "Done."
    )
    protected, spans = protect_spans(text)
    assert "$x$" not in protected
    assert "$$a+b$$" not in protected
    assert "<table>" not in protected
    assert "⟦PH" in protected
    restored = restore_spans(protected, spans)
    assert "$x$" in restored
    assert "$$a+b$$" in restored
    assert "<table><tr><td>1</td></tr></table>" in restored


def test_chunk_paragraphs_respects_budget() -> None:
    text = "aaa\n\nbbb\n\nccc"
    chunks = chunk_paragraphs(text, max_chars=5)
    assert chunks == ["aaa", "bbb", "ccc"]
    packed = chunk_paragraphs(text, max_chars=100)
    assert packed == ["aaa\n\nbbb\n\nccc"]


def test_needs_pivot() -> None:
    assert needs_pivot("hi", "ta") is True
    assert needs_pivot("en", "hi") is False
    assert needs_pivot(None, "hi") is False
    assert needs_pivot("hi", "en") is False


def test_structure_ok_detects_heading_loss() -> None:
    src = "# Title\n\n## Sec\n\nBody $1$."
    good = "# शीर्षक\n\n## खंड\n\nपाठ $1$."
    bad = "# शीर्षक\n\nपाठ $1$."
    assert structure_ok(src, good)[0] is True
    ok, issues = structure_ok(src, bad)
    assert ok is False
    assert any("h2" in i or "headings" in i for i in issues)


def _chat_ok(text: str, *, usage: dict | None = None) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": usage or {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def test_translate_lesson_markdown_en_to_hi(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict] = []

    def fake_post(path, body, api_key):  # noqa: ANN001
        calls.append(body)
        assert path == "/v1/chat/completions"
        assert body["model"] == "indic-translate"
        src = body["messages"][0]["content"]
        return _chat_ok(src.replace("Hello", "नमस्ते"))

    text = "# Hello\n\nSee $x=1$."
    with patch("bodhan_lesson.translate.get_translate_api_key", return_value="k"):
        out = translate_lesson_markdown(
            text, "hi", source_lang="en", post_fn=fake_post
        )
    assert out["pivot"] is False
    assert "नमस्ते" in out["translation"]
    assert "$x=1$" in out["translation"]
    assert out["structure_ok"] is True
    assert calls and calls[0]["target_language"] == "hi"


def test_translate_lesson_markdown_indic_pivot() -> None:
    targets: list[str] = []

    def fake_post(path, body, api_key):  # noqa: ANN001
        targets.append(body["target_language"])
        if body["target_language"] == "en":
            return _chat_ok("Hello world $x$", usage={"completion_tokens": 2})
        return _chat_ok("வணக்கம் உலகம் $x$", usage={"completion_tokens": 3})

    src = "नमस्ते दुनिया $x$"
    with patch("bodhan_lesson.translate.get_translate_api_key", return_value="k"):
        out = translate_lesson_markdown(
            src, "ta", source_lang="hi", post_fn=fake_post
        )
    assert out["pivot"] is True
    assert targets == ["en", "ta"]
    assert "$x$" in out["translation"]


def test_translate_lesson_file_isolates_lang_failure(tmp_path: Path) -> None:
    lesson = tmp_path / "lesson.src.md"
    lesson.write_text("# Hello\n\nBody 1.\n", encoding="utf-8")

    def fake_post(path, body, api_key):  # noqa: ANN001
        if body["target_language"] == "ta":
            raise RuntimeError("boom")
        return _chat_ok("नमस्ते\n\nपाठ 1.", usage={"completion_tokens": 1})

    with patch("bodhan_lesson.translate.get_translate_api_key", return_value="k"):
        rows = translate_lesson_file(
            lesson,
            tmp_path,
            ["hi", "ta"],
            source_lang="en",
            post_fn=fake_post,
        )
    assert rows[0]["status"] == "ok"
    assert (tmp_path / "hi" / "lesson.hi.md").is_file()
    assert rows[1]["status"] == "error"
    assert "boom" in (rows[1]["error"] or "")
