"""Generate a realistic 3-page scanned-style sample PDF document for Stage A tests.
Uses PyMuPDF (fitz) and Pillow.
"""

import os
from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF


def generate_scanned_3page_pdf(output_pdf_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)
    doc = fitz.open()

    W, H = 800, 1100

    # --- Page 1: Public Health Overview ---
    img1 = Image.new("RGB", (W, H), color=(250, 250, 250))
    d1 = ImageDraw.Draw(img1)
    try:
        f_title = ImageFont.load_default(size=24)
        f_head = ImageFont.load_default(size=18)
        f_body = ImageFont.load_default(size=14)
    except Exception:
        f_title = f_head = f_body = None

    d1.rectangle([(40, 40), (760, 1060)], outline=(180, 180, 180), width=1)
    d1.text((70, 70), "NATIONAL RURAL HEALTH INITIATIVE", fill=(0, 51, 102), font=f_title)
    d1.text((70, 105), "Department of Public Health & Family Welfare - Annual Report 2026", fill=(100, 100, 100), font=f_head)
    d1.line([(70, 135), (730, 135)], fill=(0, 51, 102), width=2)

    p1_text = (
        "1. Executive Overview:\n"
        "The National Rural Health Initiative launched a comprehensive child immunization drive\n"
        "across rural districts during the first quarter of 2026. The primary focus was delivering\n"
        "essential vaccines to children aged 0 to 5 years, with special attention to remote villages\n"
        "and tribal settlements.\n\n"
        "2. Key Objectives:\n"
        "- Ensure 95% immunization coverage for measles and rubella in high-risk zones.\n"
        "- Deploy mobile vaccination vans to improve access in underserved hilly regions.\n"
        "- Strengthen solar-powered cold-chain infrastructure across all primary health centers.\n"
        "- Train local community healthcare workers (ASHA) for digital tracking and record-keeping."
    )
    d1.multiline_text((70, 170), p1_text, fill=(20, 20, 20), font=f_body, spacing=8)
    d1.text((370, 1030), "- Page 1 -", fill=(120, 120, 120), font=f_body)

    # --- Page 2: Statistical Table of Coverage ---
    img2 = Image.new("RGB", (W, H), color=(250, 250, 250))
    d2 = ImageDraw.Draw(img2)
    d2.rectangle([(40, 40), (760, 1060)], outline=(180, 180, 180), width=1)
    d2.text((70, 70), "DISTRICT IMMUNIZATION PROGRESS", fill=(0, 51, 102), font=f_title)
    d2.text((70, 105), "Quarter 1 Progress Review & Coverage Statistics", fill=(100, 100, 100), font=f_head)
    d2.line([(70, 135), (730, 135)], fill=(0, 51, 102), width=2)

    # Table Grid
    tx, ty = 70, 170
    col_w = [140, 160, 180, 180]
    row_h = 45
    t_rows = [
        ["District", "Target Children", "Doses Delivered", "Coverage %"],
        ["Vellore", "12500", "11875", "95.0%"],
        ["Salem", "18200", "17290", "95.0%"],
        ["Madurai", "21000", "19530", "93.0%"],
        ["Thanjavur", "14300", "13585", "95.0%"],
        ["Total", "66000", "62280", "94.4%"],
    ]

    for r_i, row in enumerate(t_rows):
        cy = ty + r_i * row_h
        if r_i == 0:
            d2.rectangle([(tx, cy), (tx + sum(col_w), cy + row_h)], fill=(230, 240, 250))
        d2.line([(tx, cy), (tx + sum(col_w), cy)], fill=(0, 0, 0), width=1)
        cx = tx
        for c_i, cell in enumerate(row):
            d2.text((cx + 12, cy + 14), cell, fill=(0, 0, 0), font=f_body)
            d2.line([(cx, ty), (cx, ty + len(t_rows) * row_h)], fill=(0, 0, 0), width=1)
            cx += col_w[c_i]
    d2.line([(tx, ty + len(t_rows) * row_h), (tx + sum(col_w), ty + len(t_rows) * row_h)], fill=(0, 0, 0), width=1)
    d2.line([(tx + sum(col_w), ty), (tx + sum(col_w), ty + len(t_rows) * row_h)], fill=(0, 0, 0), width=1)

    p2_notes = (
        "\nObservations:\n"
        "- Vellore and Salem recorded the highest coverage rate at 95%.\n"
        "- A total of 62,280 children were successfully vaccinated out of 66,000 target population.\n"
        "- Follow-up camps are scheduled for remaining unreached areas next month."
    )
    d2.multiline_text((70, 480), p2_notes, fill=(20, 20, 20), font=f_body, spacing=8)
    d2.text((370, 1030), "- Page 2 -", fill=(120, 120, 120), font=f_body)

    # --- Page 3: Strategic Recommendations ---
    img3 = Image.new("RGB", (W, H), color=(250, 250, 250))
    d3 = ImageDraw.Draw(img3)
    d3.rectangle([(40, 40), (760, 1060)], outline=(180, 180, 180), width=1)
    d3.text((70, 70), "STRATEGIC RECOMMENDATIONS & ROADMAP", fill=(0, 51, 102), font=f_title)
    d3.text((70, 105), "Actionable Steps for Quarter 2 Implementation", fill=(100, 100, 100), font=f_head)
    d3.line([(70, 135), (730, 135)], fill=(0, 51, 102), width=2)

    p3_text = (
        "1. Immediate District Actions:\n"
        "- All district medical officers must verify cold-chain logger logs twice weekly.\n"
        "- Special weekend camps should be conducted in municipal corporation wards.\n"
        "- Ensure uninterrupted supply of ORS packets alongside vaccination.\n\n"
        "2. Long-term Priorities:\n"
        "- Expand digital health IDs (ABHA) for automated vaccination certificate generation.\n"
        "- Partner with local panchayats to promote community awareness and overcome hesitancy.\n\n"
        "Approved by:\n"
        "Dr. K. Narayanan, Director of Public Health\n"
        "Contact: health.initiative@state.gov.in"
    )
    d3.multiline_text((70, 170), p3_text, fill=(20, 20, 20), font=f_body, spacing=8)
    d3.text((370, 1030), "- Page 3 -", fill=(120, 120, 120), font=f_body)

    # Add pages to PDF
    for img in [img1, img2, img3]:
        page = doc.new_page(width=W, height=H)
        img_bytes = fitz.io.BytesIO()
        img.save(img_bytes, format="PNG")
        page.insert_image(page.rect, stream=img_bytes.getvalue())

    doc.save(output_pdf_path)
    doc.close()
    print(f"Generated 3-page scanned sample PDF: {output_pdf_path}")


if __name__ == "__main__":
    generate_scanned_3page_pdf("samples/health_report_3page.pdf")

