"""Create the demo input files that are not in git.

    python demo/make_inputs.py              # worksheet image and chapter PDF only (free, offline)
    python demo/make_inputs.py --audio      # also the voice note and clips (about ₹1 of Bodhan credit)

Everything is synthetic, so there are no rights issues:
  * The worksheet and chapter pages are drawn from English text written for this demo, then
    rotated slightly and given light noise so they look like phone photos and scans.
  * The audio is made with Bodhan's own models: English sentences are translated with
    Indic-Translate and spoken with Indic-Speak. No Indian-language text is written by hand
    or by an LLM. Real recordings of real people are a better test; this is the fallback.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"

CHAPTER = [
    ("The Water Cycle", [
        "Water on Earth is always moving. It moves from the oceans to the sky, from the sky to the land, and from the land back to the oceans. This continuous journey is called the water cycle.",
        "The Sun provides the energy that drives the water cycle. Without the heat of the Sun, water would not evaporate and clouds would not form.",
    ]),
    ("1. Evaporation", [
        "When the Sun heats water in oceans, rivers and lakes, some of it turns into water vapour. This process is called evaporation. Water vapour is a gas, so we cannot see it.",
        "Plants also release water vapour through tiny pores in their leaves. This is called transpiration. A large tree can release more than 100 litres of water in a single day.",
    ]),
    ("2. Condensation", [
        "As water vapour rises, the air around it becomes cooler. The vapour cools and turns back into tiny droplets of water. This process is called condensation.",
        "Millions of these droplets come together to form clouds. You can see condensation at home when drops of water form on the outside of a cold glass.",
    ]),
    ("3. Precipitation", [
        "When the droplets in a cloud join and become heavy, they fall to the ground. Water falling from clouds is called precipitation. It can fall as rain, snow or hail.",
        "India receives most of its rain during the monsoon season, from June to September. Farmers depend on this rain to grow crops such as rice.",
    ]),
    ("4. Collection", [
        "Rainwater flows over the land into streams and rivers, and finally reaches the sea. Some of it soaks into the ground and is stored as groundwater.",
        "Wells and handpumps draw water from this groundwater. When the Sun heats the oceans again, the cycle begins once more.",
    ]),
    ("Why the water cycle matters", [
        "The water cycle gives us fresh water for drinking, farming and industry. It also helps to control the temperature of our planet.",
        "We must save water and keep our rivers clean, so that the water cycle can continue to support all living things.",
        "Exercise: Explain the difference between evaporation and condensation in two sentences.",
    ]),
]

WORKSHEET_TITLE = "Class 7 Science - Weekly Test Marks"
WORKSHEET_NOTE = "Fill in the remarks column. The pass mark is 35 out of 100."
WORKSHEET_ROWS = [
    ["Roll No.", "Student", "Marks (out of 100)", "Remarks"],
    ["1", "Asha", "92", "Excellent"],
    ["2", "Ravi", "67", "Good"],
    ["3", "Meena", "48", "Needs practice"],
    ["4", "Arjun", "81", "Very good"],
    ["5", "Farhan", "33", "Below pass mark"],
]

# English source sentences for the audio. Each is translated and spoken by Bodhan's models.
VOICE_NOTE = (
    "hi",
    "Hello everyone. Tomorrow's team meeting will start at ten in the morning instead of nine. "
    "Please bring the sales report for the last three months. "
    "We will also discuss the new project with the school in Chennai. "
    "If you cannot attend, please send me a message by tonight. Thank you.",
)
CLIPS = [
    ("clip_1", "te", "The library will stay open until eight in the evening during the exam week."),
    ("clip_2", "ta", "Please remember to carry an umbrella, as heavy rain is expected this afternoon."),
    ("clip_3", "te", "Our cricket team won the final match by twenty runs on Sunday."),
    ("clip_4", "bn", "The new bus route to the railway station starts from next Monday."),
    ("clip_5", "hi", "The science fair registration closes on Friday at five o'clock."),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]
    for name in names + ["LiberationSans-Regular.ttf", "Helvetica.ttc"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=face) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def photo_effect(page: Image.Image, seed: int, angle: float) -> Image.Image:
    """Tilt a little, add paper tone and grain, soften: like a phone photo or a flatbed scan."""
    rng = random.Random(seed)
    page = page.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(232, 228, 218))
    grain = Image.effect_noise(page.size, 14).convert("RGB")
    page = Image.blend(page, grain, 0.06)
    tone = Image.new("RGB", page.size, (250, 246, 236))
    page = Image.blend(page, tone, 0.08 + rng.random() * 0.04)
    return page.filter(ImageFilter.GaussianBlur(0.6))


def make_worksheet(path: Path) -> None:
    page = Image.new("RGB", (1240, 1000), "white")
    draw = ImageDraw.Draw(page)
    draw.text((80, 70), WORKSHEET_TITLE, font=font(44, bold=True), fill="black")
    draw.text((80, 140), WORKSHEET_NOTE, font=font(28), fill="black")
    widths, x0, y0, row_h = [170, 260, 370, 300], 80, 230, 90
    for r, row in enumerate(WORKSHEET_ROWS):
        x = x0
        for c, cell in enumerate(row):
            draw.rectangle([x, y0 + r * row_h, x + widths[c], y0 + (r + 1) * row_h], outline="black", width=3)
            draw.text((x + 18, y0 + r * row_h + 28), cell, font=font(30, bold=(r == 0)), fill="black")
            x += widths[c]
    photo_effect(page, seed=7, angle=1.6).save(path, "JPEG", quality=88)


def make_chapter(path: Path) -> None:
    pages = []
    for number, (heading, paragraphs) in enumerate(CHAPTER, 1):
        page = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi
        draw = ImageDraw.Draw(page)
        draw.text((110, 70), "Science, Class 7  |  Chapter 9", font=font(24), fill=(90, 90, 90))
        y = 170
        draw.text((110, y), heading, font=font(48 if number == 1 else 40, bold=True), fill="black")
        y += 110
        for paragraph in paragraphs:
            for line in wrap(draw, paragraph, font(32), 1020):
                draw.text((110, y), line, font=font(32), fill="black")
                y += 48
            y += 30
        draw.text((600, 1660), str(number), font=font(26), fill=(90, 90, 90))
        pages.append(photo_effect(page, seed=number, angle=(-0.8 if number % 2 else 0.6)).convert("RGB"))
    pages[0].save(path, "PDF", resolution=150, save_all=True, append_images=pages[1:])


def make_audio() -> float:
    """Translate English sentences and speak them with Bodhan's models. Returns the cost in rupees."""
    sys.path.insert(0, str(HERE.parent / "src"))
    try:
        from dotenv import load_dotenv

        load_dotenv(HERE.parent / ".env")
    except ImportError:
        pass
    from bodhan_mcp import langs, media
    from bodhan_mcp.client import BodhanClient
    from bodhan_mcp.config import Settings

    client = BodhanClient(Settings.from_env(max_cost_inr=3.0))

    def say(english: str, language: str, out: Path, gender: str) -> None:
        text = client.translate(english, language)["translation"]
        voice = langs.pick_voice(language, None, gender)
        clips = [media.wav_to_pcm(client.speak(chunk, voice, language)) for chunk in _sentences(text)]
        rate = clips[0][1]
        import numpy as np

        gap = np.zeros(int(rate * 0.35), dtype=np.int16)
        audio = np.concatenate([x for samples, _ in clips for x in (samples, gap)])
        media.export_audio(audio, rate, out)
        print(f"  {out.relative_to(HERE)}  ({langs.LANGUAGE_NAMES[language]}, voice {voice})")

    language, english = VOICE_NOTE
    say(english, language, INPUTS / "voice_note" / "voice_note.mp3", "male")
    for name, language, english in CLIPS:
        say(english, language, INPUTS / "clips" / f"{name}.mp3", "female" if name in ("clip_1", "clip_4") else "male")
    return client.ledger.spent_inr


def _sentences(text: str) -> list[str]:
    from bodhan_mcp import textops

    return textops.chunk_for_speech(text, 280)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the demo input files.")
    parser.add_argument("--audio", action="store_true", help="Also create the voice note and clips with Bodhan's models (about ₹1).")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    for folder in ("worksheet", "chapter", "voice_note", "clips"):
        (INPUTS / folder).mkdir(parents=True, exist_ok=True)
    make_worksheet(INPUTS / "worksheet" / "worksheet.jpg")
    print("  inputs/worksheet/worksheet.jpg")
    make_chapter(INPUTS / "chapter" / "chapter.pdf")
    print(f"  inputs/chapter/chapter.pdf  ({len(CHAPTER)} pages)")
    if args.audio:
        cost = make_audio()
        print(f"Audio made with Bodhan models for ₹{cost:.3f}.")
    else:
        print("Run again with --audio to make the voice note and clips (about ₹1 of Bodhan credit).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
