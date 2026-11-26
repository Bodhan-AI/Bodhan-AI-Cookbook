from bodhan_dub.clients.speak import BodhanSpeakClient
from bodhan_dub.clients.transcribe import BodhanTranscribeClient
from bodhan_dub.clients.translate import BodhanTranslateClient


def test_mock_transcribe():
    client = BodhanTranscribeClient(api_key="test", mock_mode=True)
    res = client.transcribe(b"dummy_bytes", language="en")
    assert "text" in res
    assert len(res["text"]) > 0

def test_mock_translate():
    client = BodhanTranslateClient(api_key="test", mock_mode=True)
    res = client.translate("Hello world", target_lang="hi")
    assert "यह" in res or "Hello" in res

def test_mock_speak(tmp_path):
    client = BodhanSpeakClient(api_key="test", mock_mode=True)
    out_file = tmp_path / "test.wav"
    path, dur = client.generate_speech("नमस्ते", "hi", out_file, target_duration=2.0)
    assert path.exists()
    assert dur > 0.0
