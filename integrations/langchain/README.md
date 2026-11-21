# Bodhan AI LangChain Integration

Production-ready LangChain integration for [Bodhan AI](https://bodhan.ai/)'s Indic OCR (`indic-ocr`) and Indic Translation (`indic-translate`) APIs.

This package empowers developers to build multilingual document processing pipelines, autonomous agent tools, and multilingual Retrieval-Augmented Generation (RAG) applications across 26 Indic languages and English.

---

## Features

- **`BodhanClient` & `BodhanAsyncClient`**: High-performance HTTP client built on HTTPX with connection pooling, automatic exponential backoff, rate limit handling, credential/payload sanitization in error logs, and dual-transport fallback for translation (primary `POST /translate` with automatic fallback on HTTP 404 to `/v1/chat/completions`, resolving GitHub Issue #34).
- **`BodhanOCRLoader`**: Native LangChain `BaseLoader` supporting images (`.png`, `.jpg`, `.jpeg`) and multi-page PDFs (via PyMuPDF rasterization). Performs magic byte validation, base64 data URL formatting, markdown extraction, layout block metadata preservation, and exposes both `page` (0-indexed) and `page_number` (1-indexed) metadata. Explicitly validates image formats and rejects unsupported types (such as WebP) with actionable error messages.
- **`BodhanTranslationTransformer`**: LangChain `BaseDocumentTransformer` for translating documents across 26 Indic languages + English. Features immutable document transformations, safe paragraph- and sentence-boundary chunking strictly bounded by `max_chunk_chars`, bounded async concurrency, and comprehensive metadata tracking.
- **`BodhanTranslationTool`**: Pydantic v2-compliant LangChain `BaseTool` enabling conversational agents to dynamically invoke Bodhan Indic Translation with configurable default target languages.
- **Grounded Multilingual RAG**: Complete reference example demonstrating end-to-end ingestion of authentic Indic PDF notices, translation to English, semantic chunking, vector indexing with `InMemoryVectorStore` (and optional ChromaDB), and grounded companion LLM answers with citation tracking and out-of-domain rejection.

---

## Setup

### Prerequisites

- Python 3.10 or higher
- A Bodhan AI API key from the [Bodhan Console](https://console.bodhan.ai/)

### Installation

Install core dependencies for the Bodhan LangChain integration:

```bash
pip install -r requirements.txt
```

Core dependencies:
- `httpx==0.28.1`
- `langchain-core==1.7.0`
- `pydantic==2.13.4`
- `pymupdf==2.1`

> [!IMPORTANT]
> **PyMuPDF Licensing Notice**: PyMuPDF is licensed under GNU AGPLv3 and also available under a commercial license from Artifex Software, Inc. If you are developing proprietary software and cannot comply with the terms of the AGPLv3, please ensure you acquire an appropriate commercial license from Artifex before deploying PyMuPDF in production.

For running the optional local vector database in the Multilingual RAG example:

```bash
pip install -r requirements-rag-example.txt
```

Optional RAG dependencies:
- `chromadb==0.6.3`
- `langchain==1.3.14`
- `langchain-text-splitters==1.1.3`

### Environment Configuration

Configure your Bodhan API key in your environment or copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Export your API key:

```bash
# Linux / macOS
export BODHAN_API_KEY="your-api-key-here"

# Windows PowerShell
$env:BODHAN_API_KEY="your-api-key-here"
```

---

## Run

### 1. Document OCR (`indic-ocr`)

Extract structured Markdown and layout metadata from an image or multi-page PDF. `BodhanOCRLoader` supports `.png`, `.jpg`, and `.jpeg` images, as well as multi-page `.pdf` files:

```python
from bodhan_langchain import BodhanOCRLoader

# Load a single image or multi-page PDF
loader = BodhanOCRLoader(
    file_path="sample_data/sample_hindi_scholarship.pdf",
    table_format="markdown",
    dpi=150,
)

# Eager load returns a list of LangChain Document objects (one per page)
docs = loader.load()

for doc in docs:
    # Both 0-indexed 'page' and 1-indexed 'page_number' are provided
    print(f"--- Page {doc.metadata['page_number']} (Index: {doc.metadata['page']}) ---")
    print(f"Source: {doc.metadata['source']}")
    print(f"Total Pages: {doc.metadata['total_pages']}")
    print(f"Content:\n{doc.page_content[:200]}...\n")

    # Layout blocks (bounding boxes, confidence, text) are preserved
    if "blocks" in doc.metadata:
        print(f"Detected {len(doc.metadata['blocks'])} layout blocks.")
```

Or stream large multi-page documents page-by-page using `lazy_load()` to minimize memory footprint:

```python
for doc in loader.lazy_load():
    process_page(doc)
```

> [!NOTE]
> **Image Formats**: Bodhan's `indic-ocr` API accepts PNG and JPEG formats. Unsupported image types such as WebP, GIF, or BMP are validated locally before API invocation and will raise an actionable `BodhanValidationError` with conversion instructions.

Run the standalone OCR demonstration script:

```bash
python examples/basic_ocr.py
```

---

### 2. Document Translation (`indic-translate`)

Translate documents while preserving original metadata and creating immutable transformed copies:

```python
from langchain_core.documents import Document
from bodhan_langchain import BodhanTranslationTransformer

# Create sample document in Hindi
doc = Document(
    page_content="छात्रवृत्ति के लिए आवेदन 31 दिसंबर तक खुले हैं। सभी पात्र छात्र आधिकारिक पोर्टल पर आवेदन कर सकते हैं।",
    metadata={"source": "scholarship_notice.pdf", "category": "education"},
)

# Initialize transformer
transformer = BodhanTranslationTransformer(
    target_language="en",
    source_language="hi",          # Optional; API auto-detects if omitted
    max_chunk_chars=1500,          # Splits text safely at paragraph/sentence boundaries
    max_output_tokens=2048,        # API token parameter (max_tokens also accepted)
    max_concurrency=4,             # Max parallel chunk requests
)

# Transform documents immutably
translated_docs = transformer.transform_documents([doc])

print("Original Content:", doc.page_content)
print("Translated Content:", translated_docs[0].page_content)
print("Enriched Metadata:", translated_docs[0].metadata)
# Metadata includes:
#   source_language: 'hi'
#   target_language: 'en'
#   model_name: 'indic-translate'
#   provider: 'bodhan'
#   original_content: 'छात्रवृत्ति...'
```

Run the standalone translation script:

```bash
python examples/document_translation.py
```

---

### 3. Translation Tool for LangChain Agents

Bind Bodhan Indic Translation directly to an LLM agent as a standard LangChain `BaseTool`:

```python
from bodhan_langchain import BodhanTranslationTool

# Initialize tool with an optional default target language
tool = BodhanTranslationTool(target_language="en")

# Inspect tool schema
print("Tool Name:", tool.name)
print("Tool Description:", tool.description)

# Direct tool execution
result = tool.invoke({
    "text": "விவசாயிகளுக்கு புதிய மானிய திட்டம் அறிவிக்கப்பட்டுள்ளது.",
    "target_language": "en",
})
print("Translation Result:", result)
```

Integrating with an agent:

```python
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate

tools = [BodhanTranslationTool(target_language="en")]
# Pass tools to your agent definition...
```

---

### 4. End-to-End Multilingual RAG Pipeline

Run the comprehensive multilingual RAG demonstration script:

```bash
python examples/multilingual_rag.py
```

The demonstration showcases a complete, real-world multilingual knowledge retrieval workflow:
1. **OCR Ingestion**: Ingests multi-page notices in Hindi, Tamil, and Bengali from `sample_data/` using `BodhanOCRLoader`.
2. **Translation**: Translates extracted Indic text into normalized English using `BodhanTranslationTransformer`.
3. **Text Splitting**: Splits translated documents into semantic chunks while maintaining source and page metadata.
4. **Vector Indexing**: Indexes chunks into an `InMemoryVectorStore` (default, zero extra setup) or local persistent `ChromaDB`.
5. **Grounded Question Answering**: The pipeline accepts queries in both English and Indic languages (auto-translating non-English queries to English), performs vector similarity retrieval, and synthesizes grounded answers using `GroundedContextChatModel` with exact document and page citations. If a question is not covered by the indexed context, the model explicitly declines to answer rather than hallucinating.

#### Production Deployment Considerations for RAG

The demonstration script uses `InMemoryVectorStore` and `GroundedContextChatModel` so developers can run and test the complete pipeline immediately without needing external OpenAI or third-party API accounts:

- **Vector Store**: For production environments, swap `InMemoryVectorStore` with a persistent store such as `Chroma` (`pip install chromadb`), `PGVector`, `FAISS`, or `Pinecone`.
- **Embedding Models**: In production, replace `CharacterNgramEmbeddings` with production embedding models such as `OpenAIEmbeddings(model="text-embedding-3-small")`, `HuggingFaceEmbeddings`, or `CohereEmbeddings`.
- **Companion LLMs**: In production, connect your preferred reasoning LLM (such as `ChatOpenAI(model="gpt-4o")` or `ChatAnthropic(model="claude-3-5-sonnet")`). Be mindful of token usage and per-query costs when processing large retrieved contexts.

---

## Supported Languages

`BodhanTranslationTransformer` and `BodhanTranslationTool` support 26 Indic languages plus English:

| Code | Language | Script / Variant |
|---|---|---|
| `as` | Assamese | Eastern Nagari |
| `bn` | Bengali | Eastern Nagari |
| `brx` | Bodo | Devanagari |
| `doi` | Dogri | Devanagari |
| `en` | English | Latin |
| `gu` | Gujarati | Gujarati |
| `hi` | Hindi | Devanagari |
| `kn` | Kannada | Kannada |
| `kok` | Konkani | Devanagari |
| `ks` | Kashmiri | Perso-Arabic |
| `ks-Deva` | Kashmiri | Devanagari |
| `mai` | Maithili | Devanagari |
| `ml` | Malayalam | Malayalam |
| `mni` | Manipuri | Meitei Mayek |
| `mni-Beng` | Manipuri | Bengali |
| `mr` | Marathi | Devanagari |
| `ne` | Nepali | Devanagari |
| `or` | Odia | Odia |
| `pa` | Punjabi | Gurmukhi |
| `sa` | Sanskrit | Devanagari |
| `sat` | Santali | Ol Chiki |
| `sd` | Sindhi | Devanagari |
| `sd-Arab` | Sindhi | Perso-Arabic |
| `ta` | Tamil | Tamil |
| `te` | Telugu | Telugu |
| `ur` | Urdu | Perso-Arabic |

> [!NOTE]
> **Language Codes**: Bodhan requires standard 2-letter or script-qualified ISO codes (e.g., `hi`, `ta`, `bn`, `ks-Deva`). Do not supply country-specific BCP-47 tags (such as `hi-IN` or `ta-IN`). The client validates language codes locally and raises an informative `BodhanValidationError` if an unsupported tag is passed.

---

## Architecture & Design Decisions

```
+-------------------------------------------------------------+
|                      LangChain User App                     |
+-------------------------------------------------------------+
         |                                 |
         v                                 v
+-----------------------+      +------------------------------+
|    BodhanOCRLoader    |      | BodhanTranslationTransformer |
|  - Multi-page PDF     |      |  - Chunking (paragraphs)     |
|  - Image rasterize    |      |  - Metadata preservation     |
|  - Base64 encoding    |      |  - Immutable documents       |
|  - page & page_number |      |  - max_output_tokens         |
+-----------------------+      +------------------------------+
         |                                 |
         +----------------+ +--------------+
                          | |
                          v v
             +---------------------------+
             |       BodhanClient        |
             |   - Auth & Headers        |
             |   - HTTPX connection pool |
             |   - Exponential backoff   |
             |   - Dual-transport route  |
             +---------------------------+
                          |
                          v
             +---------------------------+
             |    Bodhan AI API Cloud    |
             |  1. POST /translate (prim)|
             |  2. POST /v1/chat/... (fb)|
             +---------------------------+
```

### 1. Dual-Transport Translation Strategy (GitHub Issue #34)
GitHub Issue #34 reported that `POST /translate` returned HTTP 404 in certain deployment environments, while the completion route `POST /v1/chat/completions` with `model="indic-translate"` was available.

To guarantee maximum reliability:
- `BodhanClient` defaults to `translation_transport="auto"`.
- It attempts the official documented endpoint `POST /translate` first.
- If a 404 status code is encountered, it automatically falls back to `POST /v1/chat/completions`.
- Non-404 errors (such as 401 Unauthorized, 400 Bad Request, or 429 Rate Limit) fail immediately without falling back.
- Developers can also explicitly select a transport using `translation_transport="translate"` or `translation_transport="chat_completions"`.

### 2. Immutability in Document Transformation
`BodhanTranslationTransformer` strictly obeys LangChain conventions:
- Original `Document` instances are never mutated in-place.
- A new `Document` is instantiated with the translated text.
- Original metadata is preserved, while enrichment keys (`source_language`, `target_language`, `model_name`, `provider`, `original_content`) are recorded.

### 3. Safe Translation Chunking
When input texts exceed `max_chunk_chars`, the transformer performs recursive semantic chunking:
- Splits first at paragraph boundaries (`\n\n`), then line breaks (`\n`), then sentence delimiters (`. `, `। `, `? `, `! `).
- Guaranteed never to exceed `max_chunk_chars` or split words in half.
- Chunk translations are concatenated with appropriate whitespace.

### 4. Layout Block Metadata Preservation
The OCR API returns structured bounding box metadata in `blocks`. `BodhanOCRLoader` extracts this structure and preserves it in `doc.metadata["blocks"]`. Unknown or missing fields are left as `None` rather than fabricating zero coordinates or zero confidence.

---

## Error Handling

All client exceptions inherit from `BodhanError`, providing a clean, catchable hierarchy:

```
BodhanError
 ├── BodhanValidationError       (Client-side parameter validation failure, unsupported format)
 └── BodhanAPIError              (Server error, network failure, or HTTP error status)
      ├── BodhanAuthenticationError   (HTTP 401: Invalid or missing API key)
      └── BodhanRateLimitError        (HTTP 429: Rate limit exceeded, includes retry_after)
```

Example:

```python
from bodhan_langchain.exceptions import (
    BodhanError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    BodhanValidationError,
)

try:
    docs = loader.load()
except BodhanAuthenticationError:
    print("Authentication failed: Check that BODHAN_API_KEY is correctly set.")
except BodhanRateLimitError as e:
    print(f"Rate limited by server: {e}. Back off and retry after {e.retry_after} seconds.")
except BodhanValidationError as e:
    print(f"Invalid input parameter: {e.message} (parameter: {e.parameter})")
except BodhanError as e:
    print(f"Bodhan operation failed: {e}")
```

All exceptions automatically redact sensitive values (such as API keys, Bearer tokens, and large base64 image payloads) to prevent credential leakage in logs and tracebacks.

---

## Testing

The integration includes an automated test suite executed 100% offline using `httpx.MockTransport` (zero paid API calls):

```bash
# Run all LangChain integration tests offline
pytest tests/test_bodhan_client.py tests/test_ocr_loader.py tests/test_translation.py tests/test_tools.py tests/test_integration.py tests/test_live_api_plan.py
```

### Opt-In Live API Tests
Live integration tests that communicate with Bodhan's cloud APIs are strictly opt-in to avoid unwanted credit consumption. They are located in `tests/test_live_api_plan.py` and are skipped by default. To run them against a live account:

```bash
export BODHAN_API_KEY="your-real-key"
export RUN_BODHAN_LIVE_TESTS=1
pytest tests/test_live_api_plan.py
```

### Repository Validation Checks

Verify compliance with cookbook standards:

```bash
python scripts/validate_recipe.py integrations/langchain
python scripts/ci_validate.py --base-ref main
```

---

## Contributing

Contributions to the Bodhan LangChain integration are welcome! Please ensure:
1. All changes adhere to [CONTRIBUTING.md](../../CONTRIBUTING.md).
2. Automated tests pass offline without external network calls.
3. Code complies with `python scripts/validate_recipe.py integrations/langchain` and `python scripts/ci_validate.py --base-ref main`.
