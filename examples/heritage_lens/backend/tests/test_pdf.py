import pytest

mupdf = pytest.importorskip("pymupdf")

from app.services.bodhan import ocr as ocr_mod
from app.services.documents.pdf import render_pdf_pages
from tests.fakes import FakeClient, json_response


def make_pdf(pages: int) -> bytes:
    doc = mupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def test_pages_rendered_in_order_as_png():
    pages = render_pdf_pages(make_pdf(3), dpi=100)
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert all(p.png_bytes[:8] == b"\x89PNG\r\n\x1a\n" and p.width > 0 for p in pages)


def test_too_many_pages_refused_not_truncated():
    from app.core.errors import ValidationFailed
    with pytest.raises(ValidationFailed, match="limit"):
        render_pdf_pages(make_pdf(4), max_pages=3)


async def test_pdf_ocr_one_request_per_page_in_order():
    def responder(call):
        return json_response({"choices": [{"message": {"content": "x"}}], "blocks": []})

    client = FakeClient(responder)
    res = await ocr_mod.ocr_document(client, make_pdf(3), pdf_dpi=72)
    assert len(client.calls) == 3 and [p.page_number for p in res.pages] == [1, 2, 3] and res.complete
    for call in client.calls:  # Bodhan must only ever see images, never the PDF
        assert call["json_body"]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
