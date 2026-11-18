
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
output = root / "data" / "sample_ocr.png"

image = Image.new("RGB", (1000, 250), "white")
draw = ImageDraw.Draw(image)

text = "नमस्ते भारत"

font_candidates = [
    "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/mangal.ttf",
]

font = None

for candidate in font_candidates:
    if Path(candidate).exists():
        font = ImageFont.truetype(candidate, 72)
        break

if font is None:
    raise FileNotFoundError(
        "No Devanagari font found. Please provide a Hindi text image."
    )

draw.text(
    (70, 70),
    text,
    font=font,
    fill="black"
)

output.parent.mkdir(parents=True, exist_ok=True)
image.save(output)

print(f"Created: {output}")
print(f"Expected OCR text: {text}")
