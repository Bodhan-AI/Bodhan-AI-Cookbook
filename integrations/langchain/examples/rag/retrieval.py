"""Retriever construction and passage query logic."""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore, VectorStoreRetriever


def get_retriever(
    vector_store: InMemoryVectorStore,
    top_k: int = 4,
) -> VectorStoreRetriever:
    """Create a LangChain VectorStoreRetriever with configurable top_k."""
    k = max(1, top_k)
    return vector_store.as_retriever(search_kwargs={"k": k})


def retrieve_passages(
    retriever: VectorStoreRetriever,
    query: str,
) -> list[Document]:
    """Retrieve supporting document passages for a normalized query.

    Args:
        retriever: LangChain retriever instance.
        query: Search query in English.

    Returns:
        List of matching Document objects with preserved metadata.

    Raises:
        ValueError: If query is oversized and rejected by embedding validation.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    results = retriever.invoke(clean_query)
    return results or []
