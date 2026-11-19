import os
import wave
import struct
import tempfile
import pytest
from PIL import Image

from bodhan_mcp.tools.transcribe import chunk_wav_file, get_audio_duration_and_frames
from bodhan_mcp.tools.translate import split_text_into_chunks
from bodhan_mcp.tools.speak import split_text_for_tts
from bodhan_mcp.tools.media import run_inspect_media, run_detect_language
from bodhan_mcp.models import InspectMediaInput, DetectLanguageInput


@pytest.fixture
def sample_wav_70s():
    """Create a temporary 70-second WAV file."""
    temp_dir = tempfile.mkdtemp()
    wav_path = os.path.join(temp_dir, "test_70s.wav")
    sample_rate = 8000
    duration = 70  # seconds
    total_frames = int(sample_rate * duration)

    with wave.open(wav_path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        # Write silence
        f.writeframes(b"".join(struct.pack("<h", 0) for _ in range(total_frames)))

    yield wav_path
    if os.path.exists(wav_path):
        os.remove(wav_path)


@pytest.fixture
def sample_image():
    """Create a temporary test image."""
    temp_dir = tempfile.mkdtemp()
    img_path = os.path.join(temp_dir, "test_img.png")
    img = Image.new("RGB", (640, 480), color="blue")
    img.save(img_path)
    yield img_path
    if os.path.exists(img_path):
        os.remove(img_path)


def test_audio_chunking_under_30s(sample_wav_70s):
    duration, rate, channels, sampwidth = get_audio_duration_and_frames(sample_wav_70s)
    assert 69.9 <= duration <= 70.1

    chunks = chunk_wav_file(sample_wav_70s, max_chunk_seconds=28.0)
    assert len(chunks) == 3  # 28s + 28s + 14s = 70s

    for chunk_path, start_sec, chunk_dur in chunks:
        assert chunk_dur <= 28.0
        assert os.path.exists(chunk_path)


def test_translation_chunking():
    paragraphs = [f"Paragraph {i}: " + ("This is educational text. " * 30) for i in range(10)]
    long_text = "\n\n".join(paragraphs)

    chunks = split_text_into_chunks(long_text, max_chunk_chars=1000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1500  # Within bounds, respects paragraph boundaries

    reassembled = "\n\n".join(chunks)
    assert len(reassembled) == len(long_text)


def test_tts_chunking():
    text = (
        "गणित एक बहुत महत्वपूर्ण विषय है। रेखाएं और कोण ज्यामिति की बुनियादी अवधारणाएं हैं। "
        "जब दो रेखाएं एक दूसरे को काटती हैं, तो शीर्षाभिमुख कोण बराबर होते हैं! क्या आप इसे सिद्ध कर सकते हैं?"
    )
    chunks = split_text_for_tts(text, max_chars=80)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 100


def test_media_inspection(sample_wav_70s, sample_image):
    audio_info = run_inspect_media(InspectMediaInput(file_path=sample_wav_70s))
    assert audio_info.file_type == "audio"
    assert audio_info.sample_rate == 8000
    assert 69.9 <= audio_info.duration_seconds <= 70.1

    img_info = run_inspect_media(InspectMediaInput(file_path=sample_image))
    assert img_info.file_type == "image"
    assert img_info.width == 640
    assert img_info.height == 480


def test_script_language_detection():
    # Hindi (Devanagari)
    res_hi = run_detect_language(DetectLanguageInput(text="यह हिंदी में लिखा गया वाक्य है।"))
    assert res_hi.detected_lang == "hi"
    assert res_hi.is_indic is True

    # Tamil
    res_ta = run_detect_language(DetectLanguageInput(text="இது தமிழில் எழுதப்பட்ட வாக்கியம்."))
    assert res_ta.detected_lang == "ta"
    assert res_ta.is_indic is True

    # Bengali
    res_bn = run_detect_language(DetectLanguageInput(text="এটি বাংলায় লেখা একটি বাক্য।"))
    assert res_bn.detected_lang == "bn"
    assert res_bn.is_indic is True

    # English
    res_en = run_detect_language(DetectLanguageInput(text="This is an English sentence for testing."))
    assert res_en.detected_lang == "en"
    assert res_en.is_indic is False
