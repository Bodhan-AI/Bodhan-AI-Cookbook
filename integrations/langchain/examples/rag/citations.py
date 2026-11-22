"""Source attribution and application-validated citation management."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document


@dataclass
class CitationInfo:
    """Metadata representing a verified source citation."""

    citation_id: str
    source_file: str
    page: int
    page_number: int
    chunk_id: int | None = None
    preview: str = ""

    def format_label(self) -> str:
        """Render a clean citation string."""
        return f"{self.source_file} — Page {self.page_number}"


def format_context_with_citations(
    documents: list[Document],
) -> tuple[str, dict[str, CitationInfo]]:
    """Format retrieved passages with structured delimiters and stable citation identifiers [S1], [S2]...

    Retrieved document passages are untrusted passive data. They are encapsulated in
    structured XML-style tags to prevent instruction confusion.

    Args:
        documents: Retrieved Document objects.

    Returns:
        Tuple containing:
            1. Formatted context text to provide to the LLM.
            2. Dictionary mapping citation ID (e.g. 'S1') to verified CitationInfo.
    """
    if not documents:
        return "No relevant documents found.", {}

    context_blocks: list[str] = []
    citation_map: dict[str, CitationInfo] = {}

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
            f'<document id="{cit_id}" source="{filename}" page="{page_1}">\n'
            f"<content>\n{content}\n</content>\n"
            f"</document>"
        )
        context_blocks.append(block)

    formatted_context = "\n\n" + ("\n\n".join(context_blocks)) + "\n\n"
    return formatted_context, citation_map


def extract_and_validate_citations(
    response_text: str,
    citation_map: dict[str, CitationInfo],
) -> tuple[list[str], list[str]]:
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

    valid_labels: list[str] = []
    invalid_ids: list[str] = []
    seen_sources: set[tuple[str, int]] = set()

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


def strip_citation_markers(text: str) -> str:
    """Remove citation markers from generated answer text before translation.

    Strips [S1], [S2], [s1], [Citation: ...], etc., normalizing residual whitespace
    so that translation APIs receive clean prose without syntax artifacts.

    Args:
        text: Raw answer string containing inline citation markers.

    Returns:
        Cleaned prose string suitable for Indic translation.
    """
    if not text:
        return ""

    # Remove [S1], [s1], [S12], etc.
    cleaned = re.sub(r"\s*\[[Ss]\d+\]", "", text)
    # Remove explicit citation tags like [Citation: filename — Page N]
    cleaned = re.sub(r"\s*\[Citation:[^\]]+\]", "", cleaned)
    # Remove any stray double brackets or empty brackets
    cleaned = re.sub(r"\s*\[\s*\]", "", cleaned)
    # Normalize multiple spaces and space before punctuation
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip()


def format_sources_footer(valid_citations: list[str]) -> str:
    """Render application-owned source provenance footer.

    Args:
        valid_citations: List of validated citation labels ('filename — Page N').

    Returns:
        Formatted multi-line sources block.
    """
    if not valid_citations:
        return ""

    lines = ["Sources:"]
    for cit in valid_citations:
        lines.append(f"- {cit}")
    return "\n".join(lines)


def reconstruct_localized_answer(
    translated_body: str,
    valid_citations: list[str],
) -> str:
    """Reconstruct localized answer by appending application-verified source provenance.

    Guarantees that translated model output never determines source filenames or
    page numbers, preserving 100% provenance integrity across languages.

    Args:
        translated_body: Localized answer text in target Indic language.
        valid_citations: List of application-validated citation strings.

    Returns:
        Full localized response with reconstructed source block.
    """
    body = translated_body.strip() if translated_body else ""
    footer = format_sources_footer(valid_citations)

    if not body:
        return footer

    if not footer:
        return body

    return f"{body}\n\n{footer}"
