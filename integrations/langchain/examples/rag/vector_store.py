"""Vector store initialization and indexing using LangChain InMemoryVectorStore with tokenizer-aware chunking."""

from __future__ import annotations

import math
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

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


def create_vector_store(
    documents: list[Document],
    embeddings: Embeddings,
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    strict: bool = False,
) -> InMemoryVectorStore:
    """Chunk documents with tokenizer awareness and index them into LangChain InMemoryVectorStore.

    Args:
        documents: Source Document objects with metadata.
        embeddings: Embeddings model instance (such as E5EmbeddingsAdapter).
        max_chunk_tokens: Maximum tokens per chunk budget (default 480).
        chunk_token_overlap: Token overlap between adjacent chunks (default 40).
        tokenizer: Optional tokenizer instance. If omitted, attempts to extract from embeddings.
        chunk_size: Backward-compatibility alias for max_chunk_tokens.
        chunk_overlap: Backward-compatibility alias for chunk_token_overlap.
        strict: Whether to enforce strict tokenizer requirement without fallback.

    Returns:
        Populated InMemoryVectorStore instance ready for similarity retrieval.
    """
    if not documents:
        raise ValueError("Cannot create vector store from empty document list.")

    effective_tokens = chunk_size if chunk_size is not None else max_chunk_tokens
    effective_overlap = chunk_overlap if chunk_overlap is not None else chunk_token_overlap

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

    vector_store = InMemoryVectorStore(embeddings)
    vector_store.add_documents(chunks)

    print(
        f"  -> Successfully indexed {len(chunks)} passage chunk(s) across "
        f"{len(documents)} document(s) in InMemoryVectorStore."
    )
    return vector_store
