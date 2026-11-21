"""Optional Live Integration Test Suite for Bodhan AI Services.

These tests communicate with the live Bodhan AI production endpoints using real credentials.
By default, these tests are skipped to avoid consuming paid API credits or making
unauthorized network requests.

To run these tests (only with explicit authorization):
    export BODHAN_API_KEY="your-api-key"
    export RUN_BODHAN_LIVE_TESTS="1"
    pytest tests/test_live_api_plan.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanClient
from bodhan_langchain.exceptions import BodhanAPIError, BodhanAuthenticationError

LIVE_FLAG = os.environ.get("RUN_BODHAN_LIVE_TESTS", "").strip() == "1"
LIVE_REASON = "Live tests are opt-in to avoid paid API credit consumption. Enable with RUN_BODHAN_LIVE_TESTS=1."


@pytest.fixture
def live_client() -> BodhanClient:
    api_key = os.environ.get("BODHAN_API_KEY")
    if not api_key:
        pytest.skip("BODHAN_API_KEY environment variable is not set.")
    return BodhanClient(api_key=api_key)


@pytest.mark.skipif(not LIVE_FLAG, reason=LIVE_REASON)
def test_live_hindi_ocr_request(live_client: BodhanClient) -> None:
    """Live Case 1: Execute a live OCR request on synthetic Hindi image."""
    sample_dir = Path(__file__).resolve().parent.parent / "integrations" / "langchain" / "sample_data"
    pdf_path = sample_dir / "sample_hindi_scholarship.pdf"
    if not pdf_path.exists():
        pytest.skip("Sample PDF not found.")

    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    png_bytes = doc[0].get_pixmap(dpi=150).tobytes("png")
    doc.close()

    resp = live_client.ocr(png_bytes)
    assert resp.text
    assert resp.model == "indic-ocr"


@pytest.mark.skipif(not LIVE_FLAG, reason=LIVE_REASON)
def test_live_tamil_ocr_request(live_client: BodhanClient) -> None:
    """Live Case 2: Execute a live OCR request on synthetic Tamil image."""
    sample_dir = Path(__file__).resolve().parent.parent / "integrations" / "langchain" / "sample_data"
    pdf_path = sample_dir / "sample_tamil_agriculture.pdf"
    if not pdf_path.exists():
        pytest.skip("Sample PDF not found.")

    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    png_bytes = doc[0].get_pixmap(dpi=150).tobytes("png")
    doc.close()

    resp = live_client.ocr(png_bytes)
    assert resp.text
    assert resp.model == "indic-ocr"


@pytest.mark.skipif(not LIVE_FLAG, reason=LIVE_REASON)
def test_live_hindi_to_english_translation(live_client: BodhanClient) -> None:
    """Live Case 3: Execute a live Hindi to English translation."""
    hindi_text = "छात्रवृत्ति योजना के अंतर्गत पात्र छात्रों को प्रति वर्ष ₹१२,००० की सहायता मिलेगी।"
    resp = live_client.translate(hindi_text, target_language="en")
    assert resp.text
    assert resp.target_language == "en"


@pytest.mark.skipif(not LIVE_FLAG, reason=LIVE_REASON)
def test_live_english_to_hindi_translation(live_client: BodhanClient) -> None:
    """Live Case 4: Execute a live English to Hindi translation."""
    english_text = "The application deadline for the merit scholarship is December 31, 2026."
    resp = live_client.translate(english_text, target_language="hi")
    assert resp.text
    assert resp.target_language == "hi"


@pytest.mark.skipif(not LIVE_FLAG, reason=LIVE_REASON)
def test_live_error_handling_invalid_auth() -> None:
    """Live Case 5: Verify live API response when invalid credentials are provided."""
    invalid_client = BodhanClient(api_key="sk-" + "invalidlivekeyforerrortesting12345")
    with pytest.raises(BodhanAuthenticationError) as exc_info:
        invalid_client.translate("Test", target_language="hi")
    assert exc_info.value.status_code == 401
