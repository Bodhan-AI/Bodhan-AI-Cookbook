import os, json, base64, io, requests
from PIL import Image, ImageDraw

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
env_vars = {}
with open(ENV_PATH, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

OCR_KEY = env_vars["BODHAN_OCR_KEY"]

# Create an image with large bold text (simulating a document / worksheet)
img = Image.new("RGB", (600, 200), color=(255, 255, 255))
draw = ImageDraw.Draw(img)
# Draw larger simulated text strokes
draw.rectangle([20, 20, 580, 180], outline=(0, 0, 0), width=2)
# Draw lines of text
for i in range(1, 4):
    y = 30 + i * 35
    draw.line([(40, y), (500, y)], fill=(0, 0, 0), width=4)

# Also let's test a simple high-contrast text rendering if possible or a clean font
try:
    from PIL import ImageFont
    font = ImageFont.load_default(size=24)
except Exception:
    font = None

img2 = Image.new("RGB", (500, 150), color=(255, 255, 255))
d2 = ImageDraw.Draw(img2)
if font:
    d2.text((30, 50), "CHAPTER 1: INTRODUCTION", fill=(0, 0, 0), font=font)
else:
    d2.text((30, 50), "CHAPTER 1: INTRODUCTION", fill=(0, 0, 0))

buf = io.BytesIO()
img2.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

resp = requests.post(
    "https://api.bodhan.ai/v1/chat/completions",
    headers={"Authorization": f"Bearer {OCR_KEY}"},
    json={
        "model": "indic-ocr",
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            ]}
        ]
    },
    timeout=20,
)

print(f"Status: {resp.status_code}")
res_json = resp.json()
print("OCR choices content:", res_json.get("choices", [{}])[0].get("message", {}).get("content"))
print("Blocks:", json.dumps(res_json.get("blocks", []), indent=2))

