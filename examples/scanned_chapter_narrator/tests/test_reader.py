"""Unit tests for HTMLReaderGenerator interactive synchronized reader."""

from pathlib import Path

from bodhan_lesson.reader import html_reader_generator
from bodhan_lesson.schemas import NarrationItem


def test_html_reader_generation(tmp_path: Path):
    narration_items = [
        NarrationItem(
            section_id="01",
            section_slug="intro",
            section_title="Introduction",
            english_script="Welcome to the lesson on magnetism. Let us explore magnetic fields.",
            target_script="காந்தவியல் பாடத்திற்கு வரவேற்கிறோம். காந்தப்புலங்களை ஆராய்வோம்.",
            target_language="ta",
            duration_seconds=12.5,
        )
    ]

    glossary = {"காந்தவியல்": "Study of magnetic fields and phenomena"}
    annotations = [{"block_id": "b1", "page_number": 1, "text": "Important formula on page 1"}]

    out_file = tmp_path / "reader.html"
    content = html_reader_generator.generate_reader_html(
        lesson_title="Magnetism and Matter",
        language="ta",
        lesson_markdown="# காந்தவியல்\n\nகாந்தவியல் பாடத்திற்கு வரவேற்கிறோம். காந்தப்புலங்களை ஆராய்வோம்.",
        narration_items=narration_items,
        audio_filename="lesson.ta.mp3",
        glossary=glossary,
        annotations=annotations,
        output_html_path=out_file,
    )

    assert out_file.is_file()
    assert "Bodhan Interactive Lesson Reader" in content
    assert 'class="lesson-section"' in content
    assert 'id="section-01"' in content
    assert 'class="sentence"' in content
    assert 'data-start="' in content
    assert 'class="section-play-btn"' in content
    assert "lesson.ta.mp3" in content
    assert "Study of magnetic fields" in content
    assert "Important formula on page 1" in content
    assert "katex.min.js" in content
    assert "panel-spoken" in content
    assert "panel-textbook" in content
    assert "speed-btn" in content


def test_html_reader_renders_tables_and_math(tmp_path: Path):
    narration_items = [
        NarrationItem(
            section_id="01",
            section_slug="properties",
            section_title="Magnetic Properties",
            english_script="Here are the properties.",
            target_script="இவை பண்புகள் ஆகும்.",
            target_language="ta",
            duration_seconds=15.0,
            section_markdown=(
                "## பண்புகள்\n\n"
                "| Property | Value |\n"
                "| --- | --- |\n"
                "| Susceptibility | Negative |\n\n"
                "$$B = \\mu_0 (H + M)$$"
            ),
        )
    ]

    out_file = tmp_path / "reader_rich.html"
    content = html_reader_generator.generate_reader_html(
        lesson_title="Physics Magnetism",
        language="ta",
        lesson_markdown="",
        narration_items=narration_items,
        audio_filename="lesson.ta.mp3",
        output_html_path=out_file,
    )

    assert out_file.is_file()
    assert 'class="lesson-table"' in content
    assert "<th>Property</th>" in content
    assert "<td>Susceptibility</td>" in content
    assert 'class="math-block"' in content
    assert "$$B = \\mu_0 (H + M)$$" in content
    assert 'class="section-tab-btn"' in content
