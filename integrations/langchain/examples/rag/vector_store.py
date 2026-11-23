"""Vector store initialization and indexing using ChromaDB and InMemoryVectorStore with tokenizer-aware chunking."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore, VectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

if TYPE_CHECKING:
    from .config import RAGConfig

DEFAULT_MAX_CHUNK_TOKENS = 480
DEFAULT_CHUNK_TOKEN_OVERLAP = 40
E5_HARD_SEQUENCE_LIMIT = 512
E5_DOCUMENT_PREFIX = "passage: "


class StaleChunkCleanupError(RuntimeError):
    """Raised when deletion of stale chunks fails after successful document insertion.

    Preserves newly inserted content while surfacing diagnostic information and uncleaned
    stale chunk IDs to the caller.
    """

    def __init__(self, message: str, stale_ids: list[str] | set[str] | None = None) -> None:
        super().__init__(message)
        self.stale_ids = sorted(list(stale_ids)) if stale_ids else []


def _get_item_metadata(item: Any) -> dict[str, Any]:
    """Safely extract metadata dict from an InMemoryVectorStore item or Document."""
    if isinstance(item, dict):
        meta = item.get("metadata")
        return meta if isinstance(meta, dict) else {}
    if hasattr(item, "metadata") and isinstance(item.metadata, dict):
        return item.metadata
    return {}


def _extract_page_number(meta: dict[str, Any] | None) -> int:
    """Safely extract an integer page number from metadata, defaulting to 1."""
    if not meta:
        return 1
    for key in ("page_number", "page"):
        val = meta.get(key)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return 1


def get_source_identity(
    metadata: dict[str, Any] | None = None,
    content: str | None = None,
) -> str:
    """Derive a stable, collision-free source identity string.

    Derivation hierarchy:
      1. Explicit 'source_id' in metadata if present and non-empty.
      2. Explicit 'document_id' in metadata if present and non-empty.
      3. Normalized full source path or URI from metadata['source']:
         Combines basename and a 64-character full SHA-256 hash of the normalized path:
         f"{base_name}_{path_hash}". This guarantees distinct identities for files with
         identical basenames in different directories (e.g. '/notices/2025/doc.pdf' vs
         '/notices/2026/doc.pdf') while keeping chunk IDs readable and citation provenance intact.
      4. If none of the above exists, raises ValueError. Content-only hashing is strictly
         disallowed as a substitute for document identity to prevent collisions between
         unrelated documents with identical content.

    Note:
      Explicit IDs ('source_id' and 'document_id') are preserved verbatim without lossy
      normalization (e.g. 'a b' and 'a_b' remain distinct).
    """
    if metadata:
        explicit_src_id = metadata.get("source_id")
        if explicit_src_id is not None and str(explicit_src_id).strip():
            return str(explicit_src_id).strip()

        explicit_doc_id = metadata.get("document_id")
        if explicit_doc_id is not None and str(explicit_doc_id).strip():
            return str(explicit_doc_id).strip()

        raw_source = metadata.get("source")
        if raw_source is not None and str(raw_source).strip():
            raw_str = str(raw_source).strip()
            norm_path = os.path.normpath(raw_str).replace("\\", "/")
            base_name = Path(norm_path).name or "doc"
            path_hash = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()
            return f"{base_name}_{path_hash}"

    raise ValueError(
        "Cannot derive a stable document identity: document metadata must contain "
        "at least one of 'source_id', 'document_id', or 'source'. Content-derived "
        "hashing is disallowed to prevent identity collisions across distinct documents."
    )


def get_token_length_fn(tokenizer: Any = None, strict: bool = False):
    """Build a token-counting length function for text splitters."""
    if tokenizer is not None:
        if hasattr(tokenizer, "tokenize"):
            return lambda text: len(tokenizer.tokenize(text))
        if hasattr(tokenizer, "encode"):
            return lambda text: len(tokenizer.encode(text, add_special_tokens=False))
    if strict:
        raise RuntimeError(
            "Tokenizer is required for token counting in strict mode. "
            "Character-based approximation is disabled to prevent silent truncation."
        )
    # Fallback character-based approximation (1 token ~ 4 characters)
    return lambda text: max(1, math.ceil(len(text) / 4))


def count_prefixed_tokens(
    text: str,
    tokenizer: Any = None,
    prefix: str = E5_DOCUMENT_PREFIX,
    strict: bool = False,
) -> int:
    """Count total tokens of a document when prefixed and encoded with special tokens."""
    prefixed = f"{prefix}{text}" if not text.startswith(prefix) else text
    if tokenizer is not None:
        if hasattr(tokenizer, "encode"):
            return len(tokenizer.encode(prefixed, add_special_tokens=True))
        if hasattr(tokenizer, "tokenize"):
            # XLM-RoBERTa special tokens (<s> and </s>) add 2 tokens
            return len(tokenizer.tokenize(prefixed)) + 2
    if strict:
        raise RuntimeError(
            "Tokenizer is required for counting prefixed tokens in strict mode. "
            "Character-based approximation is disabled to prevent silent truncation."
        )
    return max(1, math.ceil(len(prefixed) / 4))


def split_documents_tokenizer_aware(
    documents: list[Document],
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    strict: bool = False,
) -> list[Document]:
    """Split documents using tokenizer-aware boundaries while guaranteeing max sequence safety.

    Splits prioritizing:
      1. Paragraph boundaries ('\\n\\n')
      2. Line breaks ('\\n')
      3. Sentence endings ('. ', '। ')
      4. Word boundaries (' ')
      5. Character fallbacks ('') for oversized unbroken sequences.

    A secondary safety validation ensures that every final chunk, when combined with
    the E5 passage prefix and special tokens, fits strictly within 512 tokens.
    """
    if strict and tokenizer is None:
        raise RuntimeError(
            "split_documents_tokenizer_aware requires an exact tokenizer when strict=True to prevent silent truncation."
        )

    len_fn = get_token_length_fn(tokenizer, strict=strict)
    separators = ["\n\n", "\n", ". ", "। ", " ", ""]

    primary_splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chunk_tokens,
        chunk_overlap=chunk_token_overlap,
        length_function=len_fn,
        separators=separators,
    )

    initial_chunks = primary_splitter.split_documents(documents)

    # Secondary validation: verify every single chunk fits within the 512-token hard limit
    validated_chunks: list[Document] = []
    for chunk in initial_chunks:
        total_tokens = count_prefixed_tokens(chunk.page_content, tokenizer=tokenizer, strict=strict)
        if total_tokens <= E5_HARD_SEQUENCE_LIMIT:
            validated_chunks.append(chunk)
        else:
            # Recursively split oversized chunks with a tighter token budget
            sub_splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_chunk_tokens // 2,
                chunk_overlap=0,
                length_function=len_fn,
                separators=separators,
            )
            sub_chunks = sub_splitter.split_documents([chunk])
            for sc in sub_chunks:
                sc_tokens = count_prefixed_tokens(sc.page_content, tokenizer=tokenizer, strict=strict)
                if sc_tokens <= E5_HARD_SEQUENCE_LIMIT:
                    validated_chunks.append(sc)
                else:
                    # Recursive split with character fallback for unbroken sequences
                    sub_sub_splitter = RecursiveCharacterTextSplitter(
                        chunk_size=max(1, max_chunk_tokens // 4),
                        chunk_overlap=0,
                        length_function=len_fn,
                        separators=[" ", ""],
                    )
                    sub_sub_chunks = sub_sub_splitter.split_documents([sc])
                    for ssc in sub_sub_chunks:
                        ssc_tokens = count_prefixed_tokens(ssc.page_content, tokenizer=tokenizer, strict=strict)
                        if ssc_tokens <= E5_HARD_SEQUENCE_LIMIT:
                            validated_chunks.append(ssc)
                        else:
                            raise ValueError(
                                f"Document passage exceeds E5 limit of {E5_HARD_SEQUENCE_LIMIT} tokens "
                                f"({ssc_tokens} tokens) and cannot be split safely without silent truncation."
                            )

    # Assign 1-indexed chunk identifiers preserving strict reading order
    for idx, chunk in enumerate(validated_chunks, start=1):
        chunk.metadata["chunk_id"] = idx

    return validated_chunks


def normalize_metadata_for_chroma(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Normalize document metadata to Chroma-supported primitive scalar types.

    Chroma restricts metadata values to primitive scalar types: str, int, float, bool.
    Complex types (nested dictionaries, lists) are JSON-serialized so structured
    annotations remain retrievable without triggering runtime errors. None values
    are safely excluded.
    """
    if not metadata:
        return {}
    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float, str)):
            normalized[key] = value
        elif isinstance(value, (dict, list)):
            normalized[key] = json.dumps(value, ensure_ascii=False)
        else:
            normalized[key] = str(value)
    return normalized


def generate_chunk_id(
    source: str | None,
    page: int | None,
    chunk_index: int,
    content: str,
    source_id: str | None = None,
) -> str:
    """Generate a stable, deterministic chunk identifier for idempotent indexing.

    Components:
      - Source identity: explicit source_id if provided; otherwise derived via get_source_identity
        from source. Preserves spaces and punctuation without lossy normalization.
      - Page identity: 'p{page}' (e.g. 'p1'). Fallback to 'p0' if page is None.
      - Chunk index: 'c{chunk_index}' (1-indexed chunk order).
      - Content hash: 16-character SHA-256 digest of page_content.
    """
    if source_id is not None and str(source_id).strip():
        src_ident = str(source_id).strip()
    elif source is not None and str(source).strip():
        src_ident = get_source_identity(metadata={"source": source})
    else:
        raise ValueError(
            "Cannot generate chunk ID without a valid document identifier. "
            "Please provide 'source_id' or 'source' in document metadata."
        )

    page_num = page if page is not None else 0
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{src_ident}:p{page_num}:c{chunk_index}:{content_hash}"


def get_vector_store(
    provider: str = "chroma",
    embeddings: Embeddings | None = None,
    persist_directory: str = "./.chroma",
    collection_name: str = "bodhan_multilingual_rag",
    collection_metadata: dict[str, Any] | None = None,
    config: RAGConfig | None = None,
) -> VectorStore:
    """Initialize a LangChain-compatible VectorStore instance (Chroma or InMemory).

    Args:
        provider: Vector store provider ('chroma' or 'inmemory'). Defaults to 'chroma'.
        embeddings: Embeddings model instance (such as E5EmbeddingsAdapter). Required for Chroma.
        persist_directory: Local storage directory for ChromaDB persistence.
        collection_name: ChromaDB collection identifier.
        collection_metadata: Optional ChromaDB collection metadata (defaults to cosine distance).
        config: Optional RAGConfig object to override provider and directory settings.

    Returns:
        VectorStore: Configured Chroma or InMemoryVectorStore instance.

    Raises:
        ImportError: When Chroma backend is requested but 'langchain-chroma' / 'chromadb' is not installed.
        ValueError: When an unsupported provider is requested, Chroma parameters are invalid,
                    embeddings is None for Chroma, or collection exists with an incompatible distance metric.
    """
    effective_provider = config.vector_store_provider if config else provider
    effective_provider = (effective_provider or "").strip().lower()

    if effective_provider == "chroma":
        try:
            from langchain_chroma import Chroma
        except ImportError as exc:
            raise ImportError(
                "ChromaDB vector store requires 'langchain-chroma' and 'chromadb'. "
                "Install with: pip install -r requirements-rag-chroma.txt (or pip install langchain-chroma chromadb)"
            ) from exc

        if embeddings is None:
            raise ValueError(
                "An explicit LangChain Embeddings instance (such as E5EmbeddingsAdapter) "
                "is required when using Chroma vector store. Passing embeddings=None is disallowed "
                "to prevent Chroma from silently falling back to its default all-MiniLM-L6-v2 model."
            )

        eff_dir = config.chroma_persist_directory if config else persist_directory
        eff_col = config.chroma_collection_name if config else collection_name

        if not eff_col or not str(eff_col).strip():
            raise ValueError("CHROMA_COLLECTION_NAME cannot be empty when using Chroma vector store.")
        if not eff_dir or not str(eff_dir).strip():
            raise ValueError("CHROMA_PERSIST_DIRECTORY cannot be empty when using Chroma vector store.")

        eff_dir = str(eff_dir).strip()
        eff_col = str(eff_col).strip()
        os.makedirs(eff_dir, exist_ok=True)

        meta = {"hnsw:space": "cosine"}
        if collection_metadata:
            meta.update(collection_metadata)

        store = Chroma(
            collection_name=eff_col,
            embedding_function=embeddings,
            persist_directory=eff_dir,
            collection_metadata=meta,
            collection_configuration={"hnsw": {"space": "cosine"}},
        )

        # Verify actual persisted collection distance metric
        if hasattr(store, "_collection"):
            col = store._collection
            persisted_space: str | None = None
            if hasattr(col, "configuration") and isinstance(col.configuration, dict):
                hnsw_cfg = col.configuration.get("hnsw")
                if isinstance(hnsw_cfg, dict):
                    persisted_space = hnsw_cfg.get("space")
            if not persisted_space and hasattr(col, "metadata") and isinstance(col.metadata, dict):
                persisted_space = col.metadata.get("hnsw:space")

            if persisted_space and str(persisted_space).strip().lower() != "cosine":
                raise ValueError(
                    f"Chroma collection {eff_col!r} exists with distance metric {persisted_space!r}, "
                    "but Bodhan multilingual RAG requires 'cosine' distance for E5 normalized embeddings. "
                    "Chroma does not support changing the distance metric of an existing collection. "
                    "Please configure a new CHROMA_COLLECTION_NAME or delete the existing collection directory "
                    f"({eff_dir!r}) and re-index your documents."
                )

        return store

    if effective_provider == "inmemory":
        return InMemoryVectorStore(embedding=embeddings)

    raise ValueError(
        f"Unsupported VECTOR_STORE_PROVIDER: {effective_provider!r}. "
        "Supported providers are 'chroma' and 'inmemory'."
    )


def index_documents(
    vector_store: VectorStore,
    documents: list[Document],
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    strict: bool = False,
    update_mode: str = "full_document",
) -> list[Document]:
    """Chunk documents with tokenizer awareness and index into vector store with stable IDs and idempotency.

    Update Scopes:
      - 'full_document' (default): Treats the supplied document chunks as the complete current
        version of each document. Upserts all new chunks first, then purges all stale chunks
        from all pages of those source documents, successfully removing old pages that no
        longer exist in the new revision.
      - 'partial_pages': Updates only the specific pages included in the batch. Preserves chunks
        from other pages of the same document, purging stale chunks only from the updated pages.

    Cleanup Safety:
      1. New chunks are chunked, validated, and normalized.
      2. New documents are upserted FIRST into the vector store.
      3. If embedding generation or upsert fails, old documents are completely preserved.
      4. Stale chunks are queried and deleted only after successful upsert.
      5. Any failure during stale chunk deletion raises StaleChunkCleanupError, surfacing
         the diagnostic and uncleaned stale IDs to the caller while preserving the newly
         inserted content.

    Args:
        vector_store: Target LangChain VectorStore (Chroma or InMemoryVectorStore).
        documents: Source Document objects with metadata.
        max_chunk_tokens: Maximum tokens per chunk budget (default 480).
        chunk_token_overlap: Token overlap between adjacent chunks (default 40).
        tokenizer: Optional tokenizer instance.
        chunk_size: Backward-compatibility alias for max_chunk_tokens.
        chunk_overlap: Backward-compatibility alias for chunk_token_overlap.
        strict: Whether to enforce strict tokenizer requirement without fallback.
        update_mode: Update scope ('full_document' or 'partial_pages'). Defaults to 'full_document'.

    Returns:
        List of indexed Document chunks with assigned chunk_id and stable IDs.

    Raises:
        ValueError: If documents list is empty, update_mode is invalid, or document identity cannot be derived.
        StaleChunkCleanupError: If stale chunk deletion fails after successful insertion.
    """
    if not documents:
        raise ValueError("Cannot index empty document list.")

    valid_modes = {"full_document", "partial_pages"}
    mode = (update_mode or "").strip().lower()
    if mode not in valid_modes:
        raise ValueError(
            f"Unsupported update_mode: {update_mode!r}. "
            f"Supported modes are: {sorted(valid_modes)!r}."
        )

    effective_tokens = chunk_size if chunk_size is not None else max_chunk_tokens
    effective_overlap = chunk_overlap if chunk_overlap is not None else chunk_token_overlap

    embeddings = getattr(vector_store, "embeddings", None) or getattr(vector_store, "embedding_function", None)
    effective_tokenizer = tokenizer or getattr(embeddings, "tokenizer", None)
    is_strict = strict or getattr(embeddings, "strict_tokenizer", False)

    if is_strict and effective_tokenizer is None:
        raise RuntimeError(
            "Vector store indexing requires a tokenizer in strict mode. "
            "Character-based approximation is disabled to prevent silent token truncation."
        )

    chunks = split_documents_tokenizer_aware(
        documents=documents,
        max_chunk_tokens=effective_tokens,
        chunk_token_overlap=effective_overlap,
        tokenizer=effective_tokenizer,
        strict=is_strict,
    )

    # 1. Derive stable source identities and generate deterministic chunk IDs
    chunk_ids: list[str] = []
    for chunk in chunks:
        src_id = get_source_identity(chunk.metadata, chunk.page_content)
        chunk.metadata["source_id"] = src_id
        page_val = _extract_page_number(chunk.metadata)
        chunk_idx = chunk.metadata.get("chunk_id", 1)
        cid = generate_chunk_id(
            source=chunk.metadata.get("source"),
            page=page_val,
            chunk_index=chunk_idx,
            content=chunk.page_content,
            source_id=src_id,
        )
        chunk_ids.append(cid)

    # Handle Chroma vs InMemory indexing
    is_chroma = hasattr(vector_store, "_collection")
    if is_chroma:
        # 1. Normalize metadata for Chroma scalar constraints
        for chunk in chunks:
            chunk.metadata = normalize_metadata_for_chroma(chunk.metadata)

        # 2. Upsert new documents FIRST into Chroma
        vector_store.add_documents(chunks, ids=chunk_ids)
        backend_name = "Chroma"
    else:
        # Upsert new documents FIRST into InMemoryVectorStore
        vector_store.add_documents(chunks, ids=chunk_ids)
        backend_name = "InMemoryVectorStore"

    # 3. Query existing records, identify stale IDs according to update_mode, and delete
    source_page_map: dict[str, set[int]] = {}
    source_new_ids: dict[str, set[str]] = {}
    for chunk, cid in zip(chunks, chunk_ids, strict=True):
        sid = str(chunk.metadata.get("source_id", ""))
        if not sid:
            continue
        pg = _extract_page_number(chunk.metadata)
        source_page_map.setdefault(sid, set()).add(pg)
        source_new_ids.setdefault(sid, set()).add(cid)

    all_stale_ids: list[str] = []
    if is_chroma:
        for sid, updated_pages in source_page_map.items():
            new_ids = source_new_ids[sid]
            try:
                existing = vector_store._collection.get(
                    where={"source_id": sid},
                    include=["metadatas"],
                )
                existing_ids = existing.get("ids") or []
                existing_metas = existing.get("metadatas") or []
                for eid, emeta in zip(existing_ids, existing_metas, strict=False):
                    if eid in new_ids:
                        continue
                    if mode == "full_document":
                        # In full_document mode, any old chunk for this source is stale
                        all_stale_ids.append(eid)
                    else:
                        # In partial_pages mode, only stale chunks on updated pages are purged
                        if emeta and isinstance(emeta, dict):
                            epg = _extract_page_number(emeta)
                            if epg in updated_pages:
                                all_stale_ids.append(eid)
                        else:
                            all_stale_ids.append(eid)
            except Exception as exc:
                raise StaleChunkCleanupError(
                    f"Failed to query existing records for source_id {sid!r} during stale chunk cleanup: {exc}",
                    stale_ids=[],
                ) from exc

        if all_stale_ids:
            try:
                vector_store._collection.delete(ids=all_stale_ids)
            except Exception as exc:
                raise StaleChunkCleanupError(
                    f"Failed to delete {len(all_stale_ids)} stale chunk(s) after successful document upsert: {exc}",
                    stale_ids=all_stale_ids,
                ) from exc
    elif hasattr(vector_store, "store") and isinstance(vector_store.store, dict):
        for sid, updated_pages in source_page_map.items():
            new_ids = source_new_ids[sid]
            try:
                for eid, item in list(vector_store.store.items()):
                    meta = _get_item_metadata(item)
                    if meta.get("source_id") != sid:
                        continue
                    if eid in new_ids:
                        continue
                    if mode == "full_document":
                        # In full_document mode, any old chunk for this source is stale
                        all_stale_ids.append(eid)
                    else:
                        # In partial_pages mode, only stale chunks on updated pages are purged
                        epg = _extract_page_number(meta)
                        if epg in updated_pages:
                            all_stale_ids.append(eid)
            except Exception as exc:
                raise StaleChunkCleanupError(
                    f"Failed to inspect existing records for source_id {sid!r} in InMemoryVectorStore: {exc}",
                    stale_ids=[],
                ) from exc

        if all_stale_ids:
            try:
                if hasattr(vector_store, "delete"):
                    vector_store.delete(ids=all_stale_ids)
                else:
                    for sid_to_del in all_stale_ids:
                        vector_store.store.pop(sid_to_del, None)
            except Exception as exc:
                raise StaleChunkCleanupError(
                    f"Failed to delete {len(all_stale_ids)} stale chunk(s) after successful document upsert in InMemoryVectorStore: {exc}",
                    stale_ids=all_stale_ids,
                ) from exc

    print(
        f"  -> Successfully indexed {len(chunks)} passage chunk(s) across "
        f"{len(documents)} document(s) in {backend_name} (mode: {mode})."
    )
    return chunks


def delete_source_documents(
    vector_store: VectorStore,
    source_id: str | None = None,
    source: str | None = None,
) -> int:
    """Safely delete all indexed chunks belonging to a specific source document.

    Scoping Rules:
      - When explicit 'source_id' is supplied:
        Matches strictly on 'source_id'. Never falls back to deleting records matching display 'source'.
        Returns 0 when the supplied identity does not exist. Applies the exact same scoping to Chroma
        and InMemory backends.
      - When only 'source' is supplied:
        Derives canonical identity via get_source_identity(metadata={"source": source}).
        Matches records where metadata['source_id'] == target_sid.
        Falls back to legacy metadata matching only if no records match target_sid, and strictly restricts
        deletion to legacy records that completely lack 'source_id' and match metadata['source'] == source.
        Never deletes unrelated records that share a display filename.

    Args:
        vector_store: Target LangChain VectorStore (Chroma or InMemoryVectorStore).
        source_id: Explicit source identifier.
        source: Source file path or URI (used to derive source_id if source_id is not provided).

    Returns:
        int: Number of deleted chunk records.

    Raises:
        ValueError: If neither source_id nor source is provided.
    """
    explicit_sid = str(source_id).strip() if source_id is not None and str(source_id).strip() else None
    raw_source = str(source).strip() if source is not None and str(source).strip() else None

    if explicit_sid is None and raw_source is None:
        raise ValueError("Either 'source_id' or 'source' must be provided to delete document chunks.")

    if explicit_sid is not None:
        target_sid = explicit_sid
        allow_legacy_fallback = False
    else:
        target_sid = get_source_identity(metadata={"source": raw_source})
        allow_legacy_fallback = True

    is_chroma = hasattr(vector_store, "_collection")
    if is_chroma:
        existing = vector_store._collection.get(
            where={"source_id": target_sid},
            include=["metadatas"],
        )
        ids_to_delete = existing.get("ids") or []

        # Fallback check strictly for legacy records lacking source_id
        if not ids_to_delete and allow_legacy_fallback and raw_source:
            legacy = vector_store._collection.get(
                where={"source": raw_source},
                include=["metadatas"],
            )
            leg_ids = legacy.get("ids") or []
            leg_metas = legacy.get("metadatas") or []
            ids_to_delete = [
                eid for eid, emeta in zip(leg_ids, leg_metas, strict=False)
                if not (emeta and isinstance(emeta, dict) and emeta.get("source_id"))
            ]

        if ids_to_delete:
            vector_store._collection.delete(ids=ids_to_delete)
        return len(ids_to_delete)

    # InMemoryVectorStore support
    if hasattr(vector_store, "store") and isinstance(vector_store.store, dict):
        matched_ids: list[str] = []
        for cid, item in vector_store.store.items():
            meta = _get_item_metadata(item)
            if meta.get("source_id") == target_sid:
                matched_ids.append(cid)

        # Fallback check strictly for legacy records lacking source_id
        if not matched_ids and allow_legacy_fallback and raw_source:
            for cid, item in vector_store.store.items():
                meta = _get_item_metadata(item)
                if not meta.get("source_id") and meta.get("source") == raw_source:
                    matched_ids.append(cid)

        if matched_ids:
            if hasattr(vector_store, "delete"):
                vector_store.delete(ids=matched_ids)
            else:
                for cid in matched_ids:
                    vector_store.store.pop(cid, None)
        return len(matched_ids)

    return 0


def create_vector_store(
    documents: list[Document],
    embeddings: Embeddings,
    provider: str | None = None,
    persist_directory: str = "./.chroma",
    collection_name: str = "bodhan_multilingual_rag",
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
    tokenizer: Any = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    strict: bool = False,
    update_mode: str = "full_document",
    config: RAGConfig | None = None,
) -> VectorStore:
    """Create a vector store and index documents with tokenizer awareness.

    Maintains backwards compatibility for existing callers and test suites while
    supporting ChromaDB local persistence and InMemory backends.
    """
    if not documents:
        raise ValueError("Cannot create vector store from empty document list.")

    effective_provider = provider or (config.vector_store_provider if config else "inmemory")
    store = get_vector_store(
        provider=effective_provider,
        embeddings=embeddings,
        persist_directory=persist_directory,
        collection_name=collection_name,
        config=config,
    )
    index_documents(
        vector_store=store,
        documents=documents,
        max_chunk_tokens=max_chunk_tokens,
        chunk_token_overlap=chunk_token_overlap,
        tokenizer=tokenizer,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strict=strict,
        update_mode=update_mode,
    )
    return store
