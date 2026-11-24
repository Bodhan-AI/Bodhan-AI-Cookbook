from __future__ import annotations

from bodhan_agent.preflight import inspect_task_inputs, validate_plan
from bodhan_agent.tool_catalog import build_tool_catalog
from bodhan_mcp.workspace import Workspace


def test_preflight_inspects_supported_inputs_and_accepts_supported_task(tmp_path):
    source = tmp_path / "paragraph.txt"
    source.write_text("নমস্কার", encoding="utf-8")
    workspace = Workspace(tmp_path)

    report = inspect_task_inputs(["paragraph.txt"], workspace)

    assert report["ok"] is True
    assert report["inspected_inputs"][0]["file_type"] == "text"
    assert report["inspected_inputs"][0]["extension"] == ".txt"
    assert report["issues"] == []


def test_preflight_accepts_video_for_supported_audio_extraction(tmp_path):
    source = tmp_path / "chapter.mp4"
    source.write_bytes(b"fixture video")
    workspace = Workspace(tmp_path)

    report = inspect_task_inputs(["chapter.mp4"], workspace)

    assert report["ok"] is True
    assert report["inspected_inputs"][0]["file_type"] == "video"
    assert report["issues"] == []


def test_plan_validation_rejects_unverified_speak_voice(tmp_path):
    report = validate_plan(
        [{"tool": "speak", "text": "Hello", "language": "bn", "voice_id": "fake_voice_99"}],
        Workspace(tmp_path),
    )

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "INVALID_ARGUMENT"


def test_preflight_does_not_infer_capabilities_from_task_wording(tmp_path):
    source = tmp_path / "chapter.pdf"
    source.write_bytes(b"%PDF-1.4 fixture")
    workspace = Workspace(tmp_path)

    report = inspect_task_inputs(["chapter.pdf"], workspace)

    assert report["ok"] is True
    assert report["issues"] == []


def test_preflight_allows_translation_of_existing_subtitle_file(tmp_path):
    source = tmp_path / "captions.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    workspace = Workspace(tmp_path)

    report = inspect_task_inputs(["captions.srt"], workspace)

    assert report["ok"] is True
    assert report["issues"] == []


def test_preflight_checks_input_size_and_workspace_safety(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("too large", encoding="utf-8")
    workspace = Workspace(tmp_path, max_input_bytes=3)

    report = inspect_task_inputs(["large.txt"], workspace)

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "INVALID_ARGUMENT"
    assert "over the 0 MB limit" in report["issues"][0]["message"]


def test_preflight_rejects_files_outside_the_workspace(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("not allowed", encoding="utf-8")

    report = inspect_task_inputs([str(outside_file)], Workspace(workspace_root))

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "PATH_NOT_ALLOWED"


def test_plan_validation_rejects_unsupported_language_and_missing_file(tmp_path):
    workspace = Workspace(tmp_path)
    report = validate_plan(
        [
            {"tool": "translate", "text": "Hello", "target_language": "French"},
            {"tool": "transcribe", "file_path": "missing.wav", "language": "hi"},
        ],
        workspace,
    )

    assert report["ok"] is False
    assert {issue["code"] for issue in report["issues"]} >= {
        "UNSUPPORTED_LANGUAGE",
        "FILE_NOT_FOUND",
    }


def test_plan_validation_rejects_tool_missing_configured_credentials(tmp_path):
    report = validate_plan(
        [{"tool": "transcribe", "file_path": "speech.wav"}],
        Workspace(tmp_path),
        build_tool_catalog({}),
    )

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "TOOL_UNAVAILABLE"
    assert "BODHAN_TRANSCRIBE_KEY" in report["issues"][0]["hint"]


def test_plan_validation_uses_catalog_language_configuration(tmp_path):
    catalog = build_tool_catalog({"BODHAN_TRANSLATE_KEY": "configured"})
    catalog["supported_languages"] = ["hi"]

    report = validate_plan(
        [{"tool": "translate", "text": "Hello", "target_language": "ta"}],
        Workspace(tmp_path),
        catalog,
    )

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "UNSUPPORTED_LANGUAGE"
    assert "listed in the task-agent capability catalog" in report["issues"][0]["hint"]


def test_plan_validation_rejects_malformed_steps_and_unavailable_source_keys(tmp_path):
    report = validate_plan(
        [
            None,
            {"tool": "translate", "target_language": "hi", "source_key": "missing_output"},
            {"tool": []},
        ],
        Workspace(tmp_path),
    )

    assert report["ok"] is False
    assert len(report["issues"]) == 3


def test_plan_validation_rejects_non_list_response(tmp_path):
    report = validate_plan({"tool": "transcribe"}, Workspace(tmp_path))

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "INVALID_ARGUMENT"


def test_empty_plan_requires_an_explicit_refusal(tmp_path):
    report = validate_plan([], Workspace(tmp_path), build_tool_catalog({}))

    assert report["ok"] is False
    assert report["issues"][0]["code"] == "INVALID_ARGUMENT"
    assert "explicit refusal" in report["issues"][0]["message"]


def test_plan_refusal_preserves_specific_missing_capability(tmp_path):
    refusal = {
        "code": "CAPABILITY_UNAVAILABLE",
        "message": (
            "Timestamped transcription is unavailable, so SRT subtitles cannot be generated."
        ),
        "hint": "Use a deployment with timed transcription and subtitle formatting.",
        "capability": "timestamped_transcription",
    }

    report = validate_plan([], Workspace(tmp_path), refusal=refusal)

    assert report["ok"] is False
    assert report["issues"] == [
        {
            "code": "CAPABILITY_UNAVAILABLE",
            "message": refusal["message"],
            "hint": refusal["hint"],
        }
    ]


def test_plan_refusal_rejects_mixed_steps_and_refusal(tmp_path):
    report = validate_plan(
        [{"tool": "translate", "text": "Hi", "target_language": "hi"}],
        Workspace(tmp_path),
        refusal={"code": "CAPABILITY_UNAVAILABLE", "message": "No."},
    )

    assert report["ok"] is False
    assert "both executable steps and a refusal" in report["issues"][0]["message"]


def test_plan_validation_accepts_supported_model_tool_and_language(tmp_path):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"fixture audio")

    report = validate_plan(
        [{"tool": "transcribe", "file_path": "speech.wav", "language": "hi"}],
        Workspace(tmp_path),
    )

    assert report == {"ok": True, "issues": []}


def test_plan_validation_uses_catalog_types_and_nullable_voice_id(tmp_path):
    valid = validate_plan(
        [{"tool": "speak", "text": "Hello", "language": "en", "voice_id": None}],
        Workspace(tmp_path),
    )
    invalid = validate_plan(
        [{"tool": "ocr", "file_path": "scan.pdf", "page_number": True}],
        Workspace(tmp_path),
    )

    assert valid == {"ok": True, "issues": []}
    assert invalid["ok"] is False
    assert any("`page_number`" in issue["message"] for issue in invalid["issues"])


def test_plan_validation_accepts_a_source_key_from_an_earlier_step(tmp_path):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"fixture audio")

    report = validate_plan(
        [
            {"tool": "transcribe", "file_path": "speech.wav", "language": "hi"},
            {"tool": "translate", "target_language": "en", "source_key": "transcript"},
            {"tool": "speak", "language": "en", "source_key": "translation"},
        ],
        Workspace(tmp_path),
    )

    assert report == {"ok": True, "issues": []}


def test_plan_validation_rejects_non_text_output_as_text_input(tmp_path):
    report = validate_plan(
        [
            {"tool": "speak", "text": "Hello", "language": "en"},
            {"tool": "translate", "target_language": "hi", "source_key": "audio_path"},
        ],
        Workspace(tmp_path),
    )

    assert report["ok"] is False
    assert "has type 'audio', not text" in report["issues"][0]["message"]


def test_composable_plan_validates_typed_references_and_deliverables(tmp_path):
    source = tmp_path / "speech.wav"
    source.write_bytes(b"fixture audio")
    plan = [
        {
            "id": "transcription",
            "tool": "transcribe",
            "arguments": {"file_path": "speech.wav"},
        },
        {
            "id": "translation",
            "tool": "translate",
            "arguments": {
                "target_language": "en",
                "text": {"$ref": {"step_id": "transcription", "output": "transcript"}},
            },
        },
    ]

    report = validate_plan(
        plan,
        Workspace(tmp_path),
        deliverables=[
            {
                "name": "english_text",
                "step_id": "translation",
                "output": "translation",
                "media_type": "text",
                "description": "Complete English translation.",
            }
        ],
    )

    assert report == {"ok": True, "issues": []}


def test_composable_plan_rejects_forward_and_incompatible_references(tmp_path):
    plan = [
        {
            "id": "translation",
            "tool": "translate",
            "arguments": {
                "target_language": "en",
                "text": {"$ref": {"step_id": "later", "output": "transcript"}},
            },
        },
        {
            "id": "later",
            "tool": "speak",
            "arguments": {
                "language": "en",
                "text": {"$ref": {"step_id": "translation", "output": "translation"}},
            },
        },
    ]
    deliverables = [
        {
            "name": "audio",
            "step_id": "later",
            "output": "audio_path",
            "media_type": "audio",
            "description": "Generated audio.",
        }
    ]

    report = validate_plan(plan, Workspace(tmp_path), deliverables=deliverables)

    assert report["ok"] is False
    assert any(
        "unknown or not produced by an earlier step" in issue["message"]
        for issue in report["issues"]
    )


def test_composable_plan_rejects_audio_bound_to_text_input(tmp_path):
    plan = [
        {
            "id": "synthesis",
            "tool": "speak",
            "arguments": {"language": "en", "text": "Hello."},
        },
        {
            "id": "translation",
            "tool": "translate",
            "arguments": {
                "target_language": "hi",
                "text": {"$ref": {"step_id": "synthesis", "output": "audio_path"}},
            },
        },
    ]
    deliverables = [
        {
            "name": "translated_text",
            "step_id": "translation",
            "output": "translation",
            "media_type": "text",
            "description": "Hindi translation.",
        }
    ]

    report = validate_plan(plan, Workspace(tmp_path), deliverables=deliverables)

    assert report["ok"] is False
    assert any("has type 'audio'" in issue["message"] for issue in report["issues"])


def test_composable_plan_validates_file_batch_bindings_and_condition(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("Hello", encoding="utf-8")
    manifest = [{"file_path": "notes.txt", "extension": ".txt"}]
    plan = [
        {
            "id": "translate_each",
            "tool": "translate",
            "arguments": {
                "target_language": "hi",
                "text": {"$item": "file_path"},
            },
            "for_each": {"source": {"input": "files"}, "as": "item"},
        },
        {
            "id": "translate_again",
            "tool": "translate",
            "arguments": {
                "target_language": "en",
                "text": "Combine the results.",
            },
            "when": {
                "ref": {"step_id": "translate_each", "output": "translation"},
                "operator": "contains",
                "value": "hello",
            },
        },
    ]
    deliverables = [
        {
            "name": "final_text",
            "step_id": "translate_again",
            "output": "translation",
            "media_type": "text",
            "description": "Final translation.",
        }
    ]

    report = validate_plan(
        plan,
        Workspace(tmp_path),
        deliverables=deliverables,
        input_manifest=manifest,
    )

    assert report == {"ok": True, "issues": []}
