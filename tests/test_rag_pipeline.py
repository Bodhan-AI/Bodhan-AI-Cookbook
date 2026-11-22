"""Comprehensive unit and integration tests for Bodhan x Groq Multilingual RAG pipeline.

All tests run 100% offline without live network requests, credentials, or paid API usage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# Ensure imports work
REPO_ROOT = Path(__file__).resolve().parent.parent
LANGCHAIN_DIR = REPO_ROOT / "integrations" / "langchain"
if str(LANGCHAIN_DIR) not in sys.path:
    sys.path.insert(0, str(LANGCHAIN_DIR))

from bodhan_langchain import (  # noqa: E402
    BodhanClient,
    BodhanTranslationTool,
    OCRBlock,
    OCRResponse,
    TranslationResponse,
)
from examples.multilingual_rag import answer_multilingual_query, run_rag_pipeline  # noqa: E402
from examples.rag.citations import (  # noqa: E402
    extract_and_validate_citations,
    format_context_with_citations,
)
from examples.rag.config import RAGConfig  # noqa: E402
from examples.rag.embeddings import (  # noqa: E402
    DeterministicFakeEmbeddings,
    E5EmbeddingsAdapter,
    get_embedding_model,
    is_e5_model,
)
from examples.rag.generation import (  # noqa: E402
    build_grounded_messages,
    generate_grounded_answer,
    get_chat_model,
)
from examples.rag.ingestion import ingest_documents  # noqa: E402
from examples.rag.retrieval import get_retriever, retrieve_passages  # noqa: E402
from examples.rag.vector_store import (  # noqa: E402
    count_prefixed_tokens,
    create_vector_store,
    split_documents_tokenizer_aware,
)


class MockGroundedChatModel(BaseChatModel):
    """Mock LangChain BaseChatModel for offline testing."""

    canned_answer: str = "According to [S1], eligible students receive assistance."
    recorded_messages: list[BaseMessage] = []

    @property
    def _llm_type(self) -> str:
        return "mock_chat_model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.recorded_messages.clear()
        self.recorded_messages.extend(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.canned_answer))])


class MockE5Tokenizer:
    """Mock HuggingFace tokenizer for offline tests with predictable token counts."""

    def __init__(self) -> None:
        self.bos_token = "<s>"
        self.eos_token = "</s>"

    def tokenize(self, text: str) -> list[str]:
        # Split on whitespace and punctuation marks
        return re.findall(r"\w+|[^\w\s]", text)

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        tokens = self.tokenize(text)
        token_ids = [abs(hash(t)) % 10000 + 1 for t in tokens]
        if add_special_tokens:
            # XLM-RoBERTa adds <s> (id 0) and </s> (id 2)
            return [0] + token_ids + [2]
        return token_ids


@pytest.fixture
def fake_embeddings() -> DeterministicFakeEmbeddings:
    return DeterministicFakeEmbeddings(vocab_size=384)


@pytest.fixture
def mock_ocr_client() -> BodhanClient:
    client = MagicMock(spec=BodhanClient)
    client.ocr.return_value = OCRResponse(
        text="छात्रवृत्ति आवेदन सूचना। पात्रता: 55% अंक।",
        model="indic-ocr",
        blocks=[OCRBlock(order=1, label="text", text="छात्रवृत्ति आवेदन सूचना।")],
    )
    return client


@pytest.fixture
def mock_trans_client() -> BodhanClient:
    client = MagicMock(spec=BodhanClient)
    client.translate.side_effect = lambda text, target_language, **kwargs: TranslationResponse(
        text=f"Translated [{target_language}]: {text}",
        target_language=target_language,
        source_language="hi" if target_language == "en" else "en",
    )
    return client


# ==============================================================================
# 1. CONFIGURATION TESTS
# ==============================================================================

def test_rag_config_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_API_KEY", "test-bodhan-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("TOP_K", "6")

    cfg = RAGConfig.from_env()
    assert cfg.bodhan_api_key == "test-bodhan-key"
    assert cfg.groq_api_key == "test-groq-key"
    assert cfg.groq_model == "llama-3.3-70b-versatile"
    assert cfg.top_k == 6
    assert cfg.embedding_model == "intfloat/multilingual-e5-small"
    assert cfg.max_chunk_tokens == 480
    assert cfg.chunk_token_overlap == 40
    assert cfg.max_query_tokens == 512
    assert cfg.effective_ocr_key == "test-bodhan-key"
    assert cfg.effective_translation_key == "test-bodhan-key"
    assert cfg.validate_for_live() == []


def test_rag_config_model_scoped_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "ocr-key-123")
    monkeypatch.setenv("BODHAN_TRANSLATION_API_KEY", "trans-key-456")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key-789")

    cfg = RAGConfig.from_env()
    assert cfg.effective_ocr_key == "ocr-key-123"
    assert cfg.effective_translation_key == "trans-key-456"
    assert cfg.validate_for_live() == []


def test_rag_config_missing_keys_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BODHAN_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BODHAN_TRANSLATION_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    cfg = RAGConfig.from_env()
    missing = cfg.validate_for_live()
    assert len(missing) >= 2


# ==============================================================================
# 2. INGESTION TESTS
# ==============================================================================

def test_ingestion_success_and_metadata_preservation(
    mock_ocr_client: BodhanClient,
    mock_trans_client: BodhanClient,
    tmp_path: Path,
) -> None:
    # Create mock PDF
    pdf_path = tmp_path / "mock_notice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 mock content")

    with patch("pymupdf.open") as mock_open:
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\n\x1a\nfakeimagebytes"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__.return_value = mock_page
        mock_open.return_value = mock_doc

        docs = ingest_documents(
            sample_dir=tmp_path,
            ocr_client=mock_ocr_client,
            translation_client=mock_trans_client,
            target_language="en",
        )

    assert len(docs) == 1
    assert "Translated [en]" in docs[0].page_content
    assert docs[0].metadata["source"] == str(pdf_path)
    assert docs[0].metadata["target_language"] == "en"
    assert docs[0].metadata["original_content"] == "छात्रवृत्ति आवेदन सूचना। पात्रता: 55% अंक।"


def test_ingestion_nonexistent_directory_raises(
    mock_ocr_client: BodhanClient,
    mock_trans_client: BodhanClient,
) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        ingest_documents(
            sample_dir="nonexistent/directory/path/123",
            ocr_client=mock_ocr_client,
            translation_client=mock_trans_client,
        )


# ==============================================================================
# 3. E5 EMBEDDING ADAPTER & PROVIDER TESTS
# ==============================================================================

def test_e5_passage_prefix(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    """Verify document embeddings are prefixed with 'passage: ' exactly once."""
    mock_base = MagicMock(spec=DeterministicFakeEmbeddings)
    mock_base.embed_documents.return_value = [[0.1] * 384]

    adapter = E5EmbeddingsAdapter(base_embeddings=mock_base)
    adapter.embed_documents(["Scholarship eligibility rules."])

    mock_base.embed_documents.assert_called_once_with(["passage: Scholarship eligibility rules."])


def test_e5_query_prefix(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    """Verify query embeddings are prefixed with 'query: '."""
    mock_base = MagicMock(spec=DeterministicFakeEmbeddings)
    mock_base.embed_query.return_value = [0.1] * 384

    adapter = E5EmbeddingsAdapter(base_embeddings=mock_base)
    adapter.embed_query("Who is eligible for scholarship?")

    mock_base.embed_query.assert_called_once_with("query: Who is eligible for scholarship?")


def test_e5_prefix_idempotence(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    """Verify that already-prefixed passages and queries are not double-prefixed."""
    mock_base = MagicMock(spec=DeterministicFakeEmbeddings)
    mock_base.embed_documents.return_value = [[0.1] * 384]
    mock_base.embed_query.return_value = [0.1] * 384

    adapter = E5EmbeddingsAdapter(base_embeddings=mock_base)
    adapter.embed_documents(["passage: Already prefixed passage."])
    mock_base.embed_documents.assert_called_once_with(["passage: Already prefixed passage."])

    adapter.embed_query("query: Already prefixed query.")
    mock_base.embed_query.assert_called_once_with("query: Already prefixed query.")


def test_no_e5_prefix_for_openai() -> None:
    """Verify that OpenAI embeddings are not wrapped in E5EmbeddingsAdapter."""
    with patch("langchain_openai.OpenAIEmbeddings") as mock_openai_cls:
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        model = get_embedding_model("openai", model_name="text-embedding-3-small")
        assert model is mock_instance
        assert not isinstance(model, E5EmbeddingsAdapter)


def test_e5_model_detection() -> None:
    """Verify is_e5_model identifies E5 variants and excludes non-E5 models."""
    assert is_e5_model("intfloat/multilingual-e5-small")
    assert is_e5_model("intfloat/multilingual-e5-base")
    assert is_e5_model("intfloat/e5-large-v2")
    assert not is_e5_model("sentence-transformers/all-MiniLM-L6-v2")
    assert not is_e5_model("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    assert not is_e5_model("text-embedding-3-small")


def test_e5_embedding_dimensions(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    """Verify E5 adapter preserves 384 vector dimensionality and order."""
    adapter = E5EmbeddingsAdapter(base_embeddings=fake_embeddings)
    docs = ["First document", "Second document"]
    vectors = adapter.embed_documents(docs)

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert len(vectors[1]) == 384

    query_vec = adapter.embed_query("Search query")
    assert len(query_vec) == 384


def test_e5_tokenizer_length_validation_and_overhead() -> None:
    """Verify token count accounts for prefix and special tokens."""
    tokenizer = MockE5Tokenizer()
    adapter = E5EmbeddingsAdapter(base_embeddings=MagicMock(), tokenizer=tokenizer)

    # "passage: test" has tokens: passage, :, test (3 tokens) + <s>, </s> (2 special tokens) = 5
    tokens = adapter.count_tokens("passage: test")
    assert tokens == 5


def test_query_token_limit_validation() -> None:
    """Verify queries exceeding 512 tokens are rejected with ValueError without silent truncation."""
    tokenizer = MockE5Tokenizer()
    adapter = E5EmbeddingsAdapter(
        base_embeddings=MagicMock(),
        tokenizer=tokenizer,
        max_sequence_length=512,
    )

    # Create oversized query with 600 words
    long_query = "word " * 600
    with pytest.raises(ValueError, match="Query exceeds E5 maximum sequence length of 512 tokens"):
        adapter.embed_query(long_query)


# ==============================================================================
# 4. TOKENIZER-AWARE CHUNKING & VECTOR STORE TESTS
# ==============================================================================

def test_tokenizer_aware_splitting_paragraph_and_sentence() -> None:
    """Verify splitting respects paragraph and sentence boundaries and preserves metadata."""
    tokenizer = MockE5Tokenizer()
    doc = Document(
        page_content=(
            "Paragraph one about scholarships.\n\n"
            "Paragraph two with eligibility rules. Minimum 55% marks required.\n\n"
            "Paragraph three about deadlines."
        ),
        metadata={"source": "scholarship.pdf", "page_number": 1, "page": 0},
    )

    chunks = split_documents_tokenizer_aware(
        documents=[doc],
        max_chunk_tokens=15,
        chunk_token_overlap=0,
        tokenizer=tokenizer,
    )

    assert len(chunks) >= 3
    # Verify reading order
    assert "Paragraph one" in chunks[0].page_content
    # Verify metadata preservation
    for idx, c in enumerate(chunks, start=1):
        assert c.metadata["source"] == "scholarship.pdf"
        assert c.metadata["page_number"] == 1
        assert c.metadata["chunk_id"] == idx


def test_oversized_unbroken_text_safety() -> None:
    """Verify oversized unbroken sequences are split cleanly to fit within 512 tokens without data loss."""
    tokenizer = MockE5Tokenizer()
    # Unbroken string of 1000 characters
    unbroken_text = "A" * 1000
    doc = Document(page_content=unbroken_text, metadata={"source": "raw.txt", "page_number": 1})

    chunks = split_documents_tokenizer_aware(
        documents=[doc],
        max_chunk_tokens=50,
        chunk_token_overlap=0,
        tokenizer=tokenizer,
    )

    assert len(chunks) > 1
    # Verify every chunk fits within sequence limit
    for chunk in chunks:
        tok_len = count_prefixed_tokens(chunk.page_content, tokenizer=tokenizer)
        assert tok_len <= 512

    # Verify no silent truncation: concatenated length equals original
    reconstructed = "".join(c.page_content for c in chunks)
    assert reconstructed == unbroken_text


def test_vector_store_indexing_and_cosine_retrieval(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    """Verify InMemoryVectorStore indexing with E5 adapter and cosine retrieval."""
    adapter = E5EmbeddingsAdapter(base_embeddings=fake_embeddings)
    docs = [
        Document(
            page_content="Students with 55% marks and family income under 150000 are eligible for merit scholarship.",
            metadata={"source": "scholarship.pdf", "page": 0, "page_number": 1},
        ),
        Document(
            page_content="Farmers are eligible for 80% subsidy on micro-irrigation equipment.",
            metadata={"source": "agriculture.pdf", "page": 0, "page_number": 1},
        ),
    ]

    vs = create_vector_store(docs, adapter, max_chunk_tokens=200, chunk_token_overlap=20)
    retriever = get_retriever(vs, top_k=1)
    results = retrieve_passages(retriever, "scholarship eligibility")

    assert len(results) == 1
    assert "scholarship" in results[0].page_content.lower()
    assert results[0].metadata["source"] == "scholarship.pdf"
    assert results[0].metadata["chunk_id"] == 1


def test_vector_store_empty_document_list_raises(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    with pytest.raises(ValueError, match="empty document list"):
        create_vector_store([], fake_embeddings)


def test_retriever_empty_query_returns_empty(fake_embeddings: DeterministicFakeEmbeddings) -> None:
    doc = Document(page_content="Test document", metadata={"source": "doc.pdf"})
    vs = create_vector_store([doc], fake_embeddings)
    retriever = get_retriever(vs, top_k=2)
    assert retrieve_passages(retriever, "") == []


def test_get_embedding_model_missing_dependency_raises() -> None:
    """Verify informative ImportError is raised when huggingface packages are missing."""
    with patch.dict(sys.modules, {"langchain_huggingface": None, "langchain_community": None, "langchain_community.embeddings": None}):
        with pytest.raises(ImportError, match="install langchain-huggingface or sentence-transformers"):
            get_embedding_model("huggingface")


def test_get_embedding_model_provider_selection() -> None:
    """Verify get_embedding_model selects fake provider and rejects unknown providers."""
    fake_mod = get_embedding_model("fake")
    assert isinstance(fake_mod, DeterministicFakeEmbeddings)
    assert fake_mod.vocab_size == 384

    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        get_embedding_model("unsupported_vector_provider")


# ==============================================================================
# 5. CITATIONS TESTS
# ==============================================================================

def test_format_context_with_citations_and_extraction() -> None:
    docs = [
        Document(
            page_content="Scholarship deadline is Dec 31.",
            metadata={"source": "scholarship.pdf", "page": 0, "page_number": 1},
        ),
        Document(
            page_content="Subsidy applies to micro-irrigation.",
            metadata={"source": "agriculture.pdf", "page": 1, "page_number": 2},
        ),
    ]

    ctx_str, cit_map = format_context_with_citations(docs)
    assert "[S1]" in ctx_str
    assert "[S2]" in ctx_str
    assert "scholarship.pdf" in ctx_str
    assert len(cit_map) == 2

    # Model cites S1 and hallucinated S99
    model_response = "Applications close on Dec 31 [S1]. Unknown fact [S99]."
    valid, invalid = extract_and_validate_citations(model_response, cit_map)

    assert valid == ["scholarship.pdf — Page 1"]
    assert invalid == ["S99"]


def test_citation_deduplication() -> None:
    docs = [
        Document(
            page_content="Chunk 1 from page 1",
            metadata={"source": "doc.pdf", "page": 0, "page_number": 1},
        ),
        Document(
            page_content="Chunk 2 from page 1",
            metadata={"source": "doc.pdf", "page": 0, "page_number": 1},
        ),
    ]
    _, cit_map = format_context_with_citations(docs)
    response = "Fact A [S1] and Fact B [S2]."
    valid, invalid = extract_and_validate_citations(response, cit_map)

    # Should deduplicate since both cite doc.pdf — Page 1
    assert len(valid) == 1
    assert valid[0] == "doc.pdf — Page 1"
    assert invalid == []


# ==============================================================================
# 6. GENERATION & GROQ CHAT MODEL TESTS
# ==============================================================================

def test_get_chat_model_missing_key_raises() -> None:
    cfg = RAGConfig(groq_api_key=None)
    with pytest.raises(ValueError, match="GROQ_API_KEY is not set"):
        get_chat_model(cfg)


def test_build_grounded_messages_includes_rules_and_context() -> None:
    messages = build_grounded_messages(
        query="What is the subsidy?",
        context_str="[S1] Farmers receive 80% subsidy.",
    )
    assert len(messages) == 2
    assert "STRICT OPERATIONAL RULES" in messages[0].content
    assert "[S1] Farmers receive 80% subsidy." in messages[1].content
    assert "What is the subsidy?" in messages[1].content


def test_generate_grounded_answer_invokes_model() -> None:
    mock_llm = MockGroundedChatModel(canned_answer="Subsidy is 80% [S1].")
    ans = generate_grounded_answer(mock_llm, "Query?", "[S1] Context")
    assert ans == "Subsidy is 80% [S1]."
    assert len(mock_llm.recorded_messages) == 2


# ==============================================================================
# 7. MULTILINGUAL QUERY HANDLING TESTS
# ==============================================================================

def test_answer_multilingual_query_indic_translation(
    fake_embeddings: DeterministicFakeEmbeddings,
    mock_trans_client: BodhanClient,
) -> None:
    docs = [
        Document(
            page_content="Scholarship amount is 12000 per year.",
            metadata={"source": "scholarship.pdf", "page": 0, "page_number": 1},
        )
    ]
    vs = create_vector_store(docs, fake_embeddings)
    retriever = get_retriever(vs, top_k=1)
    tool = BodhanTranslationTool(client=mock_trans_client)
    mock_llm = MockGroundedChatModel(canned_answer="Amount is 12000 [S1].")

    result = answer_multilingual_query(
        query="छात्रवृत्ति राशि क्या है?",
        query_lang="hi",
        retriever=retriever,
        chat_model=mock_llm,
        translation_tool=tool,
        translate_answer_back=True,
    )

    assert result["original_query"] == "छात्रवृत्ति राशि क्या है?"
    assert "Translated [en]" in result["english_query"]
    assert result["raw_answer"] == "Amount is 12000 [S1]."
    assert result["citations"] == ["scholarship.pdf — Page 1"]
    assert result["localized_answer"] is not None
    assert "Translated [hi]" in result["localized_answer"]


def test_answer_multilingual_query_english_direct(
    fake_embeddings: DeterministicFakeEmbeddings,
) -> None:
    docs = [Document(page_content="Clinic opens at 9 AM.", metadata={"source": "health.pdf", "page_number": 1})]
    vs = create_vector_store(docs, fake_embeddings)
    retriever = get_retriever(vs, top_k=1)
    mock_llm = MockGroundedChatModel(canned_answer="Opens at 9 AM [S1].")

    result = answer_multilingual_query(
        query="When does clinic open?",
        query_lang="en",
        retriever=retriever,
        chat_model=mock_llm,
        translation_tool=None,
        translate_answer_back=False,
    )

    assert result["english_query"] == "When does clinic open?"
    assert result["localized_answer"] is None
    assert result["citations"] == ["health.pdf — Page 1"]


# ==============================================================================
# 8. END-TO-END PIPELINE TEST
# ==============================================================================

def test_full_mocked_rag_pipeline(
    mock_ocr_client: BodhanClient,
    mock_trans_client: BodhanClient,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "sample_notice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 mock pdf data")

    mock_llm = MockGroundedChatModel(canned_answer="Eligible students receive scholarship [S1].")
    cfg = RAGConfig(
        embedding_provider="fake",
        top_k=2,
    )

    with patch("pymupdf.open") as mock_open:
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\n\x1a\nfakebytes"
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc.__getitem__.return_value = mock_page
        mock_open.return_value = mock_doc

        results = run_rag_pipeline(
            config=cfg,
            ocr_client=mock_ocr_client,
            translation_client=mock_trans_client,
            chat_model=mock_llm,
            custom_query="पात्रता क्या है?",
            custom_query_lang="hi",
            sample_dir=tmp_path,
        )

    assert len(results) == 1
    res = results[0]
    assert res["citations"] == ["sample_notice.pdf — Page 1"]
    assert res["raw_answer"] == "Eligible students receive scholarship [S1]."
    assert res["localized_answer"] is not None
