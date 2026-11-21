"""Bodhan Translation Document Transformer for LangChain."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any, Literal, Sequence

from langchain_core.documents import BaseDocumentTransformer, Document

from .client import BodhanAsyncClient, BodhanClient, _validate_positive_int
from .exceptions import BodhanValidationError
from .models import SUPPORTED_SCRIPTS, SUPPORTED_TRANSLATION_LANGUAGES, validate_translation_script

logger = logging.getLogger(__name__)

ErrorHandling = Literal["raise", "ignore", "warn"]

# Regex for sentence boundaries, including Indic purna viram (।) and double danda (॥)
_SENTENCE_BOUNDARY_REGEX = re.compile(r"([।॥.!?\n]+)")


def _split_oversized_text(text: str, max_chunk_chars: int) -> list[str]:
    """Split text into chunks that strictly never exceed max_chunk_chars.

    Strategy:
    1. Splits on paragraph breaks (\n\n).
    2. For oversized paragraphs, splits on sentence boundaries (।, ., ?, !, newline).
    3. For oversized sentences, splits on word boundaries (whitespace).
    4. For oversized words, performs character-level slicing.
    5. Re-packs pieces greedily up to max_chunk_chars.
    6. Ensures every single returned chunk has len(chunk) <= max_chunk_chars.
    """
    if len(text) <= max_chunk_chars:
        return [text]

    def split_sentence_or_words(sentence: str) -> list[str]:
        if len(sentence) <= max_chunk_chars:
            return [sentence]
        words = sentence.split()
        if not words:
            return [sentence[:max_chunk_chars]]

        pieces: list[str] = []
        curr = ""
        for w in words:
            if len(w) > max_chunk_chars:
                if curr:
                    pieces.append(curr)
                    curr = ""
                for idx in range(0, len(w), max_chunk_chars):
                    pieces.append(w[idx:idx + max_chunk_chars])
                continue

            test = (curr + " " + w).strip() if curr else w
            if len(test) <= max_chunk_chars:
                curr = test
            else:
                if curr:
                    pieces.append(curr)
                curr = w
        if curr:
            pieces.append(curr)
        return pieces

    def split_paragraph(p: str) -> list[str]:
        if len(p) <= max_chunk_chars:
            return [p]

        raw_parts = _SENTENCE_BOUNDARY_REGEX.split(p)
        sentences: list[str] = []
        i = 0
        while i < len(raw_parts):
            part = raw_parts[i]
            if i + 1 < len(raw_parts):
                delim = raw_parts[i + 1]
                part += delim
                i += 2
            else:
                i += 1
            if part:
                sentences.append(part)

        sub_chunks: list[str] = []
        accum = ""
        for s in sentences:
            if len(s) > max_chunk_chars:
                if accum:
                    sub_chunks.append(accum)
                    accum = ""
                word_pieces = split_sentence_or_words(s)
                for wp in word_pieces:
                    test = (accum + " " + wp).strip() if accum else wp
                    if len(test) <= max_chunk_chars:
                        accum = test
                    else:
                        if accum:
                            sub_chunks.append(accum)
                        accum = wp
            else:
                test = (accum + s) if accum else s
                if len(test) <= max_chunk_chars:
                    accum = test
                else:
                    if accum:
                        sub_chunks.append(accum)
                    accum = s
        if accum:
            sub_chunks.append(accum)
        return sub_chunks

    paragraphs = text.split("\n\n")
    packed_chunks: list[str] = []
    current_pack = ""

    for p in paragraphs:
        if not p.strip():
            continue
        if len(p) > max_chunk_chars:
            if current_pack:
                packed_chunks.append(current_pack)
                current_pack = ""
            p_chunks = split_paragraph(p)
            for pc in p_chunks:
                test = (current_pack + "\n\n" + pc).strip() if current_pack else pc
                if len(test) <= max_chunk_chars:
                    current_pack = test
                else:
                    if current_pack:
                        packed_chunks.append(current_pack)
                    current_pack = pc
        else:
            test = (current_pack + "\n\n" + p).strip() if current_pack else p
            if len(test) <= max_chunk_chars:
                current_pack = test
            else:
                if current_pack:
                    packed_chunks.append(current_pack)
                current_pack = p

    if current_pack:
        packed_chunks.append(current_pack)

    # Invariant guarantee: no chunk may exceed max_chunk_chars
    final_guaranteed: list[str] = []
    for chunk in packed_chunks:
        if len(chunk) <= max_chunk_chars:
            final_guaranteed.append(chunk)
        else:
            for k in range(0, len(chunk), max_chunk_chars):
                piece = chunk[k:k + max_chunk_chars]
                if piece:
                    final_guaranteed.append(piece)

    clean_guaranteed = [c for c in final_guaranteed if c.strip()]
    return clean_guaranteed if clean_guaranteed else [text]


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
        source_language: str | None = None,
        client: BodhanClient | None = None,
        async_client: BodhanAsyncClient | None = None,
        script: str | None = None,
        preserve_original_content: bool = False,
        chunk_paragraphs: bool = True,
        max_chunk_chars: int = 1500,
        error_handling: ErrorHandling = "raise",
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.target_language = target_language.strip()
        self.source_language = source_language.strip() if source_language else None
        self.script = script
        self.preserve_original_content = preserve_original_content
        self.chunk_paragraphs = chunk_paragraphs
        self.max_chunk_chars = _validate_positive_int(max_chunk_chars, "max_chunk_chars")
        self.error_handling = error_handling
        self.max_output_tokens = max_output_tokens if max_output_tokens is not None else max_tokens
        self.max_concurrency = _validate_positive_int(max_concurrency, "max_concurrency")
        self._client = client
        self._async_client = async_client

        if self.target_language not in SUPPORTED_TRANSLATION_LANGUAGES:
            raise BodhanValidationError(
                f"Unsupported target language '{self.target_language}'. Supported: "
                f"{', '.join(sorted(SUPPORTED_TRANSLATION_LANGUAGES))}",
                parameter="target_language",
            )
        if self.source_language is not None and self.source_language not in SUPPORTED_TRANSLATION_LANGUAGES:
            raise BodhanValidationError(
                f"Unsupported source language '{self.source_language}'. Supported: "
                f"{', '.join(sorted(SUPPORTED_TRANSLATION_LANGUAGES))}",
                parameter="source_language",
            )
        if self.script is not None:
            validate_translation_script(self.target_language, self.script)
        if max_output_tokens is not None:
            _validate_positive_int(max_output_tokens, "max_output_tokens")
        if max_tokens is not None:
            _validate_positive_int(max_tokens, "max_tokens")
        if self.error_handling not in ("raise", "ignore", "warn"):
            raise BodhanValidationError(
                f"Invalid error_handling '{self.error_handling}'. Must be 'raise', 'ignore', or 'warn'.",
                parameter="error_handling",
            )

    def _get_client(self) -> tuple[BodhanClient, bool]:
        if self._client is not None:
            return self._client, False
        return BodhanClient(), True

    def _get_async_client(self) -> tuple[BodhanAsyncClient, bool]:
        if self._async_client is not None:
            return self._async_client, False
        return BodhanAsyncClient(), True

    def _translate_text(self, text: str, client: BodhanClient) -> tuple[str, str | None, bool]:
        """Translate a single string, splitting into safe chunks if chunk_paragraphs is enabled and text is long."""
        if not text or not text.strip():
            return text, None, False

        if not self.chunk_paragraphs or len(text) <= self.max_chunk_chars:
            resp = client.translate(
                text=text,
                target_language=self.target_language,
                script=self.script,
                max_output_tokens=self.max_output_tokens,
            )
            return resp.text, resp.source_language, resp.truncated

        chunks = _split_oversized_text(text, self.max_chunk_chars)
        translated_parts: list[str] = []
        detected_source: str | None = None
        any_truncated = False

        for chunk in chunks:
            resp = client.translate(
                text=chunk,
                target_language=self.target_language,
                script=self.script,
                max_output_tokens=self.max_output_tokens,
            )
            translated_parts.append(resp.text)
            if resp.source_language:
                detected_source = resp.source_language
            if resp.truncated:
                any_truncated = True

        return "\n\n".join(translated_parts), detected_source, any_truncated

    async def _atranslate_text(
        self,
        text: str,
        async_client: BodhanAsyncClient,
        sem: asyncio.Semaphore,
    ) -> tuple[str, str | None, bool]:
        """Asynchronously translate a single string with bounded concurrency."""
        if not text or not text.strip():
            return text, None, False

        if not self.chunk_paragraphs or len(text) <= self.max_chunk_chars:
            async with sem:
                resp = await async_client.translate(
                    text=text,
                    target_language=self.target_language,
                    script=self.script,
                    max_output_tokens=self.max_output_tokens,
                )
                return resp.text, resp.source_language, resp.truncated

        chunks = _split_oversized_text(text, self.max_chunk_chars)

        async def translate_chunk(c: str) -> tuple[str, str | None, bool]:
            async with sem:
                res = await async_client.translate(
                    text=c,
                    target_language=self.target_language,
                    script=self.script,
                    max_output_tokens=self.max_output_tokens,
                )
                return res.text, res.source_language, res.truncated

        chunk_results = await asyncio.gather(*(translate_chunk(c) for c in chunks))

        translated_parts: list[str] = []
        detected_source: str | None = None
        any_truncated = False

        for t_text, t_src, t_trunc in chunk_results:
            translated_parts.append(t_text)
            if t_src:
                detected_source = t_src
            if t_trunc:
                any_truncated = True

        return "\n\n".join(translated_parts), detected_source, any_truncated

    def transform_documents(
        self,
        documents: Sequence[Document],
        **kwargs: Any,
    ) -> Sequence[Document]:
        """Transform a sequence of documents by translating page_content."""
        client, owns_client = self._get_client()
        try:
            transformed: list[Document] = []

            for doc in documents:
                metadata = copy.deepcopy(doc.metadata)

                if not doc.page_content or not doc.page_content.strip():
                    transformed.append(Document(page_content=doc.page_content, metadata=metadata))
                    continue

                try:
                    translated_text, api_src, truncated = self._translate_text(doc.page_content, client)
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

                # User-declared source language metadata provenance
                # (Bodhan API automatically infers source language and does not guarantee detected language responses)
                if self.source_language:
                    metadata["source_language"] = self.source_language
                    metadata["source_language_origin"] = "user_declared"

                if self.script:
                    metadata["script"] = self.script
                metadata["translation_model"] = "indic-translate"
                metadata["provider"] = "bodhan"
                if truncated:
                    metadata["truncated"] = True

                transformed.append(Document(page_content=translated_text, metadata=metadata))

            return transformed
        finally:
            if owns_client:
                client.close()

    async def atransform_documents(
        self,
        documents: Sequence[Document],
        **kwargs: Any,
    ) -> Sequence[Document]:
        """Asynchronously transform a sequence of documents with bounded concurrency."""
        async_client, owns_client = self._get_async_client()
        sem = asyncio.Semaphore(self.max_concurrency)

        try:
            transformed: list[Document] = []

            async def process_doc(doc: Document) -> Document:
                metadata = copy.deepcopy(doc.metadata)

                if not doc.page_content or not doc.page_content.strip():
                    return Document(page_content=doc.page_content, metadata=metadata)

                try:
                    translated_text, api_src, truncated = await self._atranslate_text(doc.page_content, async_client, sem)
                except Exception as e:
                    if self.error_handling == "raise":
                        raise
                    if self.error_handling == "warn":
                        logger.warning(f"Async translation failed for document: {e}")
                    return Document(page_content=doc.page_content, metadata=metadata)

                if self.preserve_original_content:
                    metadata["original_content"] = doc.page_content

                metadata["target_language"] = self.target_language

                # User-declared source language metadata provenance
                # (Bodhan API automatically infers source language and does not guarantee detected language responses)
                if self.source_language:
                    metadata["source_language"] = self.source_language
                    metadata["source_language_origin"] = "user_declared"

                if self.script:
                    metadata["script"] = self.script
                metadata["translation_model"] = "indic-translate"
                metadata["provider"] = "bodhan"
                if truncated:
                    metadata["truncated"] = True

                return Document(page_content=translated_text, metadata=metadata)

            transformed = await asyncio.gather(*(process_doc(doc) for doc in documents))
            return transformed
        finally:
            if owns_client:
                await async_client.aclose()
