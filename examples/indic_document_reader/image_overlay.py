"""
Image Overlay Helper — Overlays translated text directly onto document images.
Uses responsive HTML/CSS positioning based on OCR bounding boxes (bbox_xyxy).
"""

import base64
import io
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any


def create_html_overlay(
    image_bytes: bytes,
    image_format: str,
    translated_blocks: List[Dict[str, Any]],
    container_width: int = 800,
) -> str:
    """
    Generate an HTML/CSS overlay where translated text boxes are rendered
    directly over their corresponding bounding boxes on the original image.
    """
    # Open image to get natural dimensions
    img = Image.open(io.BytesIO(image_bytes))
    orig_w, orig_h = img.size

    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    mime = "image/jpeg" if image_format.lower() in ("jpg", "jpeg") else "image/png"

    overlay_elements = []

    for item in translated_blocks:
        block = item.get("block")
        translation = item.get("translation", "")
        if not translation or not block:
            continue

        bbox = getattr(block, "bbox_xyxy", None)
        if not bbox or len(bbox) != 4:
            continue

        x0, y0, x1, y1 = bbox
        left_pct = max(0.0, min(100.0, (x0 / orig_w) * 100))
        top_pct = max(0.0, min(100.0, (y0 / orig_h) * 100))
        width_pct = max(2.0, min(100.0 - left_pct, ((x1 - x0) / orig_w) * 100))
        height_pct = max(2.0, min(100.0 - top_pct, ((y1 - y0) / orig_h) * 100))

        # Determine font size estimation based on box height percentage
        font_size = "clamp(11px, 1.4vw, 16px)"
        if getattr(block, "label", "") in ("Chapter-title", "Title"):
            font_size = "clamp(14px, 2.0vw, 22px)"
            font_weight = "bold"
        else:
            font_weight = "500"

        orig_text_escaped = getattr(block, "text", "").replace('"', '&quot;').replace('<', '&lt;')
        trans_escaped = translation.replace('<', '&lt;').replace('>', '&gt;')

        elem_html = f"""
        <div class="ocr-overlay-box" style="
            position: absolute;
            left: {left_pct:.2f}%;
            top: {top_pct:.2f}%;
            width: {width_pct:.2f}%;
            min-height: {height_pct:.2f}%;
            background: rgba(17, 17, 19, 0.94);
            border: 1px solid #B38B6D;
            border-radius: 0px;
            padding: 4px 6px;
            color: #FFFFFF;
            font-size: {font_size};
            font-weight: {font_weight};
            line-height: 1.35;
            box-sizing: border-box;
            overflow: auto;
            word-break: break-word;
            z-index: 10;
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
            transition: border-color 0.15s ease;
        " title="Original: {orig_text_escaped}">
            <div style="font-size: 8px; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase; font-weight: 700; margin-bottom: 2px; letter-spacing: 0.05em;">
                {getattr(block, 'label', '')}
            </div>
            {trans_escaped}
        </div>
        """
        overlay_elements.append(elem_html)

    all_boxes = "\n".join(overlay_elements)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body {{ margin: 0; padding: 0; background: transparent; font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; }}
        .overlay-container {{
            position: relative;
            display: inline-block;
            width: 100%;
            max-width: 100%;
            border-radius: 0px;
            border: 1px solid #27272A;
            overflow: hidden;
            box-shadow: 0 2px 12px rgba(0,0,0,0.3);
            background: #111113;
        }}
        .base-image {{
            width: 100%;
            height: auto;
            display: block;
        }}
        .ocr-overlay-box:hover {{
            z-index: 50;
            background: rgba(24, 24, 27, 0.98) !important;
            border-color: #F5F1E8 !important;
        }}
    </style>
    </head>
    <body>
        <div class="overlay-container">
            <img class="base-image" src="data:{mime};base64,{b64_img}" alt="Document Page" />
            {all_boxes}
        </div>
    </body>
    </html>
    """
    return html


def create_annotated_pil_image(
    image_bytes: bytes,
    translated_blocks: List[Dict[str, Any]],
) -> bytes:
    """
    Render translated bounding boxes and text directly onto a PIL image
    for downloading or static viewing.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    for item in translated_blocks:
        block = item.get("block")
        translation = item.get("translation", "")
        if not translation or not block:
            continue

        bbox = getattr(block, "bbox_xyxy", None)
        if not bbox or len(bbox) != 4:
            continue

        x0, y0, x1, y1 = bbox
        # Draw translucent pill over bounding box
        draw.rectangle([x0, y0, x1, y1], fill=(15, 15, 26, 220), outline=(108, 99, 255, 255), width=2)
        # Draw text at top-left
        draw.text((x0 + 4, y0 + 4), translation[:40], fill=(255, 255, 255, 255))

    combined = Image.alpha_composite(img, overlay).convert("RGB")
    out = io.BytesIO()
    combined.save(out, format="JPEG", quality=90)
    return out.getvalue()

