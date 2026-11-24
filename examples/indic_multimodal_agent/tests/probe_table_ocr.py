"""Test Indic-OCR with a real scanned-table image (1 page) to verify table recognition
and markdown table formatting.
"""

import os
import json
import base64
import io
import requests
from PIL import Image, ImageDraw, ImageFont

def main():
    ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_vars = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    OCR_KEY = env_vars["BODHAN_OCR_KEY"]

    # Generate a high-resolution table image (simulate 300 DPI scanned document page)
    W, H = 800, 450
    img = Image.new("RGB", (W, H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default(size=20)
        header_font = ImageFont.load_default(size=22)
    except Exception:
        font = None
        header_font = None

    # Title
    draw.text((250, 30), "Student Scorecard / विद्यार्थी गुणपत्रिका", fill=(0, 0, 0), font=header_font)

    # Draw Table Grid
    x_start = 50
    y_start = 80
    col_widths = [120, 200, 180, 180]
    row_height = 50
    rows = [
        ["Roll No", "Student Name", "Subject", "Marks"],
        ["101", "Aarav Sharma", "Mathematics", "95"],
        ["102", "Priya Patel", "Science", "88"],
        ["103", "Rohan Verma", "English", "92"],
        ["104", "Ananya Rao", "Social Studies", "85"],
    ]

    for r_idx, row in enumerate(rows):
        y = y_start + r_idx * row_height
        # Row background for header
        if r_idx == 0:
            draw.rectangle([x_start, y, x_start + sum(col_widths), y + row_height], fill=(240, 240, 240))
        # Horizontal line
        draw.line([(x_start, y), (x_start + sum(col_widths), y)], fill=(0, 0, 0), width=2)
        # Cell contents
        x = x_start
        for c_idx, cell in enumerate(row):
            draw.text((x + 15, y + 15), cell, fill=(0, 0, 0), font=font)
            # Vertical line
            draw.line([(x, y_start), (x, y_start + len(rows) * row_height)], fill=(0, 0, 0), width=2)
            x += col_widths[c_idx]

    # Bottom and right border
    draw.line([(x_start, y_start + len(rows) * row_height), (x_start + sum(col_widths), y_start + len(rows) * row_height)], fill=(0, 0, 0), width=2)
    draw.line([(x_start + sum(col_widths), y_start), (x_start + sum(col_widths), y_start + len(rows) * row_height)], fill=(0, 0, 0), width=2)

    table_img_path = os.path.join(os.path.dirname(__file__), "scanned_table_sample.png")
    img.save(table_img_path)
    print(f"Generated sample table image: {table_img_path}")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    resp = requests.post(
        "https://api.bodhan.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {OCR_KEY}"},
        json={
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                    ]
                }
            ]
        },
        timeout=25,
    )

    print(f"OCR Status: {resp.status_code}")
    res_json = resp.json()
    ocr_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    print("\n--- OCR Extracted Content ---")
    print(ocr_content)

    blocks = res_json.get("blocks", [])
    print(f"\n--- Detected Blocks Count: {len(blocks)} ---")
    for b in blocks:
        if b.get("label") == "Table" or b.get("type") == "Table":
            print(f"Table Block detected! Confidence: {b.get('conf')}")

    with open(os.path.join(os.path.dirname(__file__), "table_ocr_result.json"), "w", encoding="utf-8") as f:
        json.dump(res_json, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()

