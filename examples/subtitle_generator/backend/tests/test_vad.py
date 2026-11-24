import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from app import audio, vad

STEP = 0.032  # one window: 512 samples at 16 kHz
LOUD, QUIET = -20.0, -90.0

# The cookbook's shared sample (repo root), or a copy in this recipe's sample_data/
_UP = Path(__file__).resolve().parents  # shorter outside the cookbook (e.g. tests mounted in the Docker image)
SAMPLE_WAV = next((p for p in (_UP[i] / "sample_data" / "sample_hindi.wav" for i in (2, 4) if i < len(_UP)) if p.exists()), None)


def windows(*runs: tuple[float, float]) -> list[float]:
    """Per-window values from (value, seconds) runs."""
    return [value for value, seconds in runs for _ in range(round(seconds / STEP))]


def analysis(probability_runs, loudness_runs):
    probs, loud = windows(*probability_runs), windows(*loudness_runs)
    return vad.analysis_from(probs, loud, STEP, len(probs) * STEP)


class SpeechTests(unittest.TestCase):
    def speech(self, *runs):
        probs = windows(*runs)
        return vad.stretches(vad.speech_flags(probs), STEP, len(probs) * STEP)

    def test_speech_between_pauses(self):
        speech = self.speech((0.0, 1.0), (0.9, 2.0), (0.1, 1.0), (0.8, 1.5), (0.0, 1.0))
        self.assertEqual(len(speech), 2)
        for (start, end), (expected_start, expected_end) in zip(speech, [(0.85, 3.15), (3.85, 5.65)], strict=True):
            self.assertAlmostEqual(start, expected_start, delta=2 * STEP)
            self.assertAlmostEqual(end, expected_end, delta=2 * STEP)

    def test_breaths_do_not_split_speech(self):
        [(start, end)] = self.speech((0.9, 2.0), (0.1, 0.25), (0.9, 2.0))
        self.assertEqual(start, 0.0)
        self.assertAlmostEqual(end, 4.25, delta=2 * STEP)

    def test_middling_probability_keeps_speech_going_but_does_not_start_it(self):
        [(start, end)] = self.speech((0.4, 1.0), (0.9, 1.0), (0.4, 1.0), (0.0, 1.0))
        self.assertAlmostEqual(start, 0.85, delta=2 * STEP)
        self.assertAlmostEqual(end, 3.15, delta=2 * STEP)

    def test_blips_are_dropped(self):
        speech = self.speech((0.0, 1.0), (0.9, 0.1), (0.0, 1.0), (0.9, 1.0), (0.0, 1.0))
        self.assertEqual(len(speech), 1)
        self.assertAlmostEqual(speech[0][0], 1.95, delta=2 * STEP)

    def test_padding_stays_inside_the_audio(self):
        speech = self.speech((0.9, 1.0), (0.0, 1.0), (0.9, 1.0))
        self.assertEqual(speech[0][0], 0.0)
        self.assertAlmostEqual(speech[-1][1], len(windows((0, 1.0), (0, 1.0), (0, 1.0))) * STEP)


class SoundTests(unittest.TestCase):
    def test_singing_that_silero_ignores_is_still_sent(self):
        # Silero hears speech only at 2-4 s; the music and singing around it are loud throughout 0-10 s
        found = analysis([(0.0, 2.0), (0.9, 2.0), (0.0, 6.0)], [(LOUD, 10.0)])
        self.assertEqual(len(found.speech), 1)
        [(start, end)] = found.sound
        self.assertEqual(start, 0.0)
        self.assertAlmostEqual(end, 10.0, delta=2 * STEP)

    def test_quiet_is_skipped(self):
        found = analysis([(0.0, 7.0)], [(LOUD, 2.0), (QUIET, 3.0), (LOUD, 2.0)])
        self.assertEqual(len(found.sound), 2)
        self.assertAlmostEqual(found.sound[0][1], 2.15, delta=2 * STEP)
        self.assertAlmostEqual(found.sound[1][0], 4.85, delta=2 * STEP)

    def test_quiet_speech_that_loudness_misses_is_still_sent(self):
        found = analysis([(0.0, 3.0), (0.9, 1.0), (0.0, 1.0)], [(LOUD, 2.0), (QUIET, 3.0)])
        self.assertEqual(len(found.sound), 2)
        self.assertAlmostEqual(found.sound[1][0], 2.85, delta=2 * STEP)

    def test_silence_gives_nothing_to_send(self):
        self.assertEqual(analysis([(0.0, 3.0)], [(QUIET, 3.0)]).sound, [])

    def test_threshold_follows_the_track_but_stays_in_range(self):
        self.assertEqual(vad.sound_threshold([-10.0] * 100), -45.0)
        self.assertEqual(vad.sound_threshold([-50.0] * 100), vad.QUIETEST_SOUND_DB)
        self.assertEqual(vad.sound_threshold([2.0] * 100), vad.LOUDEST_THRESHOLD_DB)


class CutAndTimingTests(unittest.TestCase):
    def test_long_sound_is_cut_at_its_quietest_moment(self):
        # 60 s of music with a dip at 21 s: the first cut goes there, not at the 24 s limit
        found = analysis([(0.0, 60.0)], [(LOUD, 21.0), (-30.0, 0.1), (LOUD, 38.9)])
        pieces = audio.plan_pieces(found.sound, 24, cut_at=found.quietest_point)
        self.assertAlmostEqual(pieces[0].end, 21.05, delta=2 * STEP)
        self.assertTrue(all(p.end - p.start <= 24 for p in pieces))
        self.assertAlmostEqual(pieces[-1].end, 60.0, delta=2 * STEP)
        self.assertEqual([p.start for p in pieces[1:]], [p.end for p in pieces[:-1]])  # nothing skipped

    def test_cuts_avoid_speech(self):
        # Equally loud throughout, speech everywhere except 19-20 s: the cut falls in that gap
        found = analysis([(0.9, 19.0), (0.0, 1.0), (0.9, 20.0)], [(LOUD, 40.0)])
        cut = found.quietest_point(18.0, 24.0)
        self.assertGreaterEqual(cut, 19.0 - STEP)
        self.assertLessEqual(cut, 20.0 + STEP)

    def test_lines_go_on_speech_when_silero_hears_enough(self):
        found = analysis([(0.0, 1.0), (0.9, 8.0), (0.0, 1.0)], [(LOUD, 10.0)])
        self.assertEqual(found.timing_stretches(0.0, 10.0, found.sound), found.speech)

    def test_lines_go_on_sound_when_silero_hears_little(self):
        found = analysis([(0.0, 8.0), (0.9, 1.0), (0.0, 1.0)], [(LOUD, 10.0)])
        self.assertEqual(found.timing_stretches(0.0, 10.0, found.sound), found.sound)


class RealAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_tone_is_sent_though_it_is_not_speech(self):
        # A tone from 0-2 s and 4-7 s, silence elsewhere: no speech, but two stretches of sound
        wav = os.path.join(self.tmp, "tone.wav")
        tone = r"0.5*sin(2*PI*440*t)*(between(t\,0\,2)+between(t\,4\,7))"  # commas escaped for FFmpeg's filter syntax
        subprocess.run([audio.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc={tone}:s=16000:d=9", "-c:a", "pcm_s16le", wav], check=True)
        found = vad.analyse(wav)
        self.assertEqual(found.speech, [])
        self.assertEqual(len(found.sound), 2)
        for (start, end), (expected_start, expected_end) in zip(found.sound, [(0.0, 2.0), (4.0, 7.0)], strict=True):
            self.assertAlmostEqual(start, expected_start, delta=0.25)
            self.assertAlmostEqual(end, expected_end, delta=0.25)

    @unittest.skipUnless(SAMPLE_WAV, "sample_data/sample_hindi.wav not found")
    def test_pause_in_real_speech_is_found(self):
        # The sample twice, with 2 s of silence between: two stretches, split inside the silence
        with wave.open(str(SAMPLE_WAV), "rb") as src:
            params, frames = src.getparams(), src.readframes(src.getnframes())
        first = len(frames) / 2 / params.framerate
        path = os.path.join(self.tmp, "paused.wav")
        with wave.open(path, "wb") as out:
            out.setparams(params)
            out.writeframes(frames + b"\x00\x00" * params.framerate * 2 + frames)

        found = vad.analyse(path)
        for stretches in (found.speech, found.sound):
            self.assertEqual(len(stretches), 2)
            self.assertLess(stretches[0][0], 0.3)
            self.assertGreater(stretches[0][1], first - 0.5)
            self.assertLess(stretches[0][1], first + 0.5)
            self.assertGreater(stretches[1][0], first + 1.5)
            self.assertLess(stretches[1][0], first + 2.3)
