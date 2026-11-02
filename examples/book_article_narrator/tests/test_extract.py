from io import BytesIO

import pytest
from narrator.errors import InputError
from narrator.extract import _tidy, extract_pdf_text
from pypdf import PdfWriter


def test_tidy_joins_lines_but_keeps_paragraphs():
    assert _tidy("one\ntwo\n\nthree") == "one two\n\nthree"


def test_garbage_bytes_raise_input_error():
    with pytest.raises(InputError):
        extract_pdf_text(b"not a pdf")


def test_blank_pdf_is_reported_as_scanned():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(InputError, match="scanned"):
        extract_pdf_text(buffer.getvalue())

def test_tidy_turns_joined_letter_characters_into_normal_letters():
    assert _tidy("oﬃces") == "offices"