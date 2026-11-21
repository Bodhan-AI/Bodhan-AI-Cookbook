"""Vector store initialization and indexing using LangChain InMemoryVectorStore."""

from __future__ import annotations

from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter


def create_vector_store(
    documents: List[Document],
    embeddings: Embeddings,
    chunk_size: int = 350,
    chunk_overlap: int = 40,
) -> InMemoryVectorStore:
    """Chunk documents and index them into LangChain's InMemoryVectorStore.

    Note on Persistence:
        InMemoryVectorStore is an ephemeral, in-process vector store. The index is
        rebuilt from source documents on application startup with zero external service
        dependencies. In production environments with millions of documents, this can
        be replaced with a persistent LangChain VectorStore (e.g. Qdrant, PGVector, or Chroma)
        without changing the downstream retriever or generation interfaces.

    Args:
        documents: Translated Document objects with metadata.
        embeddings: Embeddings model instance.
        chunk_size: Target passage character length.
        chunk_overlap: Overlapping character count between adjacent chunks.

    Returns:
        Populated InMemoryVectorStore instance ready for similarity retrieval.
    """
    if not documents:
        raise ValueError("Cannot create vector store from empty document list.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = splitter.split_documents(documents)

    # Assign chunk identifiers for traceability
    for idx, chunk in enumerate(chunks, start=1):
        chunk.metadata["chunk_id"] = idx

    vector_store = InMemoryVectorStore(embeddings)
    vector_store.add_documents(chunks)

    print(f"  -> Successfully indexed {len(chunks)} passage chunk(s) across {len(documents)} document(s) in InMemoryVectorStore.")
    return vector_store
