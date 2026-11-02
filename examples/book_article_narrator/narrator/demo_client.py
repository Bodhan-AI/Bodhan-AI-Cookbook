"""A pretend Bodhan for building and trying the page without a key or any cost.

translate() adds a [demo xx] tag and speak() returns a beep whose length follows the
text length. Replace it with the real client once that exists.
"""

import io
import math
import struct
import wave


class DemoClient:
    def translate(self, text, target_language):
        return f"[demo {target_language}] {text}", False

    def speak(self, text, voice, lang, style=None):
        seconds = min(0.3 + len(text) * 0.02, 3.0)
        rate = 24000
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(
                b"".join(
                    struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                    for i in range(int(seconds * rate))
                )
            )
        return out.getvalue()