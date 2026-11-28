"""Regenerate the synthetic sample inputs: comment-card images and voice notes.

    python tools/make_samples.py cards     # needs Pillow and Noto Sans fonts
    python tools/make_samples.py voice     # needs a key for indic-speak; speaks the notes with indic-speak

Nothing here is a real customer. The voice notes are synthetic speech, which is cleaner
than a phone recording, so transcription accuracy on them is an upper bound.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))

CARDS = {
    "c01_hi.png": ("Noto Sans Devanagari", "ग्राहक सुझाव कार्ड", ["बिल में गलत दाम लगाया गया था।", "कृपया जाँच करें।"]),
    "c02_ta.png": ("Noto Sans Tamil", "வாடிக்கையாளர் கருத்து அட்டை", ["பழங்கள் அழுகிப் போயிருந்தன.", "பணத்தைத் திருப்பித் தரவும்."]),
    "c03_bn.png": ("Noto Sans Bengali", "গ্রাহক মতামত কার্ড", ["কর্মচারীরা খুব ভালো ব্যবহার করেছেন।", "ধন্যবাদ।"]),
}

VOICE_NOTES = {
    "v01_hi.wav": ("hi", "Amit", "नमस्ते, मैंने कल जो आटा मँगवाया था उसकी थैली फटी हुई थी। कृपया इसे बदल दीजिए।"),
    "v02_ta.wav": ("ta", "Anitha", "உங்கள் கடையில் காய்கறிகள் எப்போதும் புதிதாக இருக்கும். மிக்க நன்றி."),
    "v03_bn.wav": ("bn", "Sourav", "আমার অর্ডার এখনও আসেনি, দুই দিন হয়ে গেল। খুব খারাপ পরিষেবা।"),
}


def font_file(family: str) -> str:
    return subprocess.run(["fc-match", "-f", "%{file}", family], check=True, capture_output=True, text=True).stdout


def make_cards() -> None:
    from PIL import Image, ImageDraw, ImageFont

    for name, (family, heading, lines) in CARDS.items():
        path = font_file(family)
        image = Image.new("RGB", (1000, 560), "#fbf8ef")
        draw = ImageDraw.Draw(image)
        draw.rectangle([20, 20, 980, 540], outline="#444444", width=3)
        draw.text((60, 50), heading, font=ImageFont.truetype(path, 44), fill="#222222")
        draw.line([60, 130, 940, 130], fill="#999999", width=2)
        for row, line in enumerate(lines):
            draw.text((60, 180 + row * 110), line, font=ImageFont.truetype(path, 50), fill="#1a2a6c")
        target = RECIPE / "sample_data" / "cards" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, optimize=True)
        print("wrote", target.relative_to(RECIPE))


def make_voice() -> None:
    from dotenv import load_dotenv

    from feedback_analyser.audio import join_wavs
    from feedback_analyser.bodhan import BodhanClient
    from feedback_analyser.respond import speech_chunks

    load_dotenv(RECIPE / ".env")
    client = BodhanClient()
    for name, (lang, voice, text) in VOICE_NOTES.items():
        target = RECIPE / "sample_data" / "voice" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(join_wavs([client.speak(chunk, voice, lang) for chunk in speech_chunks(text)]))
        print("wrote", target.relative_to(RECIPE))


if __name__ == "__main__":
    wanted = sys.argv[1:] or ["cards", "voice"]
    if "cards" in wanted:
        make_cards()
    if "voice" in wanted:
        make_voice()
