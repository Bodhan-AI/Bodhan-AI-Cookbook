"""Media checks use independent PCM fixtures and real FFmpeg decoding."""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from bodhan_dub.media import (
    MediaError,
    Placement,
    extract_audio,
    fit_clip,
    probe_input,
    quiet_tail,
    render_video,
    source_windows,
)


def write_pcm(path, duration, regions):
    rate = 16_000
    samples = array("h", [0]) * round(duration * rate)
    for start, end, amplitude, frequency, modulation in regions:
        first, stop = round(start * rate), round(end * rate)
        for index in range(first, min(stop, len(samples))):
            time = index / rate
            carrier = math.sin(2 * math.pi * frequency * time)
            envelope = 1.0 + modulation * math.sin(2 * math.pi * 5 * time)
            samples[index] = round(amplitude * carrier * envelope)
    if sys.byteorder == "big":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
        check=True, capture_output=True,
    ).stdout


class MediaTests(unittest.TestCase):
    def test_forced_split_preserves_audio_and_bounds_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav = root / "source.wav"
            write_pcm(wav, 61, (
                (3, 40, 5000, 230, 0.40),
                (43, 46, 4200, 280, 0.45),
                (49, 58, 1800, 440, 0.00),
            ))
            windows = source_windows(wav, root / "windows")
            self.assertEqual(len(windows), 3)
            self.assertAlmostEqual(windows[0].start, 3, delta=0.12)
            self.assertAlmostEqual(windows[1].end, 40, delta=0.12)
            self.assertAlmostEqual(windows[2].start, 43, delta=0.12)
            self.assertAlmostEqual(windows[2].end, 46, delta=0.12)
            self.assertAlmostEqual(windows[0].end - windows[1].start, 0.24, places=5)
            self.assertTrue(windows[1].forced_overlap)
            with wave.open(str(wav), "rb") as source:
                for window in windows:
                    with wave.open(str(window.wav), "rb") as submitted:
                        frames = submitted.getnframes()
                        self.assertLessEqual(frames, 30 * 16_000)
                        self.assertAlmostEqual(frames / 16_000, window.end - window.start, places=6)
                        source.setpos(round(window.start * 16_000))
                        self.assertEqual(submitted.readframes(frames), source.readframes(frames))
            self.assertAlmostEqual(quiet_tail(wav, 46.1, 51), 49.0, delta=0.04)

    def test_candidate_decodes_by_signature_and_fits_within_tempo_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disguised = root / "claimed.mp3"
            write_pcm(disguised, 3.0, ((0.20, 2.20, 5000, 640, 0.0),))
            fit = fit_clip(disguised.read_bytes(), root, "placed", 1.86)
            self.assertIsNone(fit.reason)
            self.assertLessEqual(fit.duration, 1.86)
            self.assertGreater(fit.duration, 1.65)
            self.assertGreater(fit.tempo, 1.00)
            self.assertLessEqual(fit.tempo, 1.20)
            self.assertAlmostEqual(fit.raw_duration, 3.0)
            self.assertAlmostEqual(fit.trim_start, 0.16, delta=0.021)
            self.assertAlmostEqual(fit.trim_end, 0.74, delta=0.021)
            with wave.open(str(fit.path), "rb") as content:
                self.assertEqual(content.getframerate(), 24_000)
                self.assertAlmostEqual(content.getnframes() / 24_000, fit.duration, places=7)

            refused = fit_clip(disguised.read_bytes(), root, "overrun", 1.0)
            self.assertIsNone(refused.path)
            self.assertEqual(refused.reason, "tts_cannot_fit_within_tempo_limit")
            self.assertLessEqual(refused.tempo, 1.20)

            write_pcm(disguised, 1.0, ())
            self.assertEqual(fit_clip(disguised.read_bytes(), root, "silent", 2).reason, "tts_silence")
            self.assertTrue(fit_clip(b"invalid file", root, "invalid", 2).reason.startswith("tts_decode_failed"))

    def test_a_short_peak_does_not_prevent_speech_loudness_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "speech.wav"
            write_pcm(payload, 3.0, (
                (0, 3.0, 800, 430, 0.1),
                (1.0, 1.01, 26_000, 430, 0.0),
            ))
            clip = fit_clip(payload.read_bytes(), root, "placed", 3.1)
            self.assertIsNotNone(clip.path)
            self.assertAlmostEqual(clip.loudness_lufs, -16.0, delta=1.0)
            with wave.open(str(clip.path), "rb") as sample:
                samples = array("h", sample.readframes(sample.getnframes()))
            self.assertLess(max(map(abs, samples)), 29_500)

    def test_delayed_source_audio_keeps_position_and_has_full_video_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_video = root / "delayed.mkv"
            ffmpeg(
                "-f", "lavfi", "-i", "color=c=black:s=96x64:r=20:d=61",
                "-itsoffset", "2", "-f", "lavfi", "-i", "sine=f=440:d=4",
                "-c:v", "libx264", "-c:a", "pcm_s16le", input_video,
            )
            info = probe_input(input_video)
            self.assertAlmostEqual(info.duration, 61, delta=0.05)
            wav = extract_audio(input_video, root / "source", info.duration, video_offset=info.video_offset)
            with wave.open(str(wav), "rb") as source:
                rate = source.getframerate()
                quiet = source.readframes(round(1.8 * rate))
                self.assertEqual(quiet, bytes(len(quiet)))
                source.setpos(round(2.5 * rate))
                signal = array("h", source.readframes(rate // 8))
                self.assertGreater(max(map(abs, signal)), 1000)
            self.assertEqual(source_windows(wav, root / "windows"), [])

            write_pcm(root / "single.wav", 1.5, ((0, 1.5, 5500, 800, 0.0),))
            clip = fit_clip((root / "single.wav").read_bytes(), root, "placed", 2)
            output = root / "video.mp4"
            result = render_video(
                input_video, info, [Placement(clip.path, 6, clip.duration)], output,
            )
            self.assertTrue(result["duration_within_target"])
            self.assertTrue(result["speech_loudness_within_target"])
            self.assertAlmostEqual(result["duration"], 61, delta=0.1)
            audio = ffmpeg(
                "-ss", "6.1", "-i", output, "-t", "0.4",
                "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
            )
            self.assertGreater(max(map(abs, array("h", audio))), 1000)

            with self.assertRaisesRegex(MediaError, "overlap"):
                render_video(
                    input_video, info,
                    [Placement(clip.path, 6, clip.duration), Placement(clip.path, 7, clip.duration)],
                    root / "overlap.mp4",
                )
