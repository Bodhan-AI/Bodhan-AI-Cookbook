from types import SimpleNamespace

from bodhan_agent.argument_builder import prepare_tool_arguments


def test_prepare_translation_arguments(monkeypatch):
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"text": "wrong text", '
                        '"target_language": "English", '
                        '"script": "latin"}'
                    )
                )
            )
        ]
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: fake_response
            )
        )
    )

    monkeypatch.setattr(
        "bodhan_agent.argument_builder.get_llm_client",
        lambda: fake_client,
    )

    monkeypatch.setattr(
        "bodhan_agent.argument_builder.get_model_name",
        lambda: "test-model",
    )

    previous_results = [
        {
            "tool": "indic_ocr",
            "result": {
                "data": {
                    "text": "নমস্কার, আমার নাম জয়দীপ।",
                    "blocks": [],
                }
            },
        }
    ]

    arguments = prepare_tool_arguments(
        task="Extract Bengali text from an image and translate it into English.",
        step={
            "description": "Translate extracted Bengali text into English.",
            "tool": "indic_translate",
        },
        files=["sample_data/test_document.png"],
        previous_results=previous_results,
        tool_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target_language": {"type": "string"},
                "script": {"type": "string"},
            },
            "required": ["text", "target_language"],
        },
    )

    assert arguments["text"] == "নমস্কার, আমার নাম জয়দীপ।"
    assert arguments["target_language"] == "English"
    assert arguments["script"] == "native"