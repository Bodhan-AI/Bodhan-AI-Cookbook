"""Unit tests for narration-script helpers (LLM/translate mocked)."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.narrate import (
    markdown_to_speech,
    narrate_lesson_file,
    narrate_lesson_markdown,
    narrate_translated_lessons,
    split_sections,
    title_for_speech,
    unit_needs_spoken_form,
)


def test_markdown_to_speech_strips_markup() -> None:
    assert markdown_to_speech("**line-segment** and *ray*") == "line-segment and ray"
    assert markdown_to_speech("## Hello") == "Hello"


def test_title_for_speech_strips_section_numbers() -> None:
    assert title_for_speech("6.1 परिचय") == "परिचय"
    assert title_for_speech("6.2 Basic terms") == "Basic terms"
    assert title_for_speech("अध्याय 6") == "अध्याय 6"
    assert title_for_speech("6.1") == ""
    assert title_for_speech("## 6.1 Intro") == "Intro"


def test_unit_needs_spoken_form() -> None:
    assert unit_needs_spoken_form("Hello world") is False
    assert unit_needs_spoken_form("See $x$.") is False
    assert unit_needs_spoken_form(r"$0^{\circ} < x < 90^{\circ}$") is True
    assert unit_needs_spoken_form("$$a+b$$") is True
    assert unit_needs_spoken_form("<table><tr><td>1</td></tr></table>") is True
    assert unit_needs_spoken_form("Fig. 6.1 : Types of Angles") is False


def test_split_sections_by_heading() -> None:
    md = "# Title\n\nIntro para.\n\n## 6.1 Sec\n\nBody one.\n\nBody two."
    sections = split_sections(md)
    assert sections[0]["title"] == "Title"
    assert "Intro para." in sections[0]["body"]
    assert sections[1]["title"] == "6.1 Sec"
    assert "Body one." in sections[1]["body"]


def test_narrate_prose_keeps_translation_no_llm() -> None:
    calls: list[object] = []

    def boom_chat(messages):  # noqa: ANN001
        calls.append(messages)
        raise AssertionError("LLM should not run for plain prose")

    md = "# परिचय\n\nयह एक साधारण अनुच्छेद है।\n\n## अगला\n\nदूसरा भाग।"
    out = narrate_lesson_markdown(md, "hi", chat_fn=boom_chat)
    assert out["ok"] >= 1
    assert calls == []
    joined = " ".join(s["text_for_tts"] or "" for s in out["sections"] if s["status"] == "ok")
    assert "यह एक साधारण अनुच्छेद है।" in joined
    assert "दूसरा भाग।" in joined


def test_narrate_title_omits_section_number_in_tts() -> None:
    md = "## 6.1 परिचय\n\nयह अनुच्छेद है।"
    out = narrate_lesson_markdown(md, "hi", chat_fn=lambda m: ({"items": []}, {}))
    text = out["sections"][0]["text_for_tts"] or ""
    assert out["sections"][0]["title"] == "6.1 परिचय"
    assert text.startswith("परिचय")
    assert not text.startswith("6.1")


def test_narrate_equation_uses_english_then_translate() -> None:
    def fake_chat(messages):  # noqa: ANN001
        return (
            {
                "items": [
                    {
                        "unit_id": "u1",
                        "spoken_en": "x is greater than 0 degrees and less than 90 degrees.",
                    }
                ]
            },
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )

    def fake_post(path, body, api_key):  # noqa: ANN001
        assert body["target_language"] == "hi"
        src = body["messages"][0]["content"]
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": src.replace(
                            "x is greater than 0 degrees and less than 90 degrees.",
                            "x शून्य डिग्री से बड़ा और नब्बे डिग्री से छोटा है।",
                        ),
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    md = "## Angles\n\n(i) acute : $0^{\\circ} < x < 90^{\\circ}$"
    out = narrate_lesson_markdown(
        md, "hi", api_key="k", chat_fn=fake_chat, post_fn=fake_post
    )
    assert out["ok"] == 1
    text = out["sections"][0]["text_for_tts"] or ""
    assert "शून्य" in text or "नब्बे" in text
    assert "$" not in text
    assert out["llm_usage"]["total_tokens"] == 5


def test_narrate_skips_unit_when_llm_omits_it() -> None:
    def fake_chat(messages):  # noqa: ANN001
        return {"items": []}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    md = "## Math\n\nPlain words only.\n\n$$a+b=c$$"
    out = narrate_lesson_markdown(md, "en", chat_fn=fake_chat)
    sec = out["sections"][0]
    assert sec["status"] == "ok"
    assert "Plain words only." in (sec["text_for_tts"] or "")
    assert any("skipped_unit" in n for n in sec["notes"])


def test_narrate_lesson_file_writes_json(tmp_path: Path) -> None:
    lesson = tmp_path / "lesson.hi.md"
    lesson.write_text("# Hello\n\nBody text here.\n", encoding="utf-8")
    row = narrate_lesson_file(lesson, tmp_path, "hi", chat_fn=lambda m: ({"items": []}, {}))
    assert row["status"] == "ok"
    path = Path(row["path"])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["lang"] == "hi"
    assert data["ok"] >= 1
    assert data["sections"][0]["text_for_tts"]
    assert "qa" in data["sections"][0]


def test_narrate_translated_lessons_isolates_failure(tmp_path: Path) -> None:
    good = tmp_path / "hi" / "lesson.hi.md"
    good.parent.mkdir(parents=True)
    good.write_text("# Hi\n\nText.\n", encoding="utf-8")
    translations = [
        {"lang": "hi", "status": "ok", "path": str(good), "error": None},
        {"lang": "ta", "status": "error", "path": None, "error": "boom"},
    ]
    rows = narrate_translated_lessons(tmp_path, translations)
    assert rows[0]["status"] == "ok"
    assert (tmp_path / "hi" / "narration_script.hi.json").is_file()
    assert rows[1]["status"] == "skipped"


def test_narrate_with_spoken_plan_swaps_and_keeps_prose() -> None:
    """Plan snippets replace math; translated prose stays."""

    def boom_chat(messages):  # noqa: ANN001
        raise AssertionError("LLM must not run when plan covers hard spans")

    def fake_post(path, body, api_key):  # noqa: ANN001
        assert body["target_language"] == "hi"
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "ए प्लस बी बराबर सी।",
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    plan = {
        "spans": [
            {
                "id": "span_001",
                "kind": "display_math",
                "source_span": "$$a+b=c$$",
                "spoken_en": "a plus b equals c.",
                "status": "ok",
            }
        ]
    }
    md = "## कोण\n\nयह गद्य है।\n\n$$a+b=c$$\n\nअंत।"
    out = narrate_lesson_markdown(
        md,
        "hi",
        api_key="k",
        chat_fn=boom_chat,
        post_fn=fake_post,
        spoken_plan=plan,
    )
    assert out["used_spoken_plan"] is True
    text = " ".join(s["text_for_tts"] or "" for s in out["sections"])
    assert "यह गद्य है।" in text
    assert "अंत।" in text
    assert "ए प्लस बी" in text
    assert "$$" not in text
    assert out["llm_usage"]["total_tokens"] == 0
