from bodhan_mcp.fake_client import FakeBodhanClient


def test_fake_transcribe_is_deterministic(tmp_path):
    client = FakeBodhanClient()
    first = client.transcribe(tmp_path / "clip.wav")
    second = client.transcribe(tmp_path / "clip.wav")
    assert first == second
    assert first.text == "fake transcribed text"


def test_fake_translate_is_deterministic():
    client = FakeBodhanClient()
    result = client.translate("hello", target_language="ta", source_language="en")
    assert result.text == "fake translation [ta] hello"
    assert result.truncated is False


def test_fake_synthesize_returns_bytes():
    client = FakeBodhanClient()
    result = client.synthesize("hello", language="en", voice="test")
    assert result.format == "wav"
    assert result.audio_bytes.startswith(b"RIFF")
    assert result.audio_bytes[8:12] == b"WAVE"


def test_fake_ocr_never_reads_the_image(tmp_path):
    client = FakeBodhanClient()
    result = client.ocr(tmp_path / "missing-but-no-read.png")
    assert result.markdown == "fake OCR text"
    assert result.blocks == ({"type": "paragraph", "text": "fake OCR text"},)
