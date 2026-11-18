"""Tests for Indic-Speak MCP tool using synthetic audio and httpx.MockTransport (Zero Live API Calls)."""

import io
import json
import struct
import wave
from pathlib import Path

import httpx
import pytest

from bodhan_mcp.tools.speak import (
    IndicSpeakTool,
    chunk_text_by_sentence,
)
from bodhan_mcp.tools.transcribe import get_ffmpeg_binaries


def create_fake_wav_bytes(duration_seconds: float = 2.0, sample_rate: int = 24000) -> bytes:
    """Creates a valid 24 kHz mono 16-bit PCM WAV in memory."""
    buf = io.BytesIO()
    num_samples = int(duration_seconds * sample_rate)
    samples = bytearray()
    for i in range(num_samples):
        val = int(2000 * ((i % 40) / 20.0 - 1.0))
        samples.extend(struct.pack("<h", val))

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples)

    return buf.getvalue()


@pytest.fixture
def tmp_speak_cache(tmp_path: Path):
    """Provides an isolated cache directory for Speak tests."""
    cache_dir = tmp_path / "test_speak_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def test_speak_sentence_chunking():
    """Text with multiple sentences exceeding max_chars is split cleanly along sentence boundaries."""
    s1 = "This is the first sentence that provides initial context."  # 57 chars
    s2 = "This is the second sentence providing more detailed information."  # 64 chars
    s3 = "This is the third sentence explaining further aspects of the topic."  # 67 chars
    s4 = "This is the fourth sentence concluding the entire paragraph neatly."  # 67 chars
    # Total text is ~258 chars (> 250 max_chars)
    full_text = f"{s1} {s2} {s3} {s4}"
    chunks = chunk_text_by_sentence(full_text, max_chars=200)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 200
        # Ensure chunks don't break in the middle of sentences when sentences fit
        assert chunk.endswith((".", "!", "?", "।"))

    # Also test chunking at sentence boundaries with smaller max_chars
    small_chunks = chunk_text_by_sentence(
        "First sentence. Second sentence! Third sentence? Fourth sentence।",
        max_chars=20,
    )
    assert len(small_chunks) == 4
    assert small_chunks[0] == "First sentence."
    assert small_chunks[1] == "Second sentence!"
    assert small_chunks[2] == "Third sentence?"
    assert small_chunks[3] == "Fourth sentence।"


def test_speak_250_char_limit():
    """A sentence longer than 250 characters is split at space boundaries under 250 chars."""
    words = ["Word" + str(i) for i in range(70)]
    long_sentence = " ".join(words) + "."
    assert len(long_sentence) > 300

    chunks = chunk_text_by_sentence(long_sentence, max_chars=250)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 250
    # Combined words should match original words
    combined = " ".join(chunks)
    assert long_sentence.replace("  ", " ").strip() == combined.replace("  ", " ").strip()


@pytest.mark.asyncio
async def test_speak_budget_exceeded_makes_zero_http_calls(tmp_path: Path, tmp_speak_cache: Path):
    """When estimated cost exceeds max_cost_inr budget ceiling, 0 HTTP calls are made."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=create_fake_wav_bytes(1.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(
            api_key="mock_key",
            cache_dir=tmp_speak_cache,
            client=client,
            max_cost_inr=0.001,  # Rs 0.001 budget
        )
        # 100 characters => Rs 0.0600, which exceeds Rs 0.001
        text = "This is a sentence that has enough characters to definitely exceed the tiny budget limit set."
        out_path = tmp_path / "budget_test.wav"
        result = await tool.speak(
            input_text=text, voice="Kavya", language="hi", output_path=out_path
        )

        assert not result.success
        assert call_count == 0
        assert result.error is not None
        assert result.error.code == "BUDGET_EXCEEDED"
        assert not out_path.exists()


@pytest.mark.asyncio
async def test_speak_unsupported_language_makes_zero_http_calls(
    tmp_path: Path, tmp_speak_cache: Path
):
    """Unsupported language returns UNSUPPORTED_LANGUAGE with 0 HTTP calls."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=create_fake_wav_bytes(1.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "unsupported_lang.wav"
        result = await tool.speak(
            input_text="Bonjour le monde.",
            voice="Kavya",
            language="fr",  # French is not among 23 supported codes
            output_path=out_path,
        )

        assert not result.success
        assert call_count == 0
        assert result.error is not None
        assert result.error.code == "UNSUPPORTED_LANGUAGE"


@pytest.mark.asyncio
async def test_speak_bad_voice_makes_zero_http_calls(tmp_path: Path, tmp_speak_cache: Path):
    """Unrecognized voice returns INVALID_VOICE with 0 HTTP calls."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=create_fake_wav_bytes(1.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "bad_voice.wav"
        result = await tool.speak(
            input_text="Good morning.",
            voice="NonExistentVoice",
            language="en",
            output_path=out_path,
        )

        assert not result.success
        assert call_count == 0
        assert result.error is not None
        assert result.error.code == "INVALID_VOICE"


@pytest.mark.asyncio
async def test_speak_cache_hit_makes_zero_http_calls(tmp_path: Path, tmp_speak_cache: Path):
    """Subsequent call with identical voice, lang, and text hits disk cache and makes 0 HTTP calls."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        # Return 2.0s audio for ~25 chars
        return httpx.Response(200, content=create_fake_wav_bytes(2.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        text = "Hello world welcome back."
        out_path_1 = tmp_path / "speak_run1.wav"
        out_path_2 = tmp_path / "speak_run2.wav"

        # Call 1: live HTTP call
        res1 = await tool.speak(
            input_text=text, voice="Amit", language="en", output_path=out_path_1
        )
        assert res1.success
        assert call_count == 1
        assert res1.billed_chunks_count == 1
        assert res1.cached_chunks_count == 0
        assert res1.estimated_cost_inr > 0

        # Call 2: cache hit
        res2 = await tool.speak(
            input_text=text, voice="Amit", language="en", output_path=out_path_2
        )
        assert res2.success
        assert call_count == 1  # No new HTTP calls
        assert res2.billed_chunks_count == 0
        assert res2.cached_chunks_count == 1
        assert res2.chunks[0].cache_hit is True
        assert res2.estimated_cost_inr == 0.0


@pytest.mark.asyncio
async def test_speak_one_retry_then_fail_on_500(tmp_path: Path, tmp_speak_cache: Path):
    """Server 500 error is retried at most 1 time, then fails with SERVER_ERROR."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"error": "Internal Server Error"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "server_err.wav"
        result = await tool.speak(
            input_text="A short sentence.",
            voice="Kavya",
            language="hi",
            output_path=out_path,
        )

        assert not result.success
        assert call_count == 2  # 1 initial + 1 retry
        assert result.error is not None
        assert result.error.code == "SERVER_ERROR"


@pytest.mark.asyncio
async def test_speak_no_retry_on_400(tmp_path: Path, tmp_speak_cache: Path):
    """Client 400 error is never retried."""
    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"error": "Bad Request: Invalid parameter"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "bad_req.wav"
        result = await tool.speak(
            input_text="A short sentence.",
            voice="Kavya",
            language="hi",
            output_path=out_path,
        )

        assert not result.success
        assert call_count == 1  # Exactly 1 call, no retry
        assert result.error is not None
        assert result.error.code == "HTTP_400"


@pytest.mark.asyncio
async def test_speak_plausibility_failure_flagged(tmp_path: Path, tmp_speak_cache: Path):
    """Audio with duration outside 4 to 25 chars/sec is flagged as unverified."""
    # 100 characters text => plausible duration is [100/25, 100/4] = [4.0s, 25.0s]
    # If mock returns 0.2s audio, duration is far too fast (500 chars/s)
    text = "This is a moderately long sentence designed to test the audio pace verification logic in Indic-Speak."

    def mock_handler(request: httpx.Request):
        return httpx.Response(200, content=create_fake_wav_bytes(duration_seconds=0.2))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "plausibility.wav"
        result = await tool.speak(
            input_text=text, voice="Amit", language="en", output_path=out_path
        )

        assert result.success is True  # File was created
        assert result.verified is False  # Verification failed
        assert "outside plausible range" in result.verification_reason


@pytest.mark.asyncio
async def test_speak_file_output_and_verification(tmp_path: Path, tmp_speak_cache: Path):
    """Successful speak call writes WAV to disk and passes verification."""
    # 25 characters text => plausible duration is [25/25, 25/4] = [1.0s, 6.25s]
    text = "Hello world welcome back."

    def mock_handler(request: httpx.Request):
        # 2.0s is inside [1.0s, 6.25s]
        return httpx.Response(200, content=create_fake_wav_bytes(duration_seconds=2.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "valid_output.wav"
        result = await tool.speak(
            input_text=text, voice="Amit", language="en", output_path=out_path
        )

        assert result.success is True
        assert out_path.exists()
        assert out_path.stat().st_size > 0
        assert result.verified is True
        assert result.duration_seconds >= 1.9


@pytest.mark.asyncio
async def test_speak_preview_tier_warning(tmp_path: Path, tmp_speak_cache: Path):
    """Preview tier language (e.g. Sanskrit 'sa') includes a warning field in output."""

    def mock_handler(request: httpx.Request):
        return httpx.Response(200, content=create_fake_wav_bytes(duration_seconds=2.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "preview_lang.wav"
        result = await tool.speak(
            input_text="Namaste sarvebhyah.",
            voice="Aryaman",
            language="sa",
            output_path=out_path,
        )

        assert result.success is True
        assert result.warning is not None
        assert "Preview tier" in result.warning


@pytest.mark.asyncio
async def test_speak_input_from_txt_file(tmp_path: Path, tmp_speak_cache: Path):
    """Input text can be read directly from a .txt file on disk."""
    txt_file = tmp_path / "input_script.txt"
    txt_file.write_text("This text is read from a text file on disk.", encoding="utf-8")

    def mock_handler(request: httpx.Request):
        return httpx.Response(200, content=create_fake_wav_bytes(duration_seconds=3.0))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "from_file.wav"
        result = await tool.speak(
            input_text=str(txt_file),
            voice="Amit",
            language="en",
            output_path=out_path,
        )

        assert result.success is True
        assert result.character_count == len("This text is read from a text file on disk.")


@pytest.mark.asyncio
async def test_speak_three_chunks_joined_via_ffmpeg(tmp_path: Path, tmp_speak_cache: Path):
    """A text long enough to make 3 chunks is joined through ffmpeg, and the final WAV has 24 kHz, mono, 16-bit and a duration equal to the sum of the chunks (within 0.1 s)."""
    import subprocess

    s1 = "Sentence one is written here with plenty of words to make it long enough to exceed one hundred and sixty characters in length."
    s2 = "Sentence two is written here with plenty of words to make it long enough to exceed one hundred and sixty characters in length."
    s3 = "Sentence three is written here with plenty of words to make it long enough to exceed one hundred and sixty characters in length."
    text = f"{s1} {s2} {s3}"

    chunks = chunk_text_by_sentence(text, max_chars=250)
    assert len(chunks) == 3

    # Distinct durations for the 3 chunks
    durations = [6.0, 7.0, 8.0]
    expected_total_duration = sum(durations)  # 21.0s
    call_idx = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_idx
        dur = durations[call_idx]
        call_idx += 1
        return httpx.Response(
            200, content=create_fake_wav_bytes(duration_seconds=dur, sample_rate=24000)
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "three_chunks_joined.wav"
        result = await tool.speak(
            input_text=text, voice="Amit", language="en", output_path=out_path
        )

        assert result.success is True
        assert result.chunks_count == 3
        assert call_idx == 3
        assert out_path.exists()

        # Check properties using wave module
        with wave.open(str(out_path), "rb") as wf:
            assert wf.getframerate() == 24000  # 24 kHz
            assert wf.getnchannels() == 1  # mono
            assert wf.getsampwidth() == 2  # 16-bit (2 bytes)
            wave_duration = wf.getnframes() / float(wf.getframerate())
            assert abs(wave_duration - expected_total_duration) <= 0.1

        # Check properties using ffprobe
        _, ffprobe_bin = get_ffmpeg_binaries()
        assert ffprobe_bin is not None
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration",
            "-of",
            "json",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(proc.stdout)
        stream_info = probe_data["streams"][0]

        assert stream_info["codec_name"] == "pcm_s16le"
        assert int(stream_info["sample_rate"]) == 24000
        assert int(stream_info["channels"]) == 1
        ffprobe_dur = float(stream_info["duration"])
        assert abs(ffprobe_dur - expected_total_duration) <= 0.1


@pytest.mark.asyncio
async def test_speak_content_type_wrong_detects_magic_bytes(tmp_path: Path, tmp_speak_cache: Path):
    """Detects audio format from magic bytes (RIFF/ID3) and never trusts Content-Type header."""
    import subprocess

    # Case 1: Header says text/plain, but body is valid target WAV
    wav_bytes = create_fake_wav_bytes(duration_seconds=2.0)

    def mock_handler_wav(request: httpx.Request):
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=wav_bytes)

    transport_wav = httpx.MockTransport(mock_handler_wav)
    async with httpx.AsyncClient(transport=transport_wav) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "from_misreported_wav.wav"
        res = await tool.speak(
            input_text="Testing audio format detection.",
            voice="Amit",
            language="en",
            output_path=out_path,
        )
        assert res.success is True
        assert out_path.exists()
        assert res.verified is True

    # Case 2: Header says audio/wav, but body is MP3 bytes
    ffmpeg_bin, _ = get_ffmpeg_binaries()
    assert ffmpeg_bin is not None
    mp3_path = tmp_path / "temp_source.mp3"
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2",
            "-c:a",
            "libmp3lame",
            str(mp3_path),
        ],
        check=True,
    )
    mp3_bytes = mp3_path.read_bytes()

    def mock_handler_mp3(request: httpx.Request):
        return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=mp3_bytes)

    transport_mp3 = httpx.MockTransport(mock_handler_mp3)
    async with httpx.AsyncClient(transport=transport_mp3) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        out_path = tmp_path / "from_mp3_to_wav.wav"
        res = await tool.speak(
            input_text="Another test of format conversion.",
            voice="Amit",
            language="en",
            output_path=out_path,
        )
        assert res.success is True
        assert out_path.exists()
        with wave.open(str(out_path), "rb") as wf:
            assert wf.getframerate() == 24000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2


@pytest.mark.asyncio
async def test_speak_male_voice_selection_and_errors(tmp_path: Path, tmp_speak_cache: Path):
    """Test voice='male' resolves to Amit (hi), Arun (ta), Sourav (bn), and tests error on missing male voice."""
    captured_voices = []

    def mock_handler(request: httpx.Request):
        body = json.loads(request.content.decode("utf-8"))
        captured_voices.append(body.get("voice"))
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            content=create_fake_wav_bytes(1.0),
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)

        # 1. Hindi male voice -> Amit
        res_hi = await tool.speak(
            input_text="नमस्ते दोस्तों",
            voice="male",
            language="hi",
            output_path=tmp_path / "hi_male.wav",
        )
        assert res_hi.success is True
        assert captured_voices[-1] == "Amit"

        # 2. Tamil male voice -> Arun
        res_ta = await tool.speak(
            input_text="வணக்கம் நண்பர்களே",
            voice="male",
            language="ta",
            output_path=tmp_path / "ta_male.wav",
        )
        assert res_ta.success is True
        assert captured_voices[-1] == "Arun"

        # 3. Bengali male voice -> Sourav
        res_bn = await tool.speak(
            input_text="নমস্কার বন্ধুরা",
            voice="male",
            language="bn",
            output_path=tmp_path / "bn_male.wav",
        )
        assert res_bn.success is True
        assert captured_voices[-1] == "Sourav"

        # 4. Simulated language without a male voice returns structured error, never silent fallback
        from bodhan_mcp.tools import speak as speak_module

        orig_male_map = dict(speak_module.DEFAULT_MALE_VOICES)
        try:
            # Temporarily remove 'hi' from male voice map
            speak_module.DEFAULT_MALE_VOICES.pop("hi", None)
            res_no_male = await tool.speak(
                input_text="नमस्ते",
                voice="male",
                language="hi",
                output_path=tmp_path / "no_male.wav",
            )
            assert res_no_male.success is False
            assert res_no_male.error is not None
            assert res_no_male.error.code == "NO_MALE_VOICE"
            assert "no documented male voice" in res_no_male.error.message
        finally:
            speak_module.DEFAULT_MALE_VOICES = orig_male_map


@pytest.mark.asyncio
async def test_indic_speak_voice_gender_source_inferred(tmp_path: Path, tmp_speak_cache: Path):
    """Verify that SpeakOutput includes voice_gender_source: 'inferred'."""
    wav_bytes = create_fake_wav_bytes(2.0)

    def mock_handler(request: httpx.Request):
        return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=wav_bytes)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tool = IndicSpeakTool(api_key="mock_key", cache_dir=tmp_speak_cache, client=client)
        res = await tool.speak(
            input_text="Hello world test",
            voice="Kavya",
            language="en",
            output_path=tmp_path / "voice_source.wav",
        )
        assert res.success is True
        assert res.voice_gender_source == "inferred"
        dumped = res.model_dump()
        assert dumped.get("voice_gender_source") == "inferred"
