"""Document Translation with BodhanTranslationTransformer and LangChain.

Usage:
    python integrations/langchain/examples/document_translation.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from langchain_core.documents import Document

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from bodhan_langchain import BodhanClient, BodhanTranslationTransformer


def main() -> None:
    api_key = os.environ.get("BODHAN_API_KEY")
    if not api_key:
        print("[NOTE] Set BODHAN_API_KEY to run with live Bodhan services.")
        return

    client = BodhanClient(api_key=api_key)

    sample_docs = [
        Document(
            page_content="विद्यालय कल बंद रहेगा क्योंकि भारी बारिश की चेतावनी जारी की गई है।",
            metadata={"source": "school_circular.pdf", "page": 1},
        ),
    ]

    print("Original Document:")
    print(f"Content: {sample_docs[0].page_content}")
    print(f"Metadata: {sample_docs[0].metadata}")

    transformer = BodhanTranslationTransformer(
        target_language="en",
        client=client,
        preserve_original_content=True,
    )

    translated = transformer.transform_documents(sample_docs)

    print("\nTranslated Document:")
    print(f"Content: {translated[0].page_content}")
    print(f"Metadata: {translated[0].metadata}")


if __name__ == "__main__":
    main()
