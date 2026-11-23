"""Vector store initialization and indexing using ChromaDB and InMemoryVectorStore with tokenizer-aware chunking."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore, VectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

if TYPE_CHECKING:
    from .config import RAGConfig

DEFAULT_MAX_CHUNK_TOKENS = 480
DEFAULT_CHUNK_TOKEN_OVERLAP = 40
E5_HARD_SEQUENCE_LIMIT = 512
E5_DOCUMENT_PREFIX = "passage: "


def get_token_length_fn(tokenizer: Any = None, strict: bool = False):
    """Build a token-counting length function for text splitters."""
    if tokenizer is not None:
        if hasattr(tokenizer, "tokenize"):
            return lambda text: len(tokenizer.tokenize(text))
        if hasattr(tokenizer, "encode"):
            return lambda text: len(tokenizer.encode(text, add_special_tokens=False))
    if strict:
        raise RuntimeError(
            "Tokenizer is required for token counting in strict mode. "
            "Character-based approximation is disabled to prevent silent truncation."
        )
    # Fallback character-based approximation (1 token ~ 4 characters)
    return lambda text: max(1, math.ceil(len(text) / 4))


def count_prefixed_tokens(
    text: str,
    tokenizer: Any = None,
    prefix: str = E5_DOCUMENT_PREFIX,
    strict: bool = False,
) -> int:
    """Count total tokens of a document when prefixed and encoded with special tokens."""
    prefixed = f"{prefix}{text}" if not text.startswith(prefix) else text
    if tokenizer is not None:
        if hasattr(tokenizer, "encode"):
            return len(tokenizer.encode(prefixed, add_special_tokens=True))
        if hasattr(tokenizer, "tokenize"):
            # XLM-RoBERTa special tokens (<s> and </s>) add 2 tokens
            return len(tokenizer.tokenize(prefixed)) + 2
    if strict:
        raise RuntimeError(
            "Tokenizer is required for counting prefixed tokens in strict mode. "
            "Character-based approximation is disabled to prevent silent truncation."
        )
    return max(1, math.ceil(len(prefixed) / 4))


def split_documents_tokenizer_aware(
    documents: list[Document],
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    strict: bool = False,
) -> list[Document]:
    """Split documents using tokenizer-aware boundaries while guaranteeing max sequence safety.

    Splits prioritizing:
      1. Paragraph boundaries ('\\n\\n')
      2. Line breaks ('\\n')
      3. Sentence endings ('. ', '। ')
      4. Word boundaries (' ')
      5. Character fallbacks ('') for oversized unbroken sequences.

    A secondary safety validation ensures that every final chunk, when combined with
    the E5 passage prefix and special tokens, fits strictly within 512 tokens.
    """
    if strict and tokenizer is None:
        raise RuntimeError(
            "split_documents_tokenizer_aware requires an exact tokenizer when strict=True to prevent silent truncation."
        )

    len_fn = get_token_length_fn(tokenizer, strict=strict)
    separators = ["\n\n", "\n", ". ", "। ", " ", ""]

    primary_splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chunk_tokens,
        chunk_overlap=chunk_token_overlap,
        length_function=len_fn,
        separators=separators,
    )

    initial_chunks = primary_splitter.split_documents(documents)

    # Secondary validation: verify every single chunk fits within the 512-token hard limit
    validated_chunks: list[Document] = []
    for chunk in initial_chunks:
        total_tokens = count_prefixed_tokens(chunk.page_content, tokenizer=tokenizer, strict=strict)
        if total_tokens <= E5_HARD_SEQUENCE_LIMIT:
            validated_chunks.append(chunk)
        else:
            # Recursively split oversized chunks with a tighter token budget
            sub_splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_chunk_tokens // 2,
                chunk_overlap=0,
                length_function=len_fn,
                separators=separators,
            )
            sub_chunks = sub_splitter.split_documents([chunk])
            for sc in sub_chunks:
                sc_tokens = count_prefixed_tokens(sc.page_content, tokenizer=tokenizer, strict=strict)
                if sc_tokens <= E5_HARD_SEQUENCE_LIMIT:
                    validated_chunks.append(sc)
                else:
                    # Recursive split with character fallback for unbroken sequences
                    sub_sub_splitter = RecursiveCharacterTextSplitter(
                        chunk_size=max(1, max_chunk_tokens // 4),
                        chunk_overlap=0,
                        length_function=len_fn,
                        separators=[" ", ""],
                    )
                    sub_sub_chunks = sub_sub_splitter.split_documents([sc])
                    for ssc in sub_sub_chunks:
                        ssc_tokens = count_prefixed_tokens(ssc.page_content, tokenizer=tokenizer, strict=strict)
                        if ssc_tokens <= E5_HARD_SEQUENCE_LIMIT:
                            validated_chunks.append(ssc)
                        else:
                            raise ValueError(
                                f"Document passage exceeds E5 limit of {E5_HARD_SEQUENCE_LIMIT} tokens "
                                f"({ssc_tokens} tokens) and cannot be split safely without silent truncation."
                            )

    # Assign 1-indexed chunk identifiers preserving strict reading order
    for idx, chunk in enumerate(validated_chunks, start=1):
        chunk.metadata["chunk_id"] = idx

    return validated_chunks


def normalize_metadata_for_chroma(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Normalize document metadata to Chroma-supported primitive scalar types.

    Chroma restricts metadata values to primitive scalar types: str, int, float, bool.
    Complex types (nested dictionaries, lists) are JSON-serialized so structured
    annotations remain retrievable without triggering runtime errors. None values
    are safely excluded.
    """
    if not metadata:
        return {}
    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float, str)):
            normalized[key] = value
        elif isinstance(value, (dict, list)):
            normalized[key] = json.dumps(value, ensure_ascii=False)
        else:
            normalized[key] = str(value)
    return normalized


def generate_chunk_id(
    source: str | None,
    page: int | None,
    chunk_index: int,
    content: str,
) -> str:
    """Generate a stable, deterministic chunk identifier for idempotent indexing.

    Components:
      - Source identity: normalized base filename or clean identifier.
        Fallback to 'unknown_source' when missing.
      - Page identity: 'p{page}' (e.g. 'p1'). Fallback to 'p0'.
      - Chunk index: 'c{chunk_index}' (1-indexed chunk order).
      - Content hash: 16-character SHA-256 digest of page_content.
    """
    if source:
        clean_source = Path(source).name.replace(" ", "_")
    else:
        clean_source = "unknown_source"

    page_num = page if page is not None else 0
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{clean_source}:p{page_num}:c{chunk_index}:{content_hash}"


def get_vector_store(
    provider: str = "chroma",
    embeddings: Embeddings | None = None,
    persist_directory: str = "./.chroma",
    collection_name: str = "bodhan_multilingual_rag",
    collection_metadata: dict[str, Any] | None = None,
    config: RAGConfig | None = None,
) -> VectorStore:
    """Initialize a LangChain-compatible VectorStore instance (Chroma or InMemory).

    Args:
        provider: Vector store provider ('chroma' or 'inmemory'). Defaults to 'chroma'.
        embeddings: Embeddings model instance (such as E5EmbeddingsAdapter).
        persist_directory: Local storage directory for ChromaDB persistence.
        collection_name: ChromaDB collection identifier.
        collection_metadata: Optional ChromaDB collection metadata (defaults to cosine distance).
        config: Optional RAGConfig object to override provider and directory settings.

    Returns:
        VectorStore: Configured Chroma or InMemoryVectorStore instance.

    Raises:
        ImportError: When Chroma backend is requested but 'langchain-chroma' / 'chromadb' is not installed.
        ValueError: When an unsupported provider is requested or Chroma parameters are invalid.
    """
    effective_provider = config.vector_store_provider if config else provider
    effective_provider = (effective_provider or "").strip().lower()

    if effective_provider == "chroma":
        try:
            from langchain_chroma import Chroma
        except ImportError as exc:
            raise ImportError(
                "ChromaDB vector store requires 'langchain-chroma' and 'chromadb'. "
                "Install with: pip install -r requirements-rag-chroma.txt (or pip install langchain-chroma chromadb)"
            ) from exc

        eff_dir = config.chroma_persist_directory if config else persist_directory
        eff_col = config.chroma_collection_name if config else collection_name

        if not eff_col or not str(eff_col).strip():
            raise ValueError("CHROMA_COLLECTION_NAME cannot be empty when using Chroma vector store.")
        if not eff_dir or not str(eff_dir).strip():
            raise ValueError("CHROMA_PERSIST_DIRECTORY cannot be empty when using Chroma vector store.")

        eff_dir = str(eff_dir).strip()
        eff_col = str(eff_col).strip()
        os.makedirs(eff_dir, exist_ok=True)

        meta = {"hnsw:space": "cosine"}
        if collection_metadata:
            meta.update(collection_metadata)

        return Chroma(
            collection_name=eff_col,
            embedding_function=embeddings,
            persist_directory=eff_dir,
            collection_metadata=meta,
        )

    if effective_provider == "inmemory":
        return InMemoryVectorStore(embedding=embeddings)

    raise ValueError(
        f"Unsupported VECTOR_STORE_PROVIDER: {effective_provider!r}. "
        "Supported providers are 'chroma' and 'inmemory'."
    )


def index_documents(
    vector_store: VectorStore,
    documents: list[Document],
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    strict: bool = False,
) -> list[Document]:
    """Chunk documents with tokenizer awareness and index into vector store with stable IDs and idempotency.

    Args:
        vector_store: Target LangChain VectorStore (Chroma or InMemoryVectorStore).
        documents: Source Document objects with metadata.
        max_chunk_tokens: Maximum tokens per chunk budget (default 480).
        chunk_token_overlap: Token overlap between adjacent chunks (default 40).
        tokenizer: Optional tokenizer instance.
        chunk_size: Backward-compatibility alias for max_chunk_tokens.
        chunk_overlap: Backward-compatibility alias for chunk_token_overlap.
        strict: Whether to enforce strict tokenizer requirement without fallback.

    Returns:
        List of indexed Document chunks with assigned chunk_id and stable IDs.
    """
    if not documents:
        raise ValueError("Cannot index empty document list.")

    effective_tokens = chunk_size if chunk_size is not None else max_chunk_tokens
    effective_overlap = chunk_overlap if chunk_overlap is not None else chunk_token_overlap

    embeddings = getattr(vector_store, "embeddings", None) or getattr(vector_store, "embedding_function", None)
    effective_tokenizer = tokenizer or getattr(embeddings, "tokenizer", None)
    is_strict = strict or getattr(embeddings, "strict_tokenizer", False)

    if is_strict and effective_tokenizer is None:
        raise RuntimeError(
            "Vector store indexing requires a tokenizer in strict mode. "
            "Character-based approximation is disabled to prevent silent token truncation."
        )

    chunks = split_documents_tokenizer_aware(
        documents=documents,
        max_chunk_tokens=effective_tokens,
        chunk_token_overlap=effective_overlap,
        tokenizer=effective_tokenizer,
        strict=is_strict,
    )

    # Generate deterministic chunk IDs
    chunk_ids: list[str] = []
    for chunk in chunks:
        raw_source = chunk.metadata.get("source")
        page_val = chunk.metadata.get("page_number", chunk.metadata.get("page", 1))
        chunk_idx = chunk.metadata.get("chunk_id", 1)
        cid = generate_chunk_id(
            source=raw_source,
            page=page_val,
            chunk_index=chunk_idx,
            content=chunk.page_content,
        )
        chunk_ids.append(cid)

    # Handle Chroma vs InMemory indexing
    is_chroma = hasattr(vector_store, "_collection")
    if is_chroma:
        # 1. Normalize metadata for Chroma compatibility
        for chunk in chunks:
            chunk.metadata = normalize_metadata_for_chroma(chunk.metadata)

        # 2. Idempotent ingestion & stale chunk cleanup per document source
        sources = {chunk.metadata.get("source") for chunk in chunks if chunk.metadata.get("source")}
        for source in sources:
            try:
                existing = vector_store._collection.get(where={"source": source})
                existing_ids = set(existing.get("ids", []))
                new_ids_for_source = {
                    cid for chunk, cid in zip(chunks, chunk_ids, strict=True) if chunk.metadata.get("source") == source
                }
                stale_ids = existing_ids - new_ids_for_source
                if stale_ids:
                    vector_store._collection.delete(ids=list(stale_ids))
            except Exception:
                pass  # If query fails or where is unsupported, proceed to upsert

        vector_store.add_documents(chunks, ids=chunk_ids)
        backend_name = "Chroma"
    else:
        vector_store.add_documents(chunks, ids=chunk_ids)
        backend_name = "InMemoryVectorStore"

    print(
        f"  -> Successfully indexed {len(chunks)} passage chunk(s) across "
        f"{len(documents)} document(s) in {backend_name}."
    )
    return chunks


def create_vector_store(
    documents: list[Document],
    embeddings: Embeddings,
    provider: str | None = None,
    persist_directory: str = "./.chroma",
    collection_name: str = "bodhan_multilingual_rag",
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    strict: bool = False,
    config: RAGConfig | None = None,
) -> VectorStore:
    """Create a vector store and index documents with tokenizer awareness.

    Maintains backwards compatibility for existing callers and test suites while
    supporting ChromaDB local persistence and InMemory backends.
    """
    if not documents:
        raise ValueError("Cannot create vector store from empty document list.")

    effective_provider = provider or (config.vector_store_provider if config else "inmemory")
    store = get_vector_store(
        provider=effective_provider,
        embeddings=embeddings,
        persist_directory=persist_directory,
        collection_name=collection_name,
        config=config,
    )
    index_documents(
        vector_store=store,
        documents=documents,
        max_chunk_tokens=max_chunk_tokens,
        chunk_token_overlap=chunk_token_overlap,
        tokenizer=tokenizer,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strict=strict,
    )
    return store
