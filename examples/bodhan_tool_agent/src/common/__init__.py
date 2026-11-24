"""Shared utilities: audio manipulation, document rasterization, cost ledger, logging, and retries."""

from src.common.audio import (
    AudioMetadata,
    chunk_wav_silence_aware,
    concatenate_wav_files,
    inspect_audio,
    split_indic_sentences,
)
from src.common.config import BodhanConfig, ensure_utf8_streams
from src.common.document import (
    extract_docx_text,
    get_document_metadata,
    get_pdf_metadata,
    rasterize_document_pages,
    rasterize_pdf_pages,
)
from src.common.ledger import CostLedger
from src.common.logger import bodhan_logger, get_logger, init_logging, set_log_level
from src.common.retry import retry_async, retry_sync, with_retry


def __getattr__(name: str):
    """Lazy import for download module to avoid runpy circular warnings."""
    if name in {"CATALOG", "DatasetResource", "fetch_sample_dataset"}:
        from src.common import download as _download
        return getattr(_download, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AudioMetadata",
    "BodhanConfig",
    "CATALOG",
    "CostLedger",
    "DatasetResource",
    "bodhan_logger",
    "chunk_wav_silence_aware",
    "concatenate_wav_files",
    "ensure_utf8_streams",
    "extract_docx_text",
    "fetch_sample_dataset",
    "get_document_metadata",
    "get_logger",
    "get_pdf_metadata",
    "init_logging",
    "inspect_audio",
    "rasterize_document_pages",
    "rasterize_pdf_pages",
    "retry_async",
    "retry_sync",
    "set_log_level",
    "split_indic_sentences",
    "with_retry",
]
