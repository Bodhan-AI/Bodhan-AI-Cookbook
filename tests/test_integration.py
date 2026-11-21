"""End-to-end pipeline test for Bodhan LangChain integration."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import httpx
import pytest

INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanClient
from examples.multilingual_rag import run_multilingual_rag_pipeline

DUMMY_KEY = "dummy-rag-e2e-key"


def test_multilingual_rag_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that the multilingual RAG pipeline executes from end-to-end with mocked responses."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        if url_path == "/v1/chat/completions":
            data = json.loads(request.content)
            model = data.get("model")
            if model == "indic-ocr":
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "Scholarship eligibility is 55% marks with assistance of ₹12,000 per year."}}],
                    "blocks": [{"order": 1, "label": "paragraph", "bbox_xyxy": [50, 50, 200, 100], "conf": 0.98, "text": "Scholarship info"}],
                })
            if model == "indic-translate":
                target = data.get("target_language")
                src = data["messages"][0]["content"]
                if target == "hi":
                    return httpx.Response(200, json={
                        "choices": [{"message": {"content": "बोधन एआई बहुभाषी सहायक का उपयोग करने के लिए धन्यवाद।"}}],
                    })
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": f"English: {src}"}}],
                    "source_language": "hi",
                })
        return httpx.Response(404)

    mock_client = BodhanClient(
        api_key=DUMMY_KEY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"),
    )

    run_multilingual_rag_pipeline(mock_client=mock_client)

    captured = capsys.readouterr().out
    assert "Ingesting" in captured
    assert "Translating documents to English" in captured
    assert "Splitting translated documents" in captured
    assert "Vector indexing complete" in captured
    assert "Executing Multilingual Retrieval & Grounded Q&A" in captured
    assert "MULTILINGUAL RAG PIPELINE DEMONSTRATION COMPLETE" in captured
