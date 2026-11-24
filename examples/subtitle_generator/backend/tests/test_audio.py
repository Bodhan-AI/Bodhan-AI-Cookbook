import os
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

from app import audio
from app.config import settings


def make_video(path: str, seconds: int = 3, has_audio: bool = True) -> None:
    """Write a small synthetic test video with the bundled FFmpeg."""
    cmd = [audio.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=5"]
    if has_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=500:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", path], check=True)


def wav_duration(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ffmpeg_comes_from_the_virtual_environment(self):
        with patch.object(settings, "ffmpeg_path", ""), patch("shutil.which", return_value="C:/global/ffmpeg.exe"):
            path = audio.find_ffmpeg()
        assert path is not None
        self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(sys.prefix)))

    def test_broken_override_is_reported(self):
        with patch.object(settings, "ffmpeg_path", "does/not/exist/ffmpeg.exe"):
            self.assertIsNone(audio.find_ffmpeg())

    def test_probe_reads_duration_and_audio(self):
        with_audio, silent = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_video(with_audio)
        make_video(silent, has_audio=False)
        duration, has_audio = audio.probe(with_audio)
        assert duration is not None
        self.assertAlmostEqual(duration, 3.0, delta=0.2)
        self.assertTrue(has_audio)
        self.assertFalse(audio.probe(silent)[1])

    def test_extract_and_cut_audio(self):
        video, wav, piece = (os.path.join(self.tmp, name) for name in ("v.mp4", "a.wav", "piece.wav"))
        make_video(video, seconds=10)
        audio.extract_audio(video, wav)
        self.assertAlmostEqual(wav_duration(wav), 10.0, delta=0.1)
        audio.cut_audio(wav, 2.0, 6.5, piece)
        self.assertAlmostEqual(wav_duration(piece), 4.5, delta=0.1)

    def test_long_pauses_start_a_new_piece(self):
        # 0-2 and 2.3-7 are joined (0.3 s breath); the 1 s pause at 7-8 starts a new piece (7 s already);
        # 8-9 and 10-12 are joined (the piece is still short when the 1 s pause comes)
        speech = [(0.0, 2.0), (2.3, 7.0), (8.0, 9.0), (10.0, 12.0)]
        pieces = audio.plan_pieces(speech, max_seconds=24)
        self.assertEqual([(p.start, p.end) for p in pieces], [(0.0, 7.0), (8.0, 12.0)])
        self.assertEqual(pieces[0].speech, [(0.0, 2.0), (2.3, 7.0)])
        self.assertEqual(pieces[1].speech, [(8.0, 9.0), (10.0, 12.0)])

    def test_pieces_stay_under_the_request_limit(self):
        speech = [(0.0, 5.0), (5.3, 15.0), (15.3, 26.0), (26.3, 30.0)]  # only short breaths
        pieces = audio.plan_pieces(speech, max_seconds=24)
        self.assertEqual([(p.start, p.end) for p in pieces], [(0.0, 15.0), (15.3, 30.0)])

    def test_cuts_always_move_forward(self):
        # A cut chooser that points before the piece (as a short CHUNK_SECONDS once allowed) must not stall
        for max_seconds in (5, 6, 24):
            pieces = audio.plan_pieces([(0.0, 60.0)], max_seconds=max_seconds, cut_at=lambda earliest, latest: earliest - 3)
            self.assertEqual(pieces[0].start, 0.0)
            self.assertEqual(pieces[-1].end, 60.0)
            for a, b in zip(pieces, pieces[1:], strict=False):
                self.assertEqual(a.end, b.start)
                self.assertGreater(a.end, a.start)

    def test_speech_without_pauses_is_cut_at_fixed_points(self):
        pieces = audio.plan_pieces([(0.0, 50.0)], max_seconds=24)
        self.assertEqual([(p.start, p.end) for p in pieces], [(0.0, 24.0), (24.0, 48.0), (48.0, 50.0)])


if __name__ == "__main__":
    unittest.main()
