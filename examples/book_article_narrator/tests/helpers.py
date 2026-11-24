"""Small helpers shared by the tests and the try_ scripts: fake audio and a fake Bodhan."""

import io
import math
import struct
import wave

from narrator.errors import BodhanAPIError


def make_tone(seconds=0.2, frequency=440, rate=24000):
    """A simple beep as WAV bytes (PCM16, mono), in the same format Bodhan returns."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * frequency * i / rate)))
            for i in range(int(seconds * rate))
        )
        w.writeframes(frames)
    return out.getvalue()


def duration(wav_bytes):
    """Length of a WAV in seconds."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / w.getframerate()


class FakeClient:
    """Stands in for Bodhan, so tests cost nothing and need no key or internet."""

    def __init__(self, truncated_paragraphs=(), fail_on_speak=False):
        self.truncated_paragraphs = set(truncated_paragraphs)  # paragraph numbers
        self.fail_on_speak = fail_on_speak
        self.translate_calls = []
        self.speak_calls = []

    def translate(self, text, target_language):
        self.translate_calls.append(text)
        number = len(self.translate_calls)
        return f"[{target_language}] {text}", number in self.truncated_paragraphs

    def speak(self, text, voice, lang, style=None):
        if self.fail_on_speak:
            raise BodhanAPIError("Speech failed.")
        self.speak_calls.append((text, voice, lang, style))
        return make_tone(0.2)
    