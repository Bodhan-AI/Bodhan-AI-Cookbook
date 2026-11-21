"""End-to-End Multilingual Retrieval-Augmented Generation (RAG) with Bodhan AI and Groq.

Logical Architecture:
    Indic-language Documents (Hindi, Tamil, Bengali)
            |
            v
    Bodhan OCR + Translation (BodhanOCRLoader + BodhanTranslationTransformer)
            |
            v
    Multilingual Embeddings (HuggingFace sentence-transformers / paraphrase-multilingual-MiniLM-L12-v2)
            |
            v
    LangChain InMemoryVectorStore
            |
            v
    LangChain Retriever (Top-K)
            |
            v
    Groq ChatGroq (Grounded Answer Generation via langchain-groq)
            |
            v
    Answer + Application-Validated Source Citations ([S1], [S2] -> Page/Source)
            |
    (Optional)
            v
    Bodhan Translation (Localize answer back to user's native Indic language)

Usage:
    python integrations/langchain/examples/multilingual_rag.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure package root is in sys.path
PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bodhan_langchain import (
    BodhanClient,
    BodhanTranslationTool,
)
from examples.rag.citations import (
    extract_and_validate_citations,
    format_context_with_citations,
)
from examples.rag.config import RAGConfig
from examples.rag.embeddings import DeterministicFakeEmbeddings, get_embedding_model
from examples.rag.generation import generate_grounded_answer, get_chat_model
from examples.rag.ingestion import ingest_documents
from examples.rag.retrieval import get_retriever, retrieve_passages
from examples.rag.vector_store import create_vector_store


class MockTestGroundedChatModel(BaseChatModel):
    """Test-only mock chat model used when running offline integration tests."""

    @property
    def _llm_type(self) -> str:
        return "mock_test_grounded_chat_model"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt_text = messages[-1].content if messages else ""
        p_lower = prompt_text.lower()

        if "bullet train" in p_lower:
            answer = (
                "Insufficient context: The retrieved documents do not contain enough "
                "supporting information to answer this question."
            )
        else:
            answer = (
                "Eligible students receive scholarship assistance according to official guidelines [S1]. "
                "[Citation: sample_hindi_scholarship.pdf — Page 1]"
            )

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


def answer_multilingual_query(
    query: str,
    query_lang: str,
    retriever: Any,
    chat_model: BaseChatModel,
    translation_tool: Optional[BodhanTranslationTool] = None,
    translate_answer_back: bool = True,
    target_answer_lang: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute end-to-end question answering for a single user query."""
    clean_query = query.strip()
    is_indic = query_lang.lower() not in ("en", "english")

    # Step A: Translate Indic query to English if needed
    if is_indic and translation_tool is not None:
        try:
            english_query = translation_tool.invoke({
                "text": clean_query,
                "target_language": "en",
                "source_language": query_lang,
            })
            english_query = str(english_query).strip()
        except Exception as exc:
            print(f"  [WARNING] Query translation failed: {exc}. Using raw query.")
            english_query = clean_query
    else:
        english_query = clean_query

    # Step B: Retrieve relevant passages
    retrieved_docs = retrieve_passages(retriever, english_query)

    # Step C: Format context with stable citations [S1], [S2]
    context_str, citation_map = format_context_with_citations(retrieved_docs)

    # Step D: Invoke ChatGroq for grounded answer generation
    english_answer = generate_grounded_answer(chat_model, english_query, context_str)

    # Step E: Application-validated citation extraction
    valid_citations, invalid_citations = extract_and_validate_citations(english_answer, citation_map)

    # Step F: Localize answer back to Indic query language if requested
    desired_lang = target_answer_lang or (query_lang if is_indic else None)
    localized_answer = None

    if translate_answer_back and desired_lang and desired_lang.lower() != "en" and translation_tool is not None:
        if "not contain enough information" not in english_answer.lower():
            try:
                localized_answer = translation_tool.invoke({
                    "text": english_answer,
                    "target_language": desired_lang,
                })
                localized_answer = str(localized_answer).strip()
            except Exception as exc:
                print(f"  [WARNING] Answer translation back to '{desired_lang}' failed: {exc}")

    return {
        "original_query": clean_query,
        "query_language": query_lang,
        "english_query": english_query,
        "retrieved_count": len(retrieved_docs),
        "raw_answer": english_answer,
        "citations": valid_citations,
        "invalid_citations": invalid_citations,
        "localized_answer": localized_answer,
        "citation_map": citation_map,
    }


def run_rag_pipeline(
    config: Optional[RAGConfig] = None,
    ocr_client: Optional[BodhanClient] = None,
    translation_client: Optional[BodhanClient] = None,
    chat_model: Optional[BaseChatModel] = None,
    custom_query: Optional[str] = None,
    custom_query_lang: Optional[str] = None,
    custom_answer_lang: Optional[str] = None,
    sample_dir: Optional[Path] = None,
    mock_client: Optional[BodhanClient] = None,
) -> List[Dict[str, Any]]:
    """Execute the complete Bodhan x Groq multilingual RAG pipeline."""
    cfg = config or RAGConfig.from_env()

    # Handle backward-compatible mock_client parameter for integration testing
    if mock_client is not None:
        ocr_client = ocr_client or mock_client
        translation_client = translation_client or mock_client
        if chat_model is None:
            chat_model = MockTestGroundedChatModel()
        cfg.embedding_provider = "fake"

    print("=" * 80)
    print("  BODHAN AI x GROQ: GROUNDED MULTILINGUAL RAG PIPELINE")
    print("=" * 80)

    # 1. Credential Check (for live mode without injected mocks)
    if ocr_client is None or translation_client is None or chat_model is None:
        missing = cfg.validate_for_live()
        if missing:
            print("\n[CONFIGURATION REQUIRED]")
            print(f"Missing required environment variables for live RAG execution: {', '.join(missing)}")
            print("\nPlease configure credentials before executing billable requests:")
            print('  export BODHAN_API_KEY="sk-..."           # (Or BODHAN_OCR_API_KEY & BODHAN_TRANSLATION_API_KEY)')
            print('  export GROQ_API_KEY="gsk_..."')
            print(f'  export GROQ_MODEL="{cfg.groq_model}"')
            print(f'  export EMBEDDING_PROVIDER="{cfg.embedding_provider}"')
            print(f'  export EMBEDDING_MODEL="{cfg.embedding_model}"')
            return []

    # 2. Client Initialization
    effective_ocr_client = ocr_client or BodhanClient(api_key=cfg.effective_ocr_key)
    effective_trans_client = translation_client or BodhanClient(api_key=cfg.effective_translation_key)

    # 3. Embedding Provider Initialization
    print(f"\n[Step 1] Initializing multilingual embedding model ({cfg.embedding_provider})...")
    embeddings = get_embedding_model(cfg.embedding_provider, cfg.embedding_model)

    # 4. Ingestion & Translation
    data_dir = sample_dir or (PKG_DIR / "sample_data")
    print(f"\n[Step 2] Ingesting and Translating notices from '{data_dir.name}'...")
    translated_documents = ingest_documents(
        sample_dir=data_dir,
        ocr_client=effective_ocr_client,
        translation_client=effective_trans_client,
        target_language="en",
    )

    # 5. Vector Store & Indexing
    print("\n[Step 3] Splitting and Indexing into LangChain InMemoryVectorStore...")
    vector_store = create_vector_store(
        documents=translated_documents,
        embeddings=embeddings,
        chunk_size=cfg.chunk_size,
        chunk_overlap=cfg.chunk_overlap,
    )
    print("  -> Vector indexing complete.")
    retriever = get_retriever(vector_store, top_k=cfg.top_k)

    # 6. Groq Model Initialization
    print(f"\n[Step 4] Initializing companion LLM (Groq: {cfg.groq_model})...")
    effective_chat_model = get_chat_model(cfg, mock_chat_model=chat_model)
    translation_tool = BodhanTranslationTool(client=effective_trans_client)

    # 7. Query Execution
    test_suite = [
        {
            "query": "छात्रवृत्ति योजना के लिए पात्रता और चयन मानदंड क्या हैं?",
            "lang": "hi",
            "desc": "Hindi question regarding scholarship eligibility criteria.",
        },
        {
            "query": "விவசாயிகள் பயிர் காப்பீட்டு திட்டத்தில் மானியம் மற்றும் சலுகைகள் என்ன?",
            "lang": "ta",
            "desc": "Tamil question regarding crop insurance subsidies and benefits.",
        },
        {
            "query": "গ্রামীণ স্বাস্থ্য ক্লিনিকের সময়সূচী এবং পরিষেবাগুলি কি কি?",
            "lang": "bn",
            "desc": "Bengali question regarding rural health clinic hours and services.",
        },
        {
            "query": "What are the scholarship application deadlines and income caps?",
            "lang": "en",
            "desc": "English direct query regarding scholarship deadlines.",
        },
        {
            "query": "What is the capital expenditure budget for bullet train expansion in 2028?",
            "lang": "en",
            "desc": "Irrelevant / unanswerable query to test hallucination prevention.",
        },
    ]

    if custom_query:
        queries_to_run = [{
            "query": custom_query,
            "lang": custom_query_lang or cfg.query_language,
            "desc": "User-supplied query via CLI.",
        }]
    else:
        queries_to_run = test_suite

    print(f"\n[Step 5] Executing End-to-End Multilingual Question Answering ({len(queries_to_run)} queries)...")
    results: List[Dict[str, Any]] = []

    for idx, item in enumerate(queries_to_run, start=1):
        q = item["query"]
        qlang = item["lang"]
        desc = item["desc"]

        print("\n" + "-" * 75)
        print(f"  Query {idx} [{qlang.upper()}]: \"{q}\"")
        print(f"  Context: {desc}")

        res = answer_multilingual_query(
            query=q,
            query_lang=qlang,
            retriever=retriever,
            chat_model=effective_chat_model,
            translation_tool=translation_tool,
            translate_answer_back=cfg.translate_answer_back,
            target_answer_lang=custom_answer_lang or cfg.answer_language,
        )
        results.append(res)

        if res["english_query"] != q:
            print(f"  -> Translated Query [EN]: \"{res['english_query']}\"")
        print(f"  -> Retrieved Supporting Chunks: {res['retrieved_count']}")
        print(f"\n  Generated Answer:\n  {res['raw_answer']}")

        if res["citations"]:
            print("\n  [Application-Validated Sources]:")
            for cit in res["citations"]:
                print(f"   * [Citation: {cit}]")
        else:
            print("\n  [Application-Validated Sources]: None cited or ungrounded.")

        if res["localized_answer"]:
            print(f"\n  [Localized Answer ({qlang.upper()})]:\n  {res['localized_answer']}")

    print("\n" + "=" * 80)
    print("  MULTILINGUAL RAG PIPELINE DEMONSTRATION COMPLETE")
    print("=" * 80)
    return results


# Backward compatibility alias for existing test suites
run_multilingual_rag_pipeline = run_rag_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--query", "-q", help="Custom user question to ask.")
    parser.add_argument("--query-lang", "-ql", default="hi", help="Language code of custom question (e.g. 'hi', 'ta', 'bn', 'en').")
    parser.add_argument("--answer-lang", "-al", help="Language code for final answer (defaults to query language).")
    parser.add_argument("--embeddings", default="huggingface", help="Embedding provider ('huggingface', 'openai', 'fake').")
    parser.add_argument("--embedding-model", help="Embedding model name.")
    parser.add_argument("--model", "-m", default="openai/gpt-oss-120b", help="Groq model identifier.")
    parser.add_argument("--top-k", "-k", type=int, default=4, help="Number of chunks to retrieve.")
    parser.add_argument("--sample-dir", help="Directory containing sample PDF documents.")
    args = parser.parse_args()

    config = RAGConfig.from_env()
    if args.embeddings:
        config.embedding_provider = args.embeddings
    if args.embedding_model:
        config.embedding_model = args.embedding_model
    if args.model:
        config.groq_model = args.model
    if args.top_k:
        config.top_k = args.top_k
    if args.query_lang:
        config.query_language = args.query_lang
    if args.answer_lang:
        config.answer_language = args.answer_lang

    s_dir = Path(args.sample_dir) if args.sample_dir else None
    run_rag_pipeline(
        config=config,
        custom_query=args.query,
        custom_query_lang=args.query_lang,
        custom_answer_lang=args.answer_lang,
        sample_dir=s_dir,
    )


if __name__ == "__main__":
    main()
