"""Multilingual embedding providers for LangChain RAG pipeline.

Independent of Groq generation layer. Supports real HuggingFace multilingual
embeddings, OpenAI embeddings, and deterministic test embeddings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Optional

from langchain_core.embeddings import Embeddings


class DeterministicFakeEmbeddings(Embeddings):
    """Deterministic, lightweight embedding model implementing LangChain's Embeddings interface.

    Used exclusively for unit tests and offline integration tests to guarantee
    zero external dependencies, zero model downloads, and 100% reproducible retrieval.
    Not intended as the primary production embedding provider.
    """

    def __init__(self, vocab_size: int = 128) -> None:
        self.vocab_size = vocab_size

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"\b[a-zA-Z0-9_\u0900-\u097F\u0B80-\u0BFF\u0980-\u09FF]{2,}\b", text.lower())

    def _embed(self, text: str) -> List[float]:
        vec = [0.0] * self.vocab_size
        words = self._tokenize(text)
        if not words:
            return vec
        counts = Counter(words)
        for w, count in counts.items():
            idx = abs(hash(w)) % self.vocab_size
            # Sub-linear term frequency scaling
            vec[idx] += 1.0 + math.log(count)
        # L2 normalization for accurate cosine similarity
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


def get_embedding_model(
    provider: str = "huggingface",
    model_name: Optional[str] = None,
) -> Embeddings:
    """Instantiate a LangChain-compatible Embeddings model.

    Args:
        provider: 'huggingface' / 'sentence-transformers', 'openai', or 'fake' / 'test'.
        model_name: Optional model identifier.
            Default for HuggingFace: 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
            (Documented on HuggingFace model card: maps 50+ languages including Hindi, Bengali,
            Tamil, Telugu, Marathi, and English into a shared 384-dimensional dense vector space).

    Returns:
        Instance implementing langchain_core.embeddings.Embeddings.

    Raises:
        ValueError: If provider is unknown.
        ImportError: If required dependency package is not installed.
    """
    prov = provider.strip().lower()

    if prov in ("fake", "test", "mock"):
        return DeterministicFakeEmbeddings()

    if prov in ("huggingface", "sentence-transformers", "hf"):
        chosen_model = model_name or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(model_name=chosen_model)
        except ImportError:
            try:
                from langchain_community.embeddings import HuggingFaceEmbeddings

                return HuggingFaceEmbeddings(model_name=chosen_model)
            except ImportError as exc:
                raise ImportError(
                    "To use HuggingFace embeddings, install langchain-huggingface or sentence-transformers: "
                    "pip install langchain-huggingface sentence-transformers"
                ) from exc

    if prov == "openai":
        chosen_model = model_name or "text-embedding-3-small"
        try:
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(model=chosen_model)
        except ImportError as exc:
            raise ImportError(
                "To use OpenAI embeddings, install langchain-openai: pip install langchain-openai"
            ) from exc

    raise ValueError(
        f"Unsupported embedding provider '{provider}'. Supported providers: 'huggingface', 'openai', 'fake'."
    )
