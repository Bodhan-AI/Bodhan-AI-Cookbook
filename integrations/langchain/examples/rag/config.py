"""Configuration management for Bodhan x Groq Multilingual RAG."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class RAGConfig:
    """Runtime configuration for the Bodhan x Groq RAG pipeline."""

    # Bodhan API credentials (supports unified or model-scoped keys)
    bodhan_api_key: str | None = field(default=None, repr=False)
    bodhan_ocr_api_key: str | None = field(default=None, repr=False)
    bodhan_translate_api_key: str | None = field(default=None, repr=False)
    bodhan_translation_api_key: str | None = field(default=None, repr=False)

    # Groq credentials & model configuration
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    temperature: float = 0.0
    timeout: float = 30.0
    max_output_tokens: int | None = None

    # Multilingual Embedding configuration (independent of Groq)
    embedding_provider: str = "huggingface"
    embedding_model: str = "intfloat/multilingual-e5-small"

    # Tokenizer-aware chunking & retrieval configuration
    max_chunk_tokens: int = 480
    chunk_token_overlap: int = 40
    max_query_tokens: int = 512
    top_k: int = 4

    # Backwards-compatibility aliases for character-based sizing
    chunk_size: int = 350
    chunk_overlap: int = 40

    # Query & translation preferences
    query_language: str = "hi"
    answer_language: str | None = None
    translate_query: bool = True
    translate_answer_back: bool = True

    # Vector store configuration (default: chroma, optional: inmemory)
    vector_store_provider: str = "chroma"
    chroma_persist_directory: str = "./.chroma"
    chroma_collection_name: str = "bodhan_multilingual_rag"

    def __post_init__(self) -> None:
        """Validate vector store configuration."""
        provider = (self.vector_store_provider or "").strip().lower()
        if provider not in ("chroma", "inmemory"):
            raise ValueError(
                f"Unsupported VECTOR_STORE_PROVIDER: {self.vector_store_provider!r}. "
                "Supported providers are 'chroma' and 'inmemory'."
            )
        self.vector_store_provider = provider

        if self.vector_store_provider == "chroma":
            if not self.chroma_collection_name or not self.chroma_collection_name.strip():
                raise ValueError("CHROMA_COLLECTION_NAME cannot be empty when using Chroma vector store.")
            if not self.chroma_persist_directory or not self.chroma_persist_directory.strip():
                raise ValueError("CHROMA_PERSIST_DIRECTORY cannot be empty when using Chroma vector store.")
            self.chroma_collection_name = self.chroma_collection_name.strip()
            self.chroma_persist_directory = self.chroma_persist_directory.strip()

    @property
    def effective_ocr_key(self) -> str | None:
        """Resolve effective API key for Indic OCR."""
        return self.bodhan_ocr_api_key or self.bodhan_api_key

    @property
    def effective_translation_key(self) -> str | None:
        """Resolve effective API key for Indic Translation.

        Priority order:
          1. bodhan_translate_api_key (canonical / documented)
          2. bodhan_translation_api_key (backward-compatible alias)
          3. bodhan_api_key (unified fallback)
        """
        return self.bodhan_translate_api_key or self.bodhan_translation_api_key or self.bodhan_api_key

    @classmethod
    def from_env(cls) -> RAGConfig:
        """Instantiate configuration from environment variables."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        top_k_str = os.environ.get("TOP_K", "4")
        try:
            top_k = int(top_k_str)
        except ValueError:
            top_k = 4

        temp_str = os.environ.get("TEMPERATURE", "0.0")
        try:
            temp = float(temp_str)
        except ValueError:
            temp = 0.0

        max_tokens_str = os.environ.get("MAX_OUTPUT_TOKENS")
        max_tokens = int(max_tokens_str) if max_tokens_str and max_tokens_str.isdigit() else None

        return cls(
            bodhan_api_key=os.environ.get("BODHAN_API_KEY"),
            bodhan_ocr_api_key=os.environ.get("BODHAN_OCR_API_KEY"),
            bodhan_translate_api_key=os.environ.get("BODHAN_TRANSLATE_API_KEY"),
            bodhan_translation_api_key=os.environ.get("BODHAN_TRANSLATION_API_KEY"),
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=temp,
            timeout=float(os.environ.get("TIMEOUT", "30.0")),
            max_output_tokens=max_tokens,
            embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "huggingface").lower(),
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL",
                "intfloat/multilingual-e5-small",
            ),
            max_chunk_tokens=int(os.environ.get("MAX_CHUNK_TOKENS", "480")),
            chunk_token_overlap=int(os.environ.get("CHUNK_TOKEN_OVERLAP", "40")),
            max_query_tokens=int(os.environ.get("MAX_QUERY_TOKENS", "512")),
            top_k=top_k,
            chunk_size=int(os.environ.get("CHUNK_SIZE", "350")),
            chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "40")),
            query_language=os.environ.get("QUERY_LANGUAGE", "hi"),
            answer_language=os.environ.get("ANSWER_LANGUAGE"),
            translate_query=os.environ.get("TRANSLATE_QUERY", "1").strip().lower() in ("1", "true", "yes"),
            translate_answer_back=os.environ.get("TRANSLATE_ANSWER_BACK", "1").strip().lower() in ("1", "true", "yes"),
            vector_store_provider=os.environ.get("VECTOR_STORE_PROVIDER", "chroma").strip().lower(),
            chroma_persist_directory=os.environ.get("CHROMA_PERSIST_DIRECTORY", "./.chroma").strip(),
            chroma_collection_name=os.environ.get("CHROMA_COLLECTION_NAME", "bodhan_multilingual_rag").strip(),
        )

    def validate_for_live(self) -> list[str]:
        """Validate presence of required credentials before making billable network requests."""
        missing: list[str] = []
        if not self.effective_ocr_key:
            missing.append("BODHAN_OCR_API_KEY or BODHAN_API_KEY")
        if not self.effective_translation_key:
            missing.append("BODHAN_TRANSLATE_API_KEY (or BODHAN_TRANSLATION_API_KEY / BODHAN_API_KEY)")
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        return missing
