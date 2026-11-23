"""Modular RAG package for Bodhan AI x LangChain with Groq generation."""

from .citations import (
    CitationInfo,
    extract_and_validate_citations,
    format_context_with_citations,
)
from .config import RAGConfig
from .embeddings import (
    DeterministicFakeEmbeddings,
    get_embedding_model,
)
from .generation import (
    build_grounded_messages,
    generate_grounded_answer,
    get_chat_model,
)
from .ingestion import ingest_documents
from .retrieval import get_retriever, retrieve_passages
from .vector_store import (
    StaleChunkCleanupError,
    create_vector_store,
    generate_chunk_id,
    get_source_identity,
    get_vector_store,
    index_documents,
    normalize_metadata_for_chroma,
)

__all__ = [
    "RAGConfig",
    "ingest_documents",
    "get_embedding_model",
    "DeterministicFakeEmbeddings",
    "create_vector_store",
    "get_vector_store",
    "index_documents",
    "generate_chunk_id",
    "get_source_identity",
    "StaleChunkCleanupError",
    "normalize_metadata_for_chroma",
    "get_retriever",
    "retrieve_passages",
    "CitationInfo",
    "format_context_with_citations",
    "extract_and_validate_citations",
    "get_chat_model",
    "build_grounded_messages",
    "generate_grounded_answer",
]
