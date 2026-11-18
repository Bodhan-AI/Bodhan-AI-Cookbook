"""Server tools must return structured results and never raise on bad input."""

from bodhan_mcp import server


def test_supported_languages_all_models():
    r = server.supported_languages()
    assert r["ok"] and set(r["languages"]) == {"indic-transcribe", "indic-translate", "indic-speak", "indic-ocr"}


def test_list_voices_unsupported_language_is_structured_error():
    r = server.list_voices(language="French")
    assert r["ok"] is False
    assert r["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    assert r["error"]["hint"]


def test_list_voices_filters():
    r = server.list_voices(language="Bengali", gender="female")
    assert r == {"ok": True, "voices": [{"voice": "Ishita", "language": "bn", "gender": "female"}]}
