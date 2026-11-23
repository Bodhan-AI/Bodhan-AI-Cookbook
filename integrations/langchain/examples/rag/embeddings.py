"""Multilingual embedding providers for LangChain RAG pipeline.

Independent of Groq generation layer. Supports HuggingFace multilingual E5 embeddings
with asymmetric passage and query prefixing, OpenAI embeddings, and deterministic
test embeddings.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any

from langchain_core.embeddings import Embeddings

E5_DEFAULT_MODEL = "intfloat/multilingual-e5-small"
E5_MAX_SEQUENCE_LENGTH = 512
E5_DOCUMENT_PREFIX = "passage: "
E5_QUERY_PREFIX = "query: "


class DeterministicFakeEmbeddings(Embeddings):
    """Deterministic, lightweight embedding model implementing LangChain's Embeddings interface.

    Used exclusively for unit tests and offline integration tests to guarantee
    zero external dependencies, zero model downloads, and 100% reproducible retrieval.
    Default vocab_size is 384 to match multilingual-e5-small vector dimensionality.
    """

    def __init__(self, vocab_size: int = 384) -> None:
        self.vocab_size = vocab_size

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b[a-zA-Z0-9_\u0900-\u097F\u0B80-\u0BFF\u0980-\u09FF]{2,}\b", text.lower())

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.vocab_size
        words = self._tokenize(text)
        if not words:
            return vec
        counts = Counter(words)
        for w, count in counts.items():
            idx = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16) % self.vocab_size
            # Sub-linear term frequency scaling
            vec[idx] += 1.0 + math.log(count)
        # L2 normalization for accurate cosine similarity
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class E5EmbeddingsAdapter(Embeddings):
    """LangChain Embeddings adapter applying E5 retrieval prefixes and token limit validation.

    E5 models (such as intfloat/multilingual-e5-small) require asymmetric prefixing:
      - Document embeddings: "passage: <text>"
      - Query embeddings:    "query: <question>"

    This adapter:
      1. Prefixes every document with "passage: " (if not already prefixed).
      2. Prefixes every query with "query: " (if not already prefixed).
      3. Validates query token length against max_sequence_length, raising ValueError if oversized.
      4. Preserves 1:1 embedding order.
      5. Guarantees idempotency (prevents accidental double-prefixing).
    """

    def __init__(
        self,
        base_embeddings: Embeddings,
        tokenizer: Any = None,
        max_sequence_length: int = E5_MAX_SEQUENCE_LENGTH,
        max_query_tokens: int | None = None,
        document_prefix: str = E5_DOCUMENT_PREFIX,
        query_prefix: str = E5_QUERY_PREFIX,
        strict_tokenizer: bool = False,
    ) -> None:
        self.base_embeddings = base_embeddings
        self.tokenizer = tokenizer
        self.max_sequence_length = max_sequence_length
        if max_query_tokens is not None:
            self.max_query_tokens = min(max_query_tokens, max_sequence_length)
        else:
            self.max_query_tokens = max_sequence_length
        self.document_prefix = document_prefix
        self.query_prefix = query_prefix
        self.strict_tokenizer = strict_tokenizer
        if self.strict_tokenizer and self.tokenizer is None:
            raise RuntimeError(
                "E5EmbeddingsAdapter initialized in strict mode without a tokenizer. "
                "An exact tokenizer is required to prevent silent token truncation."
            )

    def format_document(self, text: str) -> str:
        """Format document text with E5 passage prefix idempotently."""
        if text.startswith(self.document_prefix):
            return text
        return f"{self.document_prefix}{text}"

    def format_query(self, text: str) -> str:
        """Format query text with E5 query prefix idempotently."""
        if text.startswith(self.query_prefix):
            return text
        return f"{self.query_prefix}{text}"

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using the configured tokenizer, or fallback estimator when not strict."""
        if self.tokenizer is not None:
            if hasattr(self.tokenizer, "encode"):
                return len(self.tokenizer.encode(text, add_special_tokens=True))
            if hasattr(self.tokenizer, "tokenize"):
                # XLM-RoBERTa uses <s> and </s> as boundary special tokens (+2)
                return len(self.tokenizer.tokenize(text)) + 2
        if self.strict_tokenizer:
            raise RuntimeError(
                "Cannot count tokens: strict_tokenizer is True, but no tokenizer was provided. "
                "An exact tokenizer is required to prevent silent token truncation."
            )
        # Fallback approximation: 1 token ~ 4 characters (only when strict_tokenizer=False)
        return max(1, math.ceil(len(text) / 4))

    def validate_query(self, formatted_query: str) -> None:
        """Validate that the formatted query does not exceed the model token limit or max_query_tokens."""
        token_count = self.count_tokens(formatted_query)
        effective_limit = min(self.max_sequence_length, self.max_query_tokens)
        if token_count > effective_limit:
            raise ValueError(
                f"Query exceeds maximum allowed sequence length of {effective_limit} tokens "
                f"(received {token_count} tokens). Oversized queries cannot be retrieved "
                f"reliably and silent truncation is prohibited."
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Prefix each document with 'passage: ' and generate dense embeddings."""
        prefixed_texts = [self.format_document(t) for t in texts]
        return self.base_embeddings.embed_documents(prefixed_texts)

    def embed_query(self, text: str) -> list[float]:
        """Prefix query with 'query: ', validate token limits, and generate dense embedding."""
        prefixed_query = self.format_query(text)
        self.validate_query(prefixed_query)
        return self.base_embeddings.embed_query(prefixed_query)


def get_model_tokenizer(model_name: str, strict: bool = False) -> Any | None:
    """Attempt to load the tokenizer for the given model if transformers is available.

    If strict is True, raises a RuntimeError if the tokenizer cannot be loaded.
    """
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(model_name)
    except Exception as exc:
        if strict:
            raise RuntimeError(
                f"Failed to load tokenizer for model '{model_name}'. "
                f"Production E5 embedding operations require an exact tokenizer to guarantee sequences "
                f"do not exceed the 512-token limit and prevent silent truncation. "
                f"Ensure 'transformers' is installed and model weights/cache are accessible. "
                f"Underlying error: {exc}"
            ) from exc
        return None


def is_e5_model(model_name: str) -> bool:
    """Determine whether the specified model identifier belongs to the E5 model family."""
    m_lower = model_name.strip().lower()
    return "e5" in m_lower


def get_embedding_model(
    provider: str = "huggingface",
    model_name: str | None = None,
    tokenizer: Any = None,
    api_key: str | None = None,
    max_query_tokens: int | None = None,
) -> Embeddings:
    """Instantiate a LangChain-compatible Embeddings model.

    Args:
        provider: 'huggingface' / 'sentence-transformers', 'openai', or 'fake' / 'test'.
        model_name: Optional model identifier.
            Default for HuggingFace: 'intfloat/multilingual-e5-small' (384 dimensions,
            512 token max sequence length, requires 'passage: ' and 'query: ' prefixes).
        tokenizer: Optional tokenizer instance for token validation.
        api_key: Optional API key for external providers (e.g. OpenAI).
        max_query_tokens: Optional maximum tokens allowed for query embedding validation.

    Returns:
        Instance implementing langchain_core.embeddings.Embeddings.

    Raises:
        ValueError: If provider is unknown.
        ImportError: If required dependency package is not installed.
    """
    prov = provider.strip().lower()

    if prov in ("fake", "test", "mock"):
        return DeterministicFakeEmbeddings(vocab_size=384)

    if prov in ("huggingface", "sentence-transformers", "hf"):
        chosen_model = model_name or E5_DEFAULT_MODEL
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            base = HuggingFaceEmbeddings(
                model_name=chosen_model,
                encode_kwargs={"normalize_embeddings": True},
            )
        except ImportError:
            try:
                from langchain_community.embeddings import HuggingFaceEmbeddings

                base = HuggingFaceEmbeddings(
                    model_name=chosen_model,
                    encode_kwargs={"normalize_embeddings": True},
                )
            except ImportError as exc:
                raise ImportError(
                    "To use HuggingFace embeddings, install langchain-huggingface or sentence-transformers: "
                    "pip install langchain-huggingface sentence-transformers"
                ) from exc

        if is_e5_model(chosen_model):
            tok = tokenizer
            if tok is None:
                tok = get_model_tokenizer(chosen_model, strict=True)
            return E5EmbeddingsAdapter(
                base_embeddings=base,
                tokenizer=tok,
                max_sequence_length=E5_MAX_SEQUENCE_LENGTH,
                max_query_tokens=max_query_tokens,
                strict_tokenizer=True,
            )
        return base

    if prov == "openai":
        chosen_model = model_name or "text-embedding-3-small"
        try:
            from langchain_openai import OpenAIEmbeddings

            kwargs: dict[str, Any] = {"model": chosen_model}
            if api_key:
                kwargs["api_key"] = api_key
            return OpenAIEmbeddings(**kwargs)
        except ImportError as exc:
            raise ImportError(
                "To use OpenAI embeddings, install langchain-openai: pip install langchain-openai"
            ) from exc

    raise ValueError(
        f"Unsupported embedding provider '{provider}'. Supported providers: 'huggingface', 'openai', 'fake'."
    )
