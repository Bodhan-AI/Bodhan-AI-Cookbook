import pytest

from bodhan_mcp import server

CASES = [
    (server.translate_text, {"text": "", "target_language": "ta"}),
    (server.translate_text, {"text": "x" * 5000, "target_language": "ta"}),
    (server.speak_text, {"language": "ta", "text": ""}),
    (server.speak_text, {"language": "xx", "text": "hello"}),
    (server.speak_text, {"language": "ta", "text": "hello", "voice": "Amit"}),
    (server.speak_text, {"language": "ta", "text": "hello", "style": "angry"}),
    (server.speak_text, {"language": "ta", "text_path": "no/such/file.txt"}),
    (server.transcribe_audio, {"audio_path": "no/such/file.wav"}),
    (server.ocr_document, {"file_path": "no/such/file.png"}),
    (server.translate_file, {"text_path": "no/such/file.md", "target_language": "ta"}),
    (server.read_text_file, {"text_path": "no/such/file.md"}),
    (server.write_text_file, {"text": ""}),
    (server.detect_script, {"text": ""}),
]


@pytest.mark.parametrize("tool,args", CASES)
def test_bad_input_returns_a_structured_error(tool, args):
    result = tool(**args)
    assert result["ok"] is False
    assert result["error"] and result["hint"]