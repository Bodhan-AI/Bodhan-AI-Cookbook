"""Unit tests for source-side spoken plan."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.spoken_plan import (
    build_spoken_plan,
    ensure_spoken_plan,
    extract_hard_spans,
    swap_hard_spans,
    translate_plan_snippets,
)


def test_extract_hard_spans_order() -> None:
    md = (
        "Intro.\n\n"
        "<table><tr><td>1</td></tr></table>\n\n"
        "Then $$a+b$$\n\n"
        "And $0^{\\circ} < x < 90^{\\circ}$ plus plain $x$."
    )
    spans = extract_hard_spans(md)
    assert [s["kind"] for s in spans] == ["table", "display_math", "inline_math"]
    # Simple $x$ must not be a hard span.
    assert all(s["source_span"] != "$x$" for s in spans)


def test_swap_preserves_prose() -> None:
    md = "## Sec\n\nKeep this Hindi: नमस्ते\n\n$$a+b$$\n\nMore prose."
    out, notes = swap_hard_spans(md, ["a plus b."])
    assert "नमस्ते" in out
    assert "More prose." in out
    assert "$$a+b$$" not in out
    assert "a plus b." in out
    assert notes == []


def test_build_spoken_plan_tutor_en() -> None:
    def fake_chat(messages):  # noqa: ANN001
        user = json.loads(messages[1]["content"])
        assert "spans" in user
        return (
            {
                "items": [
                    {
                        "span_id": "span_001",
                        "spoken_en": "a plus b equals c.",
                    }
                ]
            },
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )

    plan = build_spoken_plan("## M\n\n$$a+b=c$$\n", source_lang="en", chat_fn=fake_chat)
    assert plan["ok"] == 1
    assert plan["spans"][0]["spoken_en"] == "a plus b equals c."
    assert plan["planning_lang"] == "en"


def test_indic_source_pivots_before_llm() -> None:
    posts: list[str] = []

    def fake_post(path, body, api_key):  # noqa: ANN001
        posts.append(body["messages"][0]["content"])
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "table of angles"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def fake_chat(messages):  # noqa: ANN001
        user = json.loads(messages[1]["content"])
        assert user["spans"][0]["text"] == "table of angles"
        return (
            {
                "items": [
                    {
                        "span_id": "span_001",
                        "spoken_en": "The table lists angle types.",
                    }
                ]
            },
            {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        )

    md = "<table><tr><td>कोण</td></tr></table>"
    plan = build_spoken_plan(
        md, source_lang="hi", chat_fn=fake_chat, post_fn=fake_post, api_key="k"
    )
    assert plan["ok"] == 1
    assert "planning_via_en_pivot" in plan["notes"]
    assert posts  # pivoted via translate


def test_ensure_spoken_plan_writes_json(tmp_path: Path) -> None:
    source = tmp_path / "source"
    lesson = source / "lesson.src.md"
    source.mkdir()
    lesson.write_text("# T\n\nOnly prose.\n", encoding="utf-8")
    plan = ensure_spoken_plan(
        source, lesson, source_lang="en", chat_fn=lambda m: ({"items": []}, {})
    )
    path = source / "spoken_plan.json"
    assert path.is_file()
    assert plan["span_count"] == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1


def test_translate_plan_snippets_to_hi() -> None:
    plan = {
        "spans": [
            {"id": "span_001", "spoken_en": "a plus b."},
            {"id": "span_002", "spoken_en": None},
        ]
    }

    def fake_post(path, body, api_key):  # noqa: ANN001
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ए प्लस बी।"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    texts, usage, notes = translate_plan_snippets(
        plan, "hi", api_key="k", post_fn=fake_post
    )
    assert texts[0] == "ए प्लस बी।"
    assert texts[1] is None
    assert usage["total_tokens"] == 2
    assert any("no_spoken_en" in n for n in notes)
