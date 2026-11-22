"""Unit tests for FFmpeg conversion, duration check, and join helpers."""

import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from bodhan_mcp.ffmpeg_utils import (
    convert_to_wav_16k_mono,
    find_ffmpeg,
    get_audio_duration,
    join_audio_files,
)


class TestFFmpegUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_wav(self, path: Path, duration_s: float = 1.0, framerate: int = 8000) -> None:
        """Creates a small valid PCM WAV file for testing."""
        nframes = int(duration_s * framerate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(framerate)
            w.writeframes(b"\x00\x00" * nframes)

    def test_find_ffmpeg(self):
        # Must locate ffmpeg or raise actionable error
        ffmpeg_bin = find_ffmpeg()
        self.assertTrue(Path(ffmpeg_bin).exists())

    def test_get_audio_duration(self):
        test_wav = self.temp_dir / "test.wav"
        self._create_synthetic_wav(test_wav, duration_s=2.5, framerate=16000)
        dur = get_audio_duration(test_wav)
        self.assertAlmostEqual(dur, 2.5, places=1)

    def test_convert_to_wav_16k_mono(self):
        raw_wav = self.temp_dir / "raw_8k.wav"
        self._create_synthetic_wav(raw_wav, duration_s=1.0, framerate=8000)

        out_wav = self.temp_dir / "out_16k.wav"
        convert_to_wav_16k_mono(raw_wav, out_wav)

        self.assertTrue(out_wav.exists())
        with wave.open(str(out_wav), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)

    def test_join_audio_files(self):
        w1 = self.temp_dir / "w1.wav"
        w2 = self.temp_dir / "w2.wav"
        self._create_synthetic_wav(w1, duration_s=1.0, framerate=16000)
        self._create_synthetic_wav(w2, duration_s=2.0, framerate=16000)

        joined = self.temp_dir / "joined.wav"
        join_audio_files([w1, w2], joined)
        self.assertTrue(joined.exists())
        dur = get_audio_duration(joined)
        self.assertAlmostEqual(dur, 3.0, places=1)


if __name__ == "__main__":
    unittest.main()
