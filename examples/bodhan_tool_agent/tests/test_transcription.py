import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from bodhan_mcp.transcription import MAX_WINDOW_FRAMES, SAMPLE_RATE, _wav_windows


class TranscriptionTests(unittest.TestCase):
    def test_prefers_latest_sustained_pause_without_dropping_samples(self):
        duration = 9 * SAMPLE_RATE
        pcm = bytearray(b"\x00\x10" * duration)
        for start, end in ((6.2, 6.5), (7.0, 7.4), (7.8, 7.88)):
            start_frame = round(start * SAMPLE_RATE)
            end_frame = round(end * SAMPLE_RATE)
            pcm[2 * start_frame:2 * end_frame] = b"\x00\x00" * (end_frame - start_frame)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            with wave.open(str(path), "wb") as out:
                out.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                out.writeframes(pcm)

            windows = list(_wav_windows(path, 8 * SAMPLE_RATE, prefer_pauses=True))
            self.assertEqual([(start, end) for start, end, _ in windows], [(0, 7.2), (7.2, 9)])
            reconstructed = bytearray()
            for _, _, data in windows:
                with wave.open(io.BytesIO(data)) as window:
                    self.assertLessEqual(window.getnframes(), 8 * SAMPLE_RATE)
                    reconstructed.extend(window.readframes(window.getnframes()))
            self.assertEqual(reconstructed, pcm)

            fixed = list(_wav_windows(path, 8 * SAMPLE_RATE))
            self.assertEqual([(start, end) for start, end, _ in fixed], [(0, 8), (8, 9)])

    def test_wav_windows_preserve_every_frame_at_the_boundary(self):
        for frames, expected in (
            (MAX_WINDOW_FRAMES, [MAX_WINDOW_FRAMES]),
            (MAX_WINDOW_FRAMES + 1, [MAX_WINDOW_FRAMES, 1]),
        ):
            with self.subTest(frames=frames), TemporaryDirectory() as directory:
                path = Path(directory) / "input.wav"
                pcm = b"\x01\x02" * frames
                with wave.open(str(path), "wb") as out:
                    out.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                    out.writeframes(pcm)
                windows = list(_wav_windows(path))
                self.assertEqual(windows[0][0], 0)
                self.assertEqual(windows[-1][1], frames / SAMPLE_RATE)
                self.assertEqual(len(windows), len(expected))
                reconstructed = bytearray()
                for window, nframes in zip(windows, expected, strict=True):
                    with wave.open(io.BytesIO(window[2])) as segment:
                        self.assertEqual(segment.getnframes(), nframes)
                        reconstructed.extend(segment.readframes(nframes))
                self.assertEqual(reconstructed, pcm)
