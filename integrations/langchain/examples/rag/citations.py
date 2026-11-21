"""Source attribution and application-validated citation management."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document


@dataclass
class CitationInfo:
    """Metadata representing a verified source citation."""

    citation_id: str
    source_file: str
    page: int
    page_number: int
    chunk_id: Optional[int] = None
    preview: str = ""

    def format_label(self) -> str:
        """Render a clean citation string."""
        return f"{self.source_file} — Page {self.page_number}"


def format_context_with_citations(
    documents: List[Document],
) -> Tuple[str, Dict[str, CitationInfo]]:
    """Format retrieved passages with stable citation identifiers [S1], [S2]...

    Args:
        documents: Retrieved Document objects.

    Returns:
        Tuple containing:
            1. Formatted context text to provide to the LLM.
            2. Dictionary mapping citation ID (e.g. 'S1') to verified CitationInfo.
    """
    if not documents:
        return "No relevant documents found.", {}

    context_blocks: List[str] = []
    citation_map: Dict[str, CitationInfo] = {}

    for idx, doc in enumerate(documents, start=1):
        cit_id = f"S{idx}"
        raw_source = doc.metadata.get("source", "unknown_document")
        filename = Path(raw_source).name
        page_0 = int(doc.metadata.get("page", 0))
        page_1 = int(doc.metadata.get("page_number", page_0 + 1))
        chunk_id = doc.metadata.get("chunk_id")
        content = doc.page_content.strip()

        citation_map[cit_id] = CitationInfo(
            citation_id=cit_id,
            source_file=filename,
            page=page_0,
            page_number=page_1,
            chunk_id=chunk_id,
            preview=content[:100],
        )

        block = (
            f"[{cit_id}]\n"
            f"Source: {filename}\n"
            f"Page: {page_1}\n"
            f"Content:\n{content}"
        )
        context_blocks.append(block)

    formatted_context = "\n\n" + ("\n\n---\n\n".join(context_blocks)) + "\n\n"
    return formatted_context, citation_map


def extract_and_validate_citations(
    response_text: str,
    citation_map: Dict[str, CitationInfo],
) -> Tuple[List[str], List[str]]:
    """Extract citation identifiers from LLM response and validate against retrieved context.

    Prevents the LLM from hallucinating citations: only identifiers genuinely present
    in citation_map are resolved to their source files and page numbers.

    Args:
        response_text: Raw generation output from the model.
        citation_map: Mapping of valid identifiers [S1..Sn] to CitationInfo.

    Returns:
        Tuple containing:
            1. List of validated, deduplicated source labels ('filename — Page N').
            2. List of invalid/hallucinated citation IDs detected (if any).
    """
    if not response_text:
        return [], []

    # Match markers like [S1], [S2], [s1], [S1, S2], etc.
    found_tokens = re.findall(r"\[[Ss](\d+)\]", response_text)
    cited_ids = [f"S{token}" for token in found_tokens]

    valid_labels: List[str] = []
    invalid_ids: List[str] = []
    seen_sources: set[Tuple[str, int]] = set()

    for cid in cited_ids:
        if cid in citation_map:
            info = citation_map[cid]
            key = (info.source_file, info.page_number)
            if key not in seen_sources:
                seen_sources.add(key)
                valid_labels.append(info.format_label())
        else:
            invalid_ids.append(cid)

    return valid_labels, invalid_ids
