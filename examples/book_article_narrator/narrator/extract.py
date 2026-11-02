"""Turn an uploaded PDF into plain text. No Bodhan calls, no cost."""

import re
import unicodedata
from io import BytesIO

from pypdf import PdfReader

from narrator.errors import InputError

MAX_PAGES = 10


def _tidy(text):
    text = unicodedata.normalize("NFKC", text)
    # PDFs break lines inside a paragraph; keep only blank-line paragraph breaks.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pdf_text(data, max_pages=MAX_PAGES):
    try:
        reader = PdfReader(BytesIO(data))
        pages = reader.pages[:max_pages]
        raw = "\n\n".join((page.extract_text() or "") for page in pages)
    except Exception as error:
        raise InputError(
            "Could not read this PDF. It may be damaged or password-protected."
        ) from error
    text = _tidy(raw)
    if not text:
        raise InputError(
            "This PDF has no selectable text, so it looks scanned. "
            "Scanned files need OCR, which is not supported yet. "
            "Please paste the text instead."
        )
    return text