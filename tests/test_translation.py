"""Unit tests for BodhanTranslationTransformer using mocked Bodhan client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.documents import Document

INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanAsyncClient, BodhanClient  # noqa: E402
from bodhan_langchain.exceptions import BodhanAPIError, BodhanValidationError  # noqa: E402
from bodhan_langchain.translation import BodhanTranslationTransformer  # noqa: E402

DUMMY_KEY = "dummy-trans-key"


def test_transformer_translates_documents_and_preserves_metadata() -> None:
    """Verify that document content is translated while preserving all original metadata."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["target_language"] == "en"
        if "messages" in data:
            assert data["model"] == "indic-translate"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Hello world"}}],
                "source_language": "hi",
            })
        return httpx.Response(200, json={
            "translation": "Hello world",
            "source_language": "hi",
        })

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    transformer = BodhanTranslationTransformer(target_language="en", source_language="hi", client=client, preserve_original_content=True)

    original_doc = Document(
        page_content="नमस्ते दुनिया",
        metadata={"source": "notice.pdf", "page": 1, "custom_field": "val123"},
    )
    docs = [original_doc]

    transformed = transformer.transform_documents(docs)

    assert len(transformed) == 1
    t_doc = transformed[0]
    assert t_doc.page_content == "Hello world"
    assert t_doc.metadata["source"] == "notice.pdf"
    assert t_doc.metadata["page"] == 1
    assert t_doc.metadata["custom_field"] == "val123"
    assert t_doc.metadata["target_language"] == "en"
    assert t_doc.metadata["source_language"] == "hi"
    assert t_doc.metadata["source_language_origin"] == "user_declared"
    assert "detected_source_language" not in t_doc.metadata
    assert t_doc.metadata["translation_model"] == "indic-translate"
    assert t_doc.metadata["original_content"] == "नमस्ते दुनिया"

    # Immutability check: original document must not be mutated
    assert original_doc.page_content == "नमस्ते दुनिया"
    assert "target_language" not in original_doc.metadata


def test_transformer_empty_and_whitespace_documents() -> None:
    """Verify that empty or whitespace documents pass through without making API calls."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "Translated"}}]})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    transformer = BodhanTranslationTransformer(target_language="hi", client=client)

    docs = [
        Document(page_content="", metadata={"id": "empty"}),
        Document(page_content="   \n\t  ", metadata={"id": "whitespace"}),
    ]

    transformed = transformer.transform_documents(docs)

    assert len(transformed) == 2
    assert transformed[0].page_content == ""
    assert transformed[1].page_content == "   \n\t  "
    assert calls == 0  # Zero API requests made


def test_transformer_invalid_language_validation() -> None:
    """Verify validation error when constructing with unsupported target language."""
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="klingon", client=client)
    assert "Unsupported target language" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="hi", script="invalid_script", client=client)
    assert "Invalid script" in str(exc.value)


def test_transformer_paragraph_chunking() -> None:
    """Verify paragraph splitting and rejoining on long text documents."""
    received_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        text = data["messages"][0]["content"] if "messages" in data else data["text"]
        received_requests.append(text)
        if "messages" in data:
            return httpx.Response(200, json={"choices": [{"message": {"content": f"Tr: {text}"}}]})
        return httpx.Response(200, json={"translation": f"Tr: {text}"})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    # Set low chunk threshold to trigger splitting
    transformer = BodhanTranslationTransformer(
        target_language="ta",
        client=client,
        chunk_paragraphs=True,
        max_chunk_chars=20,
    )

    long_text = "Paragraph One.\n\nParagraph Two.\n\nParagraph Three."
    doc = Document(page_content=long_text, metadata={"source": "test.txt"})

    transformed = transformer.transform_documents([doc])

    assert len(transformed) == 1
    assert transformed[0].page_content == "Tr: Paragraph One.\n\nTr: Paragraph Two.\n\nTr: Paragraph Three."
    assert len(received_requests) == 3


def test_transformer_error_handling_policies() -> None:
    """Verify error_handling 'raise' vs 'ignore'."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "Translation error"}})

    # 'raise' policy
    client_raise = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    t_raise = BodhanTranslationTransformer(target_language="hi", client=client_raise, error_handling="raise")
    with pytest.raises(BodhanAPIError):
        t_raise.transform_documents([Document(page_content="Text to fail")])

    # 'ignore' policy
    client_ignore = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    t_ignore = BodhanTranslationTransformer(target_language="hi", client=client_ignore, error_handling="ignore")
    res = t_ignore.transform_documents([Document(page_content="Text to fail", metadata={"id": "orig"})])
    assert len(res) == 1
    assert res[0].page_content == "Text to fail"  # Retained original text on failure


@pytest.mark.asyncio
async def test_async_transformer_transform_documents() -> None:
    """Verify async atransform_documents functionality."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        if "messages" in data:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Async Translated Text"}}],
                "source_language": "bn",
            })
        return httpx.Response(200, json={
            "translation": "Async Translated Text",
            "source_language": "bn",
        })

    async_client = BodhanAsyncClient(api_key=DUMMY_KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    transformer = BodhanTranslationTransformer(target_language="en", source_language="bn", async_client=async_client)

    docs = [Document(page_content="বাংলা পাঠ্য", metadata={"source": "bengali.txt"})]
    transformed = await transformer.atransform_documents(docs)

    assert len(transformed) == 1
    assert transformed[0].page_content == "Async Translated Text"
    assert transformed[0].metadata["source_language"] == "bn"
    assert transformed[0].metadata["source_language_origin"] == "user_declared"
    assert "detected_source_language" not in transformed[0].metadata
    assert transformed[0].metadata["target_language"] == "en"


def test_transformer_splits_single_oversized_paragraph() -> None:
    """Verify that a single 10,000-character paragraph without newlines is safely chunked."""
    recorded_chunks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        chunk_text = data.get("text") or data["messages"][0]["content"]
        recorded_chunks.append(chunk_text)
        return httpx.Response(200, json={"translation": f"[Tr: {chunk_text[:10]}...]"})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    # Max chunk chars = 250
    transformer = BodhanTranslationTransformer(target_language="en", client=client, max_chunk_chars=250)

    # 10,000 character single paragraph with Indic sentences (using danda ।)
    sentences = ["भारत एक विशाल और विविध संस्कृति वाला देश है।" for _ in range(250)]
    oversized_paragraph = "".join(sentences)
    assert len(oversized_paragraph) > 9000

    doc = Document(page_content=oversized_paragraph, metadata={"id": "huge"})
    res = transformer.transform_documents([doc])

    assert len(res) == 1
    assert len(recorded_chunks) > 10
    # Strict safety invariant: Every chunk sent to the API must be <= 250 characters!
    assert all(len(c) <= 250 for c in recorded_chunks), [len(c) for c in recorded_chunks if len(c) > 250]


def test_transformer_client_lifecycle_internally_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that an internally instantiated client is closed after transform_documents."""
    closed = False

    class MockInternalClient:
        def __init__(self, *args, **kwargs):
            pass
        def translate(self, *args, **kwargs):
            from bodhan_langchain.models import TranslationResponse
            return TranslationResponse(text="Tr Text", target_language="en")
        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("bodhan_langchain.translation.BodhanClient", MockInternalClient)

    transformer = BodhanTranslationTransformer(target_language="en")
    docs = transformer.transform_documents([Document(page_content="Hello")])

    assert len(docs) == 1
    assert closed is True


def test_transformer_invalid_chunk_and_concurrency_configuration() -> None:
    """Verify validation of max_chunk_chars and max_concurrency, rejecting booleans, 0, and negatives."""
    # Invalid max_chunk_chars
    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_chunk_chars=True)  # type: ignore[arg-type]
    assert "max_chunk_chars must be a positive integer" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_chunk_chars=0)
    assert "max_chunk_chars must be a positive integer" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_chunk_chars=-50)
    assert "max_chunk_chars must be a positive integer" in str(exc.value)

    # Invalid max_concurrency
    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_concurrency=False)  # type: ignore[arg-type]
    assert "max_concurrency must be a positive integer" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_concurrency=0)
    assert "max_concurrency must be a positive integer" in str(exc.value)

    with pytest.raises(BodhanValidationError) as exc:
        BodhanTranslationTransformer(target_language="en", max_concurrency=-2)
    assert "max_concurrency must be a positive integer" in str(exc.value)


def test_transformer_chunking_edge_cases_and_order_preservation() -> None:
    """Verify splitting, character fallback for unbroken tokens, Indic punctuation, and reassembly order."""
    from bodhan_langchain.translation import _split_oversized_text

    # 1. Long unbroken token (no spaces) exceeding max_chunk_chars -> character fallback
    unbroken_token = "A" * 125
    chunks = _split_oversized_text(unbroken_token, max_chunk_chars=50)
    assert len(chunks) == 3
    assert chunks[0] == "A" * 50
    assert chunks[1] == "A" * 50
    assert chunks[2] == "A" * 25
    assert "".join(chunks) == unbroken_token

    # 2. Indic punctuation (danda । and double danda ॥)
    indic_text = "पहला वाक्य है। दूसरा वाक्य भी है॥ तीसरा वाक्य यहाँ है।"
    indic_chunks = _split_oversized_text(indic_text, max_chunk_chars=25)
    assert all(len(c) <= 25 for c in indic_chunks)
    assert any("।" in c for c in indic_chunks)
    assert any("॥" in c for c in indic_chunks)

    # 3. Maximum boundary exact match
    exact_text = "ExactTwentyCharacters!"
    exact_chunks = _split_oversized_text(exact_text, max_chunk_chars=len(exact_text))
    assert len(exact_chunks) == 1
    assert exact_chunks[0] == exact_text

    # 4. End-to-end transformation reassembly order preservation
    received_order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        t = data.get("text") or data["messages"][0]["content"]
        received_order.append(t)
        return httpx.Response(200, json={"translation": f"[{t}]"})

    mock_http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai")
    bodhan_client = BodhanClient(api_key=DUMMY_KEY, http_client=mock_http_client)
    transformer = BodhanTranslationTransformer(
        target_language="en",
        client=bodhan_client,
        chunk_paragraphs=True,
        max_chunk_chars=20,
    )

    doc_text = "PartOne 12345.\n\nPartTwo 67890.\n\nPartThree 99999."
    doc = Document(page_content=doc_text)
    transformed = transformer.transform_documents([doc])

    assert len(transformed) == 1
    assert received_order == ["PartOne 12345.", "PartTwo 67890.", "PartThree 99999."]
    assert transformed[0].page_content == "[PartOne 12345.]\n\n[PartTwo 67890.]\n\n[PartThree 99999.]"
