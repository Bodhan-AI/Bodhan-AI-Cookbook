"""Unit tests for the Indic Audio Engine (src.common.audio)."""

from __future__ import annotations

import math
import wave
from pathlib import Path
import numpy as np
import pytest

from src.common.audio import (
    INDIC_SENTENCE_REGEX,
    AudioChunkSegment,
    AudioMetadata,
    chunk_wav_silence_aware,
    concatenate_wav_files,
    format_srt_content,
    format_timestamp_srt,
    format_timestamp_vtt,
    format_vtt_content,
    inspect_audio,
    split_indic_sentences,
)


def create_synthetic_wav(path: Path, duration_sec: float, sample_rate: int = 24000) -> Path:
    """Create a synthetic 24kHz mono PCM16 sine-wave audio file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, total_frames, endpoint=False)
    # 440 Hz tone with soft amplitude
    signal = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(signal.tobytes())

    return path


def test_indic_sentence_splitting_hindi():
    hindi_text = "नमस्ते! आज का मौसम बहुत अच्छा है। क्या आप टहलने चलेंगे? हाँ, बिल्कुल॥"
    sentences = split_indic_sentences(hindi_text, min_chars=10)
    assert len(sentences) >= 2
    assert any("मौसम" in s for s in sentences)
    assert any("टहलने" in s for s in sentences)


def test_indic_sentence_splitting_tamil():
    tamil_text = "வணக்கம்! இன்றைய வானிலை மிகவும் அழகாக இருக்கிறது. நீங்கள் நடைப்பயிற்சிக்கு வருவீர்களா? ஆம், நிச்சயமாக."
    sentences = split_indic_sentences(tamil_text, min_chars=10)
    assert len(sentences) >= 2
    assert any("வானிலை" in s for s in sentences)


def test_indic_sentence_splitting_urdu():
    urdu_text = "السلام علیکم! آج کا موسم بہت اچھا ہے۔ کیا آپ سیر کے لیے چلیں گے؟"
    sentences = split_indic_sentences(urdu_text, min_chars=10)
    assert len(sentences) >= 1
    assert any("موسم" in s for s in sentences)


def test_audio_inspect_and_concat(tmp_path: Path):
    wav1 = create_synthetic_wav(tmp_path / "clip1.wav", duration_sec=1.5)
    wav2 = create_synthetic_wav(tmp_path / "clip2.wav", duration_sec=2.0)

    meta1 = inspect_audio(wav1)
    assert math.isclose(meta1.duration_seconds, 1.5, abs_tol=0.05)
    assert meta1.sample_rate == 24000
    assert meta1.channels == 1
    assert meta1.is_24khz_mono

    # 1. Default concat with 200ms silence gap: 1.5 + 0.2 + 2.0 = 3.7s
    out_concat = tmp_path / "concat.wav"
    concatenate_wav_files([wav1, wav2], out_concat)
    meta_concat = inspect_audio(out_concat)
    assert math.isclose(meta_concat.duration_seconds, 3.7, abs_tol=0.05)

    # 2. Butt-join with silence_gap_ms=0: 1.5 + 2.0 = 3.5s
    out_zero_gap = tmp_path / "concat_zero.wav"
    concatenate_wav_files([wav1, wav2], out_zero_gap, silence_gap_ms=0)
    meta_zero = inspect_audio(out_zero_gap)
    assert math.isclose(meta_zero.duration_seconds, 3.5, abs_tol=0.05)


def test_concat_wav_bit_depth_mismatch(tmp_path: Path):
    wav_16bit = create_synthetic_wav(tmp_path / "pcm16.wav", duration_sec=1.0)
    
    # Create an 8-bit audio file (sampwidth = 1)
    wav_8bit = tmp_path / "pcm8.wav"
    with wave.open(str(wav_8bit), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(24000)
        wf.writeframes(b"\x80" * 24000)

    out_file = tmp_path / "bad_concat.wav"
    with pytest.raises(ValueError, match="WAV mismatch"):
        concatenate_wav_files([wav_16bit, wav_8bit], out_file)


def test_chunk_wav_threshold_consistency(tmp_path: Path):
    # Audio with 26.0s (under max_chunk_sec=28.0) returns unchunked single path
    short_wav = create_synthetic_wav(tmp_path / "short_26s.wav", duration_sec=26.0)
    chunks_short = chunk_wav_silence_aware(short_wav, tmp_path / "short_out", max_chunk_sec=28.0)
    assert len(chunks_short) == 1
    assert chunks_short[0] == short_wav

    # Audio with 29.0s (between 28.0s and 30.0s) MUST be chunked when max_chunk_sec=28.0
    med_wav = create_synthetic_wav(tmp_path / "med_29s.wav", duration_sec=29.0)
    chunks_med = chunk_wav_silence_aware(med_wav, tmp_path / "med_out", max_chunk_sec=28.0, search_window_start=20.0)
    assert len(chunks_med) >= 2


def test_silence_aware_chunking(tmp_path: Path):
    # 40-second audio with silence around 22s-25s
    sample_rate = 24000
    total_frames = int(40.0 * sample_rate)
    t = np.linspace(0, 40.0, total_frames, endpoint=False)
    signal = (np.sin(2 * np.pi * 300 * t) * 15000).astype(np.int16)

    # Insert silence dip between 22s and 25s
    silence_start = int(22.0 * sample_rate)
    silence_end = int(25.0 * sample_rate)
    signal[silence_start:silence_end] = 0

    wav_file = tmp_path / "long_speech.wav"
    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(signal.tobytes())

    chunks = chunk_wav_silence_aware(
        wav_file,
        output_dir=tmp_path / "chunks",
        max_chunk_sec=28.0,
        search_window_start=20.0,
    )

    assert len(chunks) >= 2
    for c in chunks:
        meta = inspect_audio(c)
        assert meta.duration_seconds <= 28.5


def test_indic_sentence_splitting_edge_cases():
    # 1. Honorific abbreviations with periods
    t1 = "डॉ. कलाम और पं. नेहरू महान विभूतियां थीं। उनका योगदान अतुलनीय है।"
    c1 = split_indic_sentences(t1, min_chars=15)
    assert len(c1) == 2
    assert "डॉ. कलाम और पं. नेहरू" in c1[0]

    # 2. Danda followed by quotes
    t2 = 'उसने कहा, "यह सच है।" फिर वह तेजी से चला गया।'
    c2 = split_indic_sentences(t2, min_chars=15)
    assert len(c2) == 2
    assert 'सच है।"' in c2[0]

    # 3. Double dandas in verses
    t3 = "गुरुर्ब्रह्मा गुरुर्विष्णुः गुरुर्देवो महेश्वरः॥ गुरुः साक्षात् परं ब्रह्म तस्मै श्रीगुरवे नमः॥"
    c3 = split_indic_sentences(t3, min_chars=15)
    assert len(c3) == 2
    assert c3[0].endswith("॥")

    # 4. Urdu Khatam and Arabic question mark
    t4 = "کیا آپ نے کھانا کھا لیا؟ جی ہاں، میں نے کھانا کھا لیا ہے۔ اب چائے پیتے ہیں۔"
    c4 = split_indic_sentences(t4, min_chars=15)
    assert len(c4) == 3

    # 5. Code-mixed with decimal numbers
    t5 = "मुझे 2.5 लाख रुपये UPI से ट्रांसफर करने हैं। क्या सर्वर 99.9% चालू है?"
    c5 = split_indic_sentences(t5, min_chars=15)
    assert len(c5) == 2
    assert "2.5" in c5[0]
    assert "99.9%" in c5[1]


def test_inspect_audio_diagnostic_warnings(tmp_path: Path):
    # Stereo 48kHz file
    stereo_path = tmp_path / "stereo_48k.wav"
    t = np.linspace(0, 1.0, 48000, endpoint=False)
    mono = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    stereo = np.column_stack([mono, mono])
    with wave.open(str(stereo_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(stereo.tobytes())

    meta = inspect_audio(stereo_path)
    assert meta.has_warnings
    assert any("Multi-channel" in w for w in meta.warnings)
    assert any("48kHz" in w for w in meta.warnings)

    # 8kHz telephony mono file
    telephony_path = tmp_path / "telephony_8k.wav"
    t_8k = np.linspace(0, 1.0, 8000, endpoint=False)
    mono_8k = (np.sin(2 * np.pi * 440 * t_8k) * 10000).astype(np.int16)
    with wave.open(str(telephony_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(mono_8k.tobytes())

    meta_8k = inspect_audio(telephony_path)
    assert any("Telephony 8kHz" in w for w in meta_8k.warnings)


def test_convert_to_indic_pcm16(tmp_path: Path):
    from src.common.audio import convert_to_indic_pcm16
    
    # Create 8kHz stereo file
    in_wav = tmp_path / "raw_8k_stereo.wav"
    t = np.linspace(0, 1.0, 8000, endpoint=False)
    mono = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    stereo = np.column_stack([mono, mono])
    with wave.open(str(in_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(stereo.tobytes())

    out_wav = tmp_path / "converted_24k_mono.wav"
    convert_to_indic_pcm16(in_wav, out_wav, target_sr=24000)

    meta = inspect_audio(out_wav)
    assert meta.sample_rate == 24000
    assert meta.channels == 1
    assert meta.sample_width == 2
    assert meta.is_24khz_mono
    assert not meta.has_warnings


def test_ffmpeg_audio_formats_conversion_and_inspection(tmp_path: Path):
    """Test non-WAV compressed formats (MP3, OGG, M4A, FLAC, WebM) with ffmpeg."""
    import subprocess
    from src.common.audio import _resolve_tool_path, ensure_wav, inspect_audio

    ffmpeg_bin = _resolve_tool_path("ffmpeg")
    try:
        check = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, timeout=5)
        if check.returncode != 0:
            pytest.skip("ffmpeg not available")
    except Exception:
        pytest.skip("ffmpeg not available")

    # Test formats
    test_formats = ["mp3", "ogg", "m4a", "flac", "webm"]
    for ext in test_formats:
        media_file = tmp_path / f"synthetic_tone.{ext}"
        # Generate 1.5s sine tone
        res = subprocess.run(
            [ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=1.5", str(media_file)],
            capture_output=True,
            timeout=15,
        )
        assert res.returncode == 0, f"Failed to generate {ext} test audio: {res.stderr}"
        assert media_file.exists() and media_file.stat().st_size > 0

        # 1. Test inspect_audio on non-WAV file (ffprobe)
        meta = inspect_audio(media_file)
        assert meta.duration_seconds > 0.0
        assert math.isclose(meta.duration_seconds, 1.5, abs_tol=0.2)
        assert meta.has_warnings
        assert any("Compressed/non-WAV container" in w for w in meta.warnings)

        # 2. Test ensure_wav transcoding to standard 16kHz mono PCM16
        wav_converted = ensure_wav(media_file, output_dir=tmp_path / "conv_out", target_sr=16000)
        assert wav_converted.is_file()
        assert wav_converted.suffix.lower() == ".wav"

        wav_meta = inspect_audio(wav_converted)
        assert wav_meta.sample_rate == 16000
        assert wav_meta.channels == 1
        assert wav_meta.sample_width == 2
        assert math.isclose(wav_meta.duration_seconds, 1.5, abs_tol=0.2)


def test_audio_chunk_segment_dataclass(tmp_path: Path):
    dummy_path = tmp_path / "test.wav"
    seg = AudioChunkSegment(path=dummy_path, start_sec=0.0, end_sec=5.25)
    assert seg.path == dummy_path
    assert seg.start_sec == 0.0
    assert seg.end_sec == 5.25

    with pytest.raises(Exception):
        seg.start_sec = 1.0  # frozen dataclass


def test_chunk_wav_silence_aware_backward_compatibility(tmp_path: Path):
    wav = create_synthetic_wav(tmp_path / "short.wav", duration_sec=2.0)
    # Default return_segments=False returns list[Path]
    res = chunk_wav_silence_aware(wav, tmp_path / "out1")
    assert isinstance(res, list)
    assert len(res) == 1
    assert isinstance(res[0], Path)
    assert res[0] == wav

    # Explicit return_segments=False returns list[Path]
    res2 = chunk_wav_silence_aware(wav, tmp_path / "out2", return_segments=False)
    assert isinstance(res2[0], Path)


def test_chunk_wav_silence_aware_segments(tmp_path: Path):
    # Short audio
    wav_short = create_synthetic_wav(tmp_path / "short_seg.wav", duration_sec=3.5)
    res_short = chunk_wav_silence_aware(wav_short, tmp_path / "out_short", return_segments=True)
    assert isinstance(res_short, list)
    assert len(res_short) == 1
    assert isinstance(res_short[0], AudioChunkSegment)
    assert res_short[0].start_sec == 0.0
    assert math.isclose(res_short[0].end_sec, 3.5, abs_tol=0.05)
    assert res_short[0].path.is_file()

    # Long audio (35 seconds) with silence around 22-25s
    sample_rate = 24000
    total_frames = int(35.0 * sample_rate)
    t = np.linspace(0, 35.0, total_frames, endpoint=False)
    signal = (np.sin(2 * np.pi * 300 * t) * 15000).astype(np.int16)
    silence_start = int(22.0 * sample_rate)
    silence_end = int(25.0 * sample_rate)
    signal[silence_start:silence_end] = 0

    wav_long = tmp_path / "long_seg.wav"
    with wave.open(str(wav_long), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(signal.tobytes())

    res_long = chunk_wav_silence_aware(
        wav_long,
        tmp_path / "out_long",
        max_chunk_sec=28.0,
        search_window_start=20.0,
        return_segments=True,
    )
    assert len(res_long) >= 2
    assert all(isinstance(s, AudioChunkSegment) for s in res_long)
    assert res_long[0].start_sec == 0.0
    # Segments should be contiguous
    for i in range(len(res_long) - 1):
        assert res_long[i].end_sec == res_long[i + 1].start_sec
        assert res_long[i].path.is_file()
    assert math.isclose(res_long[-1].end_sec, 35.0, abs_tol=0.1)


def test_format_timestamps():
    assert format_timestamp_srt(0.0) == "00:00:00,000"
    assert format_timestamp_srt(83.45) == "00:01:23,450"
    assert format_timestamp_srt(3661.005) == "01:01:01,005"
    assert format_timestamp_srt(-10.0) == "00:00:00,000"

    assert format_timestamp_vtt(0.0) == "00:00:00.000"
    assert format_timestamp_vtt(83.45) == "00:01:23.450"
    assert format_timestamp_vtt(3661.005) == "01:01:01.005"
    assert format_timestamp_vtt(-10.0) == "00:00:00.000"


def test_format_srt_content():
    cues = [
        (0.0, 3.5, "  नमस्ते दुनिया  "),
        (3.5, 7.2, "आज का दिन बहुत शुभ है।"),
        (7.2, 9.0, "   "),  # Empty text should be skipped
        (9.0, 12.5, "अलविदा।"),
    ]
    srt = format_srt_content(cues)
    expected = (
        "1\n00:00:00,000 --> 00:00:03,500\nनमस्ते दुनिया\n\n"
        "2\n00:00:03,500 --> 00:00:07,200\nआज का दिन बहुत शुभ है।\n\n"
        "3\n00:00:09,000 --> 00:00:12,500\nअलविदा।\n"
    )
    assert srt == expected
    assert format_srt_content([]) == ""
    assert format_srt_content([(0.0, 1.0, "")]) == ""


def test_format_vtt_content():
    cues = [
        (0.0, 2.5, "Hello world"),
        (2.5, 5.0, "Goodbye"),
    ]
    vtt = format_vtt_content(cues)
    expected = (
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:02.500\nHello world\n\n"
        "2\n00:00:02.500 --> 00:00:05.000\nGoodbye\n"
    )
    assert vtt == expected
    assert format_vtt_content([]) == "WEBVTT\n"


