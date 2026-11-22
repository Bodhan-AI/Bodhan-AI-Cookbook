import io
import unittest
import wave
from decimal import Decimal
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from bodhan_mcp.request_budget import BudgetExhausted, RequestBudget, active_budget
from bodhan_mcp.transcription import MAX_WINDOW_FRAMES, SAMPLE_RATE, _wav_windows, transcribe_audio


class TranscriptionTests(unittest.TestCase):
    def test_thirty_seconds_fits_exactly_but_one_more_sample_exceeds_budget(self):
        for extra_frame in (0, 1):
            with self.subTest(extra_frame=extra_frame), TemporaryDirectory() as directory:
                path = Path(directory) / "input.wav"
                with wave.open(str(path), "wb") as out:
                    out.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                    out.writeframes(b"\x01\x02" * (MAX_WINDOW_FRAMES + extra_frame))

                budget = RequestBudget(Decimal("0.05"))
                token = active_budget.set(budget)
                try:
                    with (
                        patch.dict("os.environ", {"BODHAN_TRANSCRIBE_KEY": "test"}),
                        patch("bodhan_mcp.transcription._normalize", side_effect=copyfile),
                        patch.object(httpx.Client, "post", return_value=httpx.Response(200, json={"text": "Hello"})) as post,
                    ):
                        if extra_frame:
                            with self.assertRaises(BudgetExhausted):
                                transcribe_audio(str(path))
                        else:
                            result = transcribe_audio(str(path))
                            self.assertEqual(result.duration_seconds, 30)
                        post.assert_called_once()
                    self.assertEqual(budget.total, Decimal("0.05"))
                finally:
                    active_budget.reset(token)

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
