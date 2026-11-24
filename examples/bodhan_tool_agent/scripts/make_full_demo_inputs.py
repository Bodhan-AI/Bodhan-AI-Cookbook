"""Create the two larger synthetic demo inputs in sample_data/demo/ (git-ignored). Costs about ₹0.35.

    python scripts/make_full_demo_inputs.py

* chapter_scan.pdf: an 8-page "scanned" Hindi chapter. The English source text below is original to
  this recipe; Indic-Translate (Bodhan) turns it into Hindi, and each page is rendered as a slightly
  rotated, noisy grayscale image, so the PDF has no text layer and must be read with OCR.
* clips/clip1.wav … clip5.wav: five short spoken clips in Hindi, Telugu, Kannada, Telugu and Tamil,
  produced by translating English sentences with Indic-Translate and speaking them with Indic-Speak.
  File names do not reveal the language. They are AI-generated speech.
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

OUT = HERE / "sample_data" / "demo"
FONT = "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"
FONT_FALLBACKS = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                  "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf"]

TITLE = "Chapter 4: The Water Cycle"
CHAPTER = [
    "Water on Earth is always moving. It travels from the oceans to the sky, from the sky to the land, and from "
    "the land back to the oceans. This endless journey is called the water cycle. The same water has been moving "
    "in this way for millions of years, so the water you drink today may once have fallen as rain on a forest "
    "long ago.",
    "The journey begins with evaporation. When the sun heats the surface of oceans, lakes and rivers, some of the "
    "water turns into an invisible gas called water vapour. Warm air carries this vapour upwards. Evaporation is "
    "faster on hot, dry and windy days, which is why wet clothes dry quickly in summer.",
    "Plants also release water into the air. They draw water from the soil through their roots and lose some of "
    "it as vapour through tiny pores in their leaves. This process is called transpiration. A single large tree "
    "can release hundreds of litres of water into the air on a hot day.",
    "As warm, moist air rises, it cools. Cool air cannot hold as much vapour as warm air, so the vapour turns back "
    "into tiny droplets of liquid water around specks of dust. This change is called condensation. Billions of "
    "these droplets together form the clouds we see in the sky.",
    "When the droplets in a cloud join together and grow too heavy, they fall to the ground as precipitation. "
    "Precipitation can be rain, snow, sleet or hail, depending on the temperature of the air. In India most of "
    "the yearly rainfall arrives during the monsoon months, from June to September.",
    "Some of the water that falls on land flows over the surface into streams and rivers. This is called runoff. "
    "Rivers carry the water back to the sea. On the way, runoff can wash away loose soil, which is why trees and "
    "grass that hold the soil in place are so important.",
    "Not all rainwater flows away. Some of it soaks into the ground and fills the spaces between soil particles "
    "and rocks. This stored water is called groundwater. Wells and hand pumps draw on groundwater, and in many "
    "villages it is the main source of drinking water.",
    "The water cycle keeps fresh water available for all living things, but people can disturb it. Cutting "
    "forests, wasting water and polluting rivers all harm the cycle. Collecting rainwater, planting trees and "
    "using water carefully help protect it for the future.",
]

CLIPS = [  # (file, language, voice, English source)
    ("clip1.wav", "hi", "Amit", "The bus to the city leaves at seven every morning."),
    ("clip2.wav", "te", "Sravani", "Please bring your notebook and a pencil to the science class tomorrow."),
    ("clip3.wav", "kn", "Deepika", "The market will stay closed on Sunday because of the festival."),
    ("clip4.wav", "te", "Vamsi", "Drink plenty of water and rest well during the hot afternoon."),
    ("clip5.wav", "ta", "Anitha", "The library has new storybooks for children this week."),
]


async def translate(text: str, lang: str) -> str:
    from bodhan_mcp import server

    r = await server.translate_text(lang, text=text, out_dir=str(OUT / ".work"))
    if not r.get("ok"):
        raise SystemExit(f"translation failed: {r}")
    return Path(r["output_path"]).read_text(encoding="utf-8").strip()


def _font(size: int) -> ImageFont.FreeTypeFont:
    for f in [FONT, *FONT_FALLBACKS]:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    raise SystemExit("No Devanagari font found; install Noto Sans Devanagari.")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def scan_page(title: str | None, body: str, page_no: int, rng: random.Random) -> Image.Image:
    w, h, margin = 1240, 1754, 120  # A4 at 150 dpi
    img = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(img)
    y = margin
    if title:
        d.text((margin, y), title, font=_font(52), fill=20)
        y += 120
    body_font = _font(36)
    for line in _wrap(d, body, body_font, w - 2 * margin):
        d.text((margin, y), line, font=body_font, fill=25)
        y += 66
    d.text((w // 2 - 10, h - 90), str(page_no), font=_font(30), fill=60)
    # Scanner look: slight tilt, paper noise, soft focus.
    img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=245)
    noise = Image.effect_noise((w, h), 18).point(lambda v: 255 - v // 6)
    img = Image.blend(img, noise, 0.12).filter(ImageFilter.GaussianBlur(0.6))
    return img.convert("RGB")


async def chapter(path: Path) -> None:
    rng = random.Random(4)
    title = await translate(TITLE, "hi")
    pages = []
    for i, para in enumerate(CHAPTER, 1):
        body = await translate(para, "hi")
        pages.append(scan_page(title if i == 1 else None, body, i, rng))
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=150)


async def clips(folder: Path) -> None:
    from bodhan_mcp import server

    folder.mkdir(parents=True, exist_ok=True)
    for name, lang, voice, english in CLIPS:
        text = await translate(english, lang)
        r = await server.synthesize_speech(lang, text=text, voice=voice, verify=False, out_dir=str(OUT / ".work"))
        if not r.get("ok"):
            raise SystemExit(f"speech failed for {name}: {r}")
        wav = sorted((OUT / ".work" / ".work").glob("*.wav"), key=lambda p: p.stat().st_mtime)[-1]
        (folder / name).write_bytes(wav.read_bytes())


def main() -> None:
    import shutil

    OUT.mkdir(parents=True, exist_ok=True)
    async def both() -> None:  # one event loop: the Bodhan client is shared between the two steps
        await chapter(OUT / "chapter_scan.pdf")
        await clips(OUT / "clips")

    asyncio.run(both())
    shutil.rmtree(OUT / ".work", ignore_errors=True)
    from bodhan_mcp import runtime

    print(f"Bodhan spend: ₹{runtime.get_client().ledger.spent_inr:.3f}")
    for p in [OUT / "chapter_scan.pdf", *sorted((OUT / "clips").iterdir())]:
        print(f"{p.relative_to(OUT)!s:24} {p.stat().st_size:>9} bytes")


if __name__ == "__main__":
    main()
