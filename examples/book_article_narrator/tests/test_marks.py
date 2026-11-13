import pytest
from narrator.text_utils import split_sentences


@pytest.mark.parametrize(
    "mark", ["\u0964", "\u0965", "\u06d4", "\u061f", "\uabeb", "\u1c7e", "\u1c7f"]
)
def test_split_sentences_knows_every_script_end_mark(mark):
    text = f"First{mark} Second{mark}"
    assert split_sentences(text) == [f"First{mark}", f"Second{mark}"]