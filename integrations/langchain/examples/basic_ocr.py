"""Basic Document OCR with BodhanOCRLoader and LangChain.

Usage:
    python integrations/langchain/examples/basic_ocr.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from bodhan_langchain import BodhanClient, BodhanOCRLoader


def main() -> None:
    api_key = os.environ.get("BODHAN_API_KEY")
    if not api_key:
        print("[NOTE] Set BODHAN_API_KEY to run with live Bodhan services.")
        return

    sample_pdf = PKG_DIR / "sample_data" / "sample_hindi_scholarship.pdf"
    if not sample_pdf.exists():
        print(f"Sample file not found at {sample_pdf}")
        return

    with BodhanClient(api_key=api_key) as client:
        loader = BodhanOCRLoader(sample_pdf, client=client, dpi=150)

        print(f"Loading and processing {sample_pdf.name}...")
        documents = loader.load()

        for doc in documents:
            print(f"\n--- Page {doc.metadata['page_number']} of {doc.metadata['total_pages']} ---")
            print(doc.page_content)
            print(f"\nLayout Blocks Detected: {len(doc.metadata.get('blocks', []))}")


if __name__ == "__main__":
    main()
