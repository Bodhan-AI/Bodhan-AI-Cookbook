"""End-to-End Multilingual Retrieval-Augmented Generation (RAG) with Bodhan AI & LangChain.

Demonstrates an end-to-end pipeline:
1. Ingest multilingual scanned PDF notices (Hindi, Tamil, Bengali) via BodhanOCRLoader.
2. Translate extracted documents into English via BodhanTranslationTransformer.
3. Split documents into chunked passages using LangChain text splitters.
4. Index passages into a vector store.
5. Retrieve relevant passages for user questions with document source citations.
6. Answer questions with grounded context and page-level attribution.

Usage:
    python integrations/langchain/examples/multilingual_rag.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Ensure bodhan_langchain is importable
PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from bodhan_langchain import (
    BodhanClient,
    BodhanOCRLoader,
    BodhanTranslationTool,
    BodhanTranslationTransformer,
)


class DeterministicSimpleEmbeddings(Embeddings):
    """Lightweight 64-dimensional bag-of-words embedding for offline demonstrations.

    Allows running this demonstration out-of-the-box without requiring third-party API keys
    (e.g., OpenAI, Cohere) or large downloads. In production, replace with HuggingFaceEmbeddings
    or OpenAIEmbeddings.
    """

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def _embed_text(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        words = text.lower().split()
        for w in words:
            idx = abs(hash(w)) % self.dimensions
            vec[idx] += 1.0
        # Normalize
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_text(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_text(text)


def run_multilingual_rag_pipeline(mock_client: BodhanClient | None = None) -> None:
    """Execute the end-to-end multilingual RAG demonstration."""
    print("=" * 70)
    print("  BODHAN AI x LANGCHAIN: MULTILINGUAL RAG PIPELINE DEMONSTRATION")
    print("=" * 70)

    # 1. Initialize Client
    client = mock_client
    if client is None:
        api_key = os.environ.get("BODHAN_API_KEY")
        if not api_key:
            print("\n[NOTE] BODHAN_API_KEY environment variable not set.")
            print("Please set BODHAN_API_KEY to run against the live Bodhan API.")
            return
        client = BodhanClient(api_key=api_key)

    sample_dir = PKG_DIR / "sample_data"
    sample_files = list(sample_dir.glob("*.pdf"))
    if not sample_files:
        print(f"No sample PDF files found in '{sample_dir}'.")
        return

    print(f"\n[Step 1] Ingesting {len(sample_files)} multilingual documents via BodhanOCRLoader...")
    all_raw_docs: list[Document] = []
    for pdf_file in sample_files:
        print(f"  -> Processing: {pdf_file.name}")
        loader = BodhanOCRLoader(pdf_file, client=client, dpi=150)
        docs = loader.load()
        print(f"     Loaded {len(docs)} page(s). (Chars: {sum(len(d.page_content) for d in docs)})")
        all_raw_docs.extend(docs)

    # 2. Translate Documents to English
    print("\n[Step 2] Translating documents to English via BodhanTranslationTransformer...")
    transformer = BodhanTranslationTransformer(
        target_language="en",
        client=client,
        preserve_original_content=True,
    )
    translated_docs = transformer.transform_documents(all_raw_docs)
    for doc in translated_docs:
        print(f"  -> File: {Path(doc.metadata['source']).name} (Page {doc.metadata['page']})")
        print(f"     Target Language: {doc.metadata.get('target_language')}")
        snippet = doc.page_content.replace("\n", " ")[:90]
        print(f"     Snippet: {snippet}...")

    # 3. Chunking
    print("\n[Step 3] Splitting translated documents with RecursiveCharacterTextSplitter...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=30)
    chunked_docs = text_splitter.split_documents(translated_docs)
    print(f"  -> Generated {len(chunked_docs)} passage chunks across knowledge base.")

    # 4. Vector Indexing
    print("\n[Step 4] Indexing passage chunks into InMemoryVectorStore...")
    embeddings = DeterministicSimpleEmbeddings()
    vector_store = InMemoryVectorStore(embeddings)
    vector_store.add_documents(chunked_docs)
    print("  -> Vector indexing complete.")

    # 5. Querying & Retrieval
    user_queries = [
        "What is the eligibility and scholarship amount for students?",
        "What is the government subsidy for crop insurance?",
        "What are the clinic timings for rural health care?",
    ]

    print("\n[Step 5] Executing Multilingual Retrieval & Grounded Q&A...")
    retriever = vector_store.as_retriever(search_kwargs={"k": 1})

    for q_idx, query in enumerate(user_queries, start=1):
        print(f"\n--- Query {q_idx}: \"{query}\" ---")
        retrieved_results = retriever.invoke(query)
        if not retrieved_results:
            print("  No matching documents found.")
            continue

        top_match = retrieved_results[0]
        source_name = Path(top_match.metadata.get("source", "unknown")).name
        page_num = top_match.metadata.get("page", 1)

        print(f"  [Top Match Source] {source_name} (Page {page_num})")
        print(f"  [Grounded Content] {top_match.page_content.strip()}")
        if "original_content" in top_match.metadata:
            orig_snippet = top_match.metadata["original_content"].replace("\n", " ")[:60]
            print(f"  [Original Source Text] {orig_snippet}...")

    # 6. Demonstrating Agent Translation Tool
    print("\n[Step 6] Demonstrating BodhanTranslationTool for Agents...")
    tool = BodhanTranslationTool(client=client)
    agent_query = "Thank you for using the Bodhan AI Multilingual RAG assistant."
    print(f"  Original English: \"{agent_query}\"")
    hi_trans = tool.invoke({"text": agent_query, "target_language": "hi"})
    print(f"  Translated (Hindi): \"{hi_trans}\"")

    print("\n" + "=" * 70)
    print("  MULTILINGUAL RAG PIPELINE DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_multilingual_rag_pipeline()
