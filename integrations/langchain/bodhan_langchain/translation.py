"""Bodhan Translation Document Transformer for LangChain."""

from __future__ import annotations

import copy
import logging
from typing import Any, Literal, Sequence

from langchain_core.documents import BaseDocumentTransformer, Document

from .client import BodhanAsyncClient, BodhanClient
from .exceptions import BodhanValidationError
from .models import SUPPORTED_SCRIPTS, SUPPORTED_TRANSLATION_LANGUAGES

logger = logging.getLogger(__name__)

ErrorHandling = Literal["raise", "ignore", "warn"]


class BodhanTranslationTransformer(BaseDocumentTransformer):
    """Document transformer that translates LangChain Documents using Bodhan indic-translate.

    Preserves original document metadata (source, page, blocks, etc.) while enriching
    the metadata with target language, script variant, and translation model information.

    The original Document objects are never mutated.

    Usage:
        ```python
        from bodhan_langchain import BodhanClient, BodhanTranslationTransformer
        from langchain_core.documents import Document

        client = BodhanClient()
        transformer = BodhanTranslationTransformer(target_language="en", client=client)
        docs = [Document(page_content="नमस्ते दुनिया", metadata={"source": "doc.pdf", "page": 1})]
        translated = transformer.transform_documents(docs)
        print(translated[0].page_content)
        ```
    """

    def __init__(
        self,
        target_language: str,
        client: BodhanClient | None = None,
        async_client: BodhanAsyncClient | None = None,
        script: str | None = None,
        preserve_original_content: bool = False,
        chunk_paragraphs: bool = True,
        max_chunk_chars: int = 1500,
        error_handling: ErrorHandling = "raise",
        max_tokens: int | None = None,
    ) -> None:
        self.target_language = target_language.strip()
        self.script = script
        self.preserve_original_content = preserve_original_content
        self.chunk_paragraphs = chunk_paragraphs
        self.max_chunk_chars = max_chunk_chars
        self.error_handling = error_handling
        self.max_tokens = max_tokens
        self._client = client
        self._async_client = async_client

        if self.target_language not in SUPPORTED_TRANSLATION_LANGUAGES:
            raise BodhanValidationError(
                f"Unsupported target language '{self.target_language}'. Supported: "
                f"{', '.join(sorted(SUPPORTED_TRANSLATION_LANGUAGES))}",
                parameter="target_language",
            )
        if self.script is not None and self.script not in SUPPORTED_SCRIPTS:
            raise BodhanValidationError(
                f"Invalid script '{self.script}'. Must be one of: {', '.join(sorted(SUPPORTED_SCRIPTS))}",
                parameter="script",
            )
        if self.error_handling not in ("raise", "ignore", "warn"):
            raise BodhanValidationError(
                f"Invalid error_handling '{self.error_handling}'. Must be 'raise', 'ignore', or 'warn'.",
                parameter="error_handling",
            )

    def _get_client(self) -> BodhanClient:
        if self._client is not None:
            return self._client
        return BodhanClient()

    def _get_async_client(self) -> BodhanAsyncClient:
        if self._async_client is not None:
            return self._async_client
        return BodhanAsyncClient()

    def _translate_text(self, text: str, client: BodhanClient) -> tuple[str, str | None, bool]:
        """Translate a single string, splitting on paragraphs if chunk_paragraphs is enabled and text is long."""
        if not text or not text.strip():
            return text, None, False

        if not self.chunk_paragraphs or len(text) <= self.max_chunk_chars:
            resp = client.translate(
                text=text,
                target_language=self.target_language,
                script=self.script,
                max_tokens=self.max_tokens,
            )
            return resp.text, resp.source_language, resp.truncated

        # Split on double-newlines (paragraphs)
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return text, None, False

        translated_parts: list[str] = []
        detected_source: str | None = None
        any_truncated = False

        for p in paragraphs:
            resp = client.translate(
                text=p,
                target_language=self.target_language,
                script=self.script,
                max_tokens=self.max_tokens,
            )
            translated_parts.append(resp.text)
            if resp.source_language:
                detected_source = resp.source_language
            if resp.truncated:
                any_truncated = True

        return "\n\n".join(translated_parts), detected_source, any_truncated

    async def _atranslate_text(self, text: str, async_client: BodhanAsyncClient) -> tuple[str, str | None, bool]:
        """Asynchronously translate a single string."""
        if not text or not text.strip():
            return text, None, False

        if not self.chunk_paragraphs or len(text) <= self.max_chunk_chars:
            resp = await async_client.translate(
                text=text,
                target_language=self.target_language,
                script=self.script,
                max_tokens=self.max_tokens,
            )
            return resp.text, resp.source_language, resp.truncated

        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return text, None, False

        translated_parts: list[str] = []
        detected_source: str | None = None
        any_truncated = False

        for p in paragraphs:
            resp = await async_client.translate(
                text=p,
                target_language=self.target_language,
                script=self.script,
                max_tokens=self.max_tokens,
            )
            translated_parts.append(resp.text)
            if resp.source_language:
                detected_source = resp.source_language
            if resp.truncated:
                any_truncated = True

        return "\n\n".join(translated_parts), detected_source, any_truncated

    def transform_documents(
        self,
        documents: Sequence[Document],
        **kwargs: Any,
    ) -> Sequence[Document]:
        """Transform a sequence of documents by translating page_content."""
        client = self._get_client()
        transformed: list[Document] = []

        for doc in documents:
            metadata = copy.deepcopy(doc.metadata)

            if not doc.page_content or not doc.page_content.strip():
                # Preserve empty document unchanged
                transformed.append(Document(page_content=doc.page_content, metadata=metadata))
                continue

            try:
                translated_text, src_lang, truncated = self._translate_text(doc.page_content, client)
            except Exception as e:
                if self.error_handling == "raise":
                    raise
                if self.error_handling == "warn":
                    logger.warning(f"Translation failed for document: {e}")
                transformed.append(Document(page_content=doc.page_content, metadata=metadata))
                continue

            if self.preserve_original_content:
                metadata["original_content"] = doc.page_content

            metadata["target_language"] = self.target_language
            if src_lang:
                metadata["source_language"] = src_lang
            if self.script:
                metadata["script"] = self.script
            metadata["translation_model"] = "indic-translate"
            if truncated:
                metadata["truncated"] = True

            transformed.append(Document(page_content=translated_text, metadata=metadata))

        return transformed

    async def atransform_documents(
        self,
        documents: Sequence[Document],
        **kwargs: Any,
    ) -> Sequence[Document]:
        """Asynchronously transform a sequence of documents by translating page_content."""
        async_client = self._get_async_client()
        transformed: list[Document] = []

        for doc in documents:
            metadata = copy.deepcopy(doc.metadata)

            if not doc.page_content or not doc.page_content.strip():
                transformed.append(Document(page_content=doc.page_content, metadata=metadata))
                continue

            try:
                translated_text, src_lang, truncated = await self._atranslate_text(doc.page_content, async_client)
            except Exception as e:
                if self.error_handling == "raise":
                    raise
                if self.error_handling == "warn":
                    logger.warning(f"Async translation failed for document: {e}")
                transformed.append(Document(page_content=doc.page_content, metadata=metadata))
                continue

            if self.preserve_original_content:
                metadata["original_content"] = doc.page_content

            metadata["target_language"] = self.target_language
            if src_lang:
                metadata["source_language"] = src_lang
            if self.script:
                metadata["script"] = self.script
            metadata["translation_model"] = "indic-translate"
            if truncated:
                metadata["truncated"] = True

            transformed.append(Document(page_content=translated_text, metadata=metadata))

        return transformed
