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
    assert 'class="sentence"' in content
    assert 'data-start="' in content
    assert "lesson.ta.mp3" in content
    assert "Study of magnetic fields" in content
    assert "Important formula on page 1" in content
