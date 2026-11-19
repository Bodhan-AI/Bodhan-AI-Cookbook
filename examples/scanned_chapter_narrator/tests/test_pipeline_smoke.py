"""End-to-end smoke test for Bodhan Lesson pipeline."""

import json
from pathlib import Path

from bodhan_lesson.pipeline import pipeline
from PIL import Image


def test_pipeline_end_to_end_mock(tmp_path: Path):
    # 1. Create 2 dummy scan page images
    img_dir = tmp_path / "scans"
    img_dir.mkdir()

    img1 = Image.new("RGB", (800, 1100), color=(255, 255, 255))
    img1.save(img_dir / "page_1.png")

    img2 = Image.new("RGB", (800, 1100), color=(255, 255, 255))
    img2.save(img_dir / "page_2.png")

    out_dir = tmp_path / "out"

    # 2. Run pipeline
    report = pipeline.run(
        input_path=img_dir,
        target_languages=["hi", "ta"],
        output_dir=out_dir,
        source_language="en",
        skip_qa=False,
    )

    # 3. Assert outputs
    assert report["status"] == "completed"
    assert report["source_document"]["total_pages"] == 2
    assert "hi" in report["languages"]
    assert "ta" in report["languages"]
    assert report["financial_audit"]["total_cost_inr"] > 0

    # Verify physical file existence
    print("DEBUG HI FILES:", [f.name for f in (out_dir / "hi").iterdir()])
    assert (out_dir / "source" / "lesson.src.md").is_file()
    assert (out_dir / "source" / "document_ir.json").is_file()
    assert (out_dir / "hi" / "lesson.hi.md").is_file()
    assert (out_dir / "hi" / "lesson.hi.mp3").is_file()
    assert (out_dir / "ta" / "lesson.ta.md").is_file()
    assert (out_dir / "ta" / "lesson.ta.mp3").is_file()
    assert (out_dir / "report.json").is_file()

    # Verify JSON content
    with open(out_dir / "report.json", encoding="utf-8") as f:
        loaded = json.load(f)
        assert loaded["status"] == "completed"
        assert loaded["source_document"]["total_pages"] == 2
