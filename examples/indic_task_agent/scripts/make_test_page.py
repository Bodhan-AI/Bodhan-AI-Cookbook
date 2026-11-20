"""Create sample_data/page.png (and page.pdf): a worksheet with a paragraph and a table.

Run from examples/indic_task_agent:  python scripts/make_test_page.py
"""

from pathlib import Path

import pymupdf

out = Path("sample_data")
out.mkdir(exist_ok=True)

doc = pymupdf.open()
page = doc.new_page()  # A4
page.insert_text((60, 80), "Weekly Class Schedule", fontsize=22)
page.insert_text((60, 120), "The meeting has been postponed to tomorrow.", fontsize=13)
page.insert_text((60, 142), "Please check the table below for the new timings.", fontsize=13)

rows = [("Day", "Subject", "Room"),
        ("Monday", "Mathematics", "A-101"),
        ("Tuesday", "Science", "B-204"),
        ("Wednesday", "English", "C-110")]
x0, y0, widths, height = 60, 180, (130, 170, 100), 32
for r, row in enumerate(rows):
    x = x0
    for c, cell in enumerate(row):
        page.draw_rect(pymupdf.Rect(x, y0 + r * height, x + widths[c], y0 + (r + 1) * height), width=0.8)
        page.insert_text((x + 8, y0 + r * height + 21), cell, fontsize=13)
        x += widths[c]

page.get_pixmap(dpi=150).save(out / "page.png")
doc.save(out / "page.pdf")
print("created:", out / "page.png", (out / "page.png").stat().st_size, "bytes |", out / "page.pdf")