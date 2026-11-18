#!/usr/bin/env python3
"""Generate synthetic test files for demo tasks.

Creates demo_inputs/ folder with:
- Task 1: 8-page scanned-style English PDF/PNG images
- Task 2: 3-minute Hindi WAV (silent if ffmpeg unavailable)
- Task 3: 4-column, 5-row worksheet PNG
- Task 4: 5 short audio clips (Telugu, Hindi, Tamil, English)
- Task 5: Bengali UTF-8 text file
- Task 6: Silent MP4 video clip (requires ffmpeg)
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

DEMO_INPUTS_DIR = Path(__file__).resolve().parent.parent / "demo_inputs"


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None


def has_ffmpeg() -> bool:
    return run_cmd(["ffmpeg", "-version"]) is not None


def generate_task1_scanned_pdf(out_path: Path) -> None:
    """Generate 8-page scanned-style English images (simulating PDF pages).

    ponytail: pypdfium2 API varies by version; generate PNG pages instead
    for reliable cross-platform demo. Use sample_pdf_generator.sh if PDF needed.
    """
    from PIL import Image, ImageDraw, ImageFont

    out_path.parent.mkdir(parents=True, exist_ok=True)
    for page_num in range(1, 9):
        img = Image.new("RGB", (2480, 3508), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 24
            )
            bold_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 36
            )
        except Exception:
            font = ImageFont.load_default()
            bold_font = font

        y = 100
        draw.text((100, y), f"Chapter {page_num}: Introduction to Testing", fill="black", font=bold_font)
        y += 80

        para_text = (
            "This is a sample paragraph for testing optical character recognition. "
            "It contains multiple sentences that should be detected and transcribed accurately. "
            "The scanned document simulates a book page with traditional formatting."
        )
        for line in para_text.split(". "):
            if line.strip():
                draw.text((100, y), line.strip() + ".", fill="black", font=font)
                y += 36

        img.save(out_path.parent / f"page_{page_num}.png", "PNG")


def generate_task2_hindi_wav(out_path: Path, duration_sec: float = 180) -> None:
    """Generate ~3-minute Hindi WAV (silent if ffmpeg unavailable)."""
    import wave
    import struct
    import math

    if has_ffmpeg():
        sample_hindi = Path(__file__).resolve().parent.parent / "sample_data" / "sample_hindi.wav"
        if sample_hindi.exists():
            run_cmd([
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(sample_hindi),
                "-t", str(duration_sec), "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(out_path)
            ])
            return

    sr = 16000
    n = int(duration_sec * sr)
    frames = []
    for i in range(n):
        t = i / sr
        sample = 0.0
        frames.append(struct.pack("<h", int(sample * 32767)))
    with wave.open(str(out_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"".join(frames))


def generate_task3_worksheet_png(out_path: Path) -> None:
    """Generate 4-column, 5-row schedule/pricing table PNG using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (800, 500), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18
        )
        bold_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
        )
    except Exception:
        font = ImageFont.load_default()
        bold_font = font

    y = 20
    headers = ["Item", "Price (₹)", "Quantity", "Total (₹)"]
    draw.text((40, y), "Pricing Worksheet", fill="black", font=bold_font)
    y += 60

    col_width = 180
    for i, h in enumerate(headers):
        draw.text((40 + i * col_width, y), h, fill="black", font=bold_font)
    y += 35

    draw.line([(40, y), (40 + 4 * col_width, y)], fill="black", width=2)
    y += 10

    data = [
        ["Apples", "25.00", "5", "125.00"],
        ["Oranges", "30.00", "3", "90.00"],
        ["Mangoes", "45.00", "2", "90.00"],
        ["Bananas", "15.00", "10", "150.00"],
        ["Grapes", "80.00", "1", "80.00"],
    ]

    for row in data:
        for i, cell in enumerate(row):
            draw.text((40 + i * col_width, y), cell, fill="black", font=font)
        y += 30

    draw.line([(40, y), (40 + 4 * col_width, y)], fill="black", width=2)
    y += 20
    draw.text((40, y), "Total: ₹ 535.00", fill="black", font=bold_font)

    img.save(out_path, "PNG")


def generate_task4_audio_clips(out_dir: Path) -> None:
    """Generate 5 short audio clips (2 Telugu, 1 Hindi, 1 Tamil, 1 English)."""
    clips = [
        ("telugu_1.wav", "హలో ప్రపంచం", "te"),
        ("telugu_2.wav", "మంచి రోజు జరిగింది", "te"),
        ("hindi_1.wav", "नमस्ते दुनिया", "hi"),
        ("tamil_1.wav", "வணக்கம் உலகம்", "ta"),
        ("english_1.wav", "Hello World", "en"),
    ]

    import wave
    import struct
    import math

    for filename, text, lang in clips:
        duration = 2.0
        sr = 16000
        n = int(duration * sr)

        frames = []
        for i in range(n):
            t = i / sr
            sample = math.sin(2 * math.pi * 440 * t) * 0.3
            frames.append(struct.pack("<h", int(sample * 32767)))

        out_path = out_dir / filename
        with wave.open(str(out_path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"".join(frames))


def generate_task5_bengali_txt(out_path: Path) -> None:
    """Generate UTF-8 text file with 3-sentence Bengali paragraph."""
    bengali_text = """আমি একটি বাংলা বাক্য লিখছি। এটি একটি সংক্ষিপ্ত বাংলা প্যারাগ্রাফ। আমরা ভাষার সুন্দরতা ও স্তব্ধতা প্রকাশ করতে পারি।"""
    out_path.write_text(bengali_text, encoding="utf-8")


def generate_task6_silent_mp4(out_path: Path, duration_sec: float = 5) -> None:
    """Generate silent MP4 video clip via ffmpeg."""
    if not has_ffmpeg():
        print("  Warning: ffmpeg not found, skipping MP4 generation")
        return
    run_cmd([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=640x480:d={duration_sec}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def main() -> None:
    out_dir = DEMO_INPUTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating demo inputs in {out_dir}")

    generate_task1_scanned_pdf(out_dir / "task1_chapter.pdf")
    print("  Task 1: 8-page scanned PDF/PNG (task1_page_*.png)")

    generate_task2_hindi_wav(out_dir / "task2_note.wav")
    print("  Task 2: 3-minute Hindi WAV")

    generate_task3_worksheet_png(out_dir / "task3_worksheet.png")
    print("  Task 3: Worksheet PNG")

    generate_task4_audio_clips(out_dir)
    print("  Task 4: Audio clips")

    generate_task5_bengali_txt(out_dir / "task5_paragraph.txt")
    print("  Task 5: Bengali text")

    generate_task6_silent_mp4(out_dir / "task6_video.mp4")
    print("  Task 6: Silent MP4")

    print("Done!")


if __name__ == "__main__":
    main()