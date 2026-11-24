"""Generic unsupported-language preflight tests, without Bodhan API calls."""
import pytest

from bodhan_agent.language_preflight import unsupported_target_language


@pytest.mark.parametrize("task, expected", [
    ("Translate this video into French and save a French output video.", "French"),
    ("Translate this paragraph to Spanish.", "Spanish"),
    ("Dub this lesson in Japanese.", "Japanese"),
    ("Read this paragraph aloud in French.", "French"),
    ("Translate the worksheet into Marathi, keeping the table.", None),
    ("Read this paragraph aloud in Bengali with a female voice.", None),
    ("Translate the transcript into English and make subtitles.", None),
    ("Translate the transcript into a Markdown file.", None),
    ("Make a Tamil audio summary of the chapter.", None),
    ("Which clips are in Telugu? Translate those into English.", None),
])
def test_output_target_language(task, expected):
    assert unsupported_target_language(task) == expected
