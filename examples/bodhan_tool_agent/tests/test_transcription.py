import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from bodhan_mcp.transcription import MAX_WINDOW_FRAMES, SAMPLE_RATE, _wav_windows


class TranscriptionTests(unittest.TestCase):
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
