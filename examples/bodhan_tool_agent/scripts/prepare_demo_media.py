"""Generate synthetic input media files for the 6 demo tasks (SPEC §20)."""

import wave
from pathlib import Path

import pymupdf


def generate_wav(path: Path, duration_s: float = 3.0, sample_rate: int = 16000) -> None:
    """Generate a valid PCM 16-bit mono WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Low amplitude audio signal
        raw_data = bytearray(n_frames * 2)
        wf.writeframes(raw_data)


def generate_pdf(path: Path, num_pages: int = 8) -> None:
    """Generate a multi-page PDF with realistic text and tables."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        rect = pymupdf.Rect(50, 50, 545, 800)
        content = (
            f"Annual Agricultural and Water Resources Report - Page {i + 1}\n\n"
            "Executive Summary:\n"
            "This report documents irrigation efficiency, seasonal rainfall patterns, and agricultural yield.\n\n"
            "| Region | Rainfall (mm) | Yield (tons/ha) |\n"
            "| --- | --- | --- |\n"
            f"| North Zone | {600 + i * 20} | {3.2 + i * 0.1:.1f} |\n"
            f"| South Zone | {850 - i * 15} | {4.1 + i * 0.05:.1f} |\n\n"
            "Conclusions and Recommendations:\n"
            "Crop diversification and localized drip irrigation should be prioritized.\n"
        )
        page.insert_textbox(rect, content, fontsize=12)
    doc.save(str(path))
    doc.close()


def generate_image_with_table(path: Path) -> None:
    """Generate a PNG image containing a table representing a worksheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    rect = pymupdf.Rect(40, 40, 560, 360)
    content = (
        "Science Worksheet: Planetary Characteristics\n\n"
        "| Planet | Diameter (km) | Moons |\n"
        "| --- | --- | --- |\n"
        "| Mercury | 4,879 | 0 |\n"
        "| Venus | 12,104 | 0 |\n"
        "| Earth | 12,742 | 1 |\n"
        "| Mars | 6,779 | 2 |\n"
    )
    page.insert_textbox(rect, content, fontsize=14)
    pix = page.get_pixmap(dpi=150)
    pix.save(str(path))
    doc.close()


def generate_dummy_mp4(path: Path) -> None:
    """Generate a minimal valid MP4 file container."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal ftyp box for mp42
    ftyp = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00isommp42\x00\x00\x00\x08mdat"
    path.write_bytes(ftyp)


def main() -> None:
    base_dir = Path("demo/inputs")

    # Task 1: 8-page PDF
    t1_dir = base_dir / "task_1"
    generate_pdf(t1_dir / "scanned_report_8pages.pdf", num_pages=8)
    print("Generated Task 1 inputs.")

    # Task 2: Hindi WAV
    t2_dir = base_dir / "task_2"
    generate_wav(t2_dir / "hindi_speech.wav", duration_s=4.0)
    print("Generated Task 2 inputs.")

    # Task 3: Worksheet image
    t3_dir = base_dir / "task_3"
    generate_image_with_table(t3_dir / "worksheet_table.png")
    print("Generated Task 3 inputs.")

    # Task 4: 5 audio clips (Telugu, Hindi, Tamil, Telugu, Kannada)
    t4_dir = base_dir / "task_4"
    for i in range(1, 6):
        generate_wav(t4_dir / f"clip{i}.wav", duration_s=2.5)
    print("Generated Task 4 inputs.")

    # Task 5: Bengali text
    t5_dir = base_dir / "task_5"
    t5_dir.mkdir(parents=True, exist_ok=True)
    (t5_dir / "bengali_passage.txt").write_text(
        "রবীন্দ্রনাথ ঠাকুর ছিলেন একজন ভারতীয় বাঙালি কবি, সঙ্গীতজ্ঞ, চিত্রশিল্পী, নাট্যকার, ঔপন্যাসিক ও দার্শনিক। "
        "তিনি ১৯১৩ সালে গীতাঞ্জলি কাব্যগ্রন্থের জন্য সাহিত্যে প্রথম অ-ইউরোপীয় হিসেবে নোবেল পুরস্কার লাভ করেন।",
        encoding="utf-8",
    )
    print("Generated Task 5 inputs.")

    # Task 6: MP4 video
    t6_dir = base_dir / "task_6"
    generate_dummy_mp4(t6_dir / "sample_video.mp4")
    print("Generated Task 6 inputs.")


if __name__ == "__main__":
    main()
