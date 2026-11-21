# Bodhan AI LangChain Integration

Production-ready LangChain integration for [Bodhan AI](https://bodhan.ai/)'s Indic OCR (`indic-ocr`) and Indic Translation (`indic-translate`) APIs.

This package empowers developers to build multilingual document processing pipelines, autonomous agent tools, and multilingual Retrieval-Augmented Generation (RAG) applications across 22 Indian languages and English across 26 language codes.

---

## Features

- **`BodhanClient` & `BodhanAsyncClient`**: High-performance HTTP client built on HTTPX with connection pooling, rate limit detection and `retry_after` surfacing, credential and payload sanitization in error logs and exception bodies, and dual-transport fallback for translation (primary `POST /translate` with automatic fallback on HTTP 404 to `/v1/chat/completions`, resolving GitHub Issue #34).
- **`BodhanOCRLoader`**: Native LangChain `BaseLoader` supporting images (`.png`, `.jpg`, `.jpeg`) and multi-page PDFs (via optional PyMuPDF rasterization). Performs magic byte signature validation, base64 data URL formatting, markdown extraction, layout block metadata preservation (including `order`, `label`, `type`, `bbox_xyxy` float coordinates, and `conf`), and exposes both `page` (1-indexed) and `page_number` (1-indexed) metadata. Explicitly validates image formats and rejects unsupported types (such as WebP, GIF, SVG) with actionable error messages.
- **`BodhanTranslationTransformer`**: LangChain `BaseDocumentTransformer` for translating documents across 22 Indian languages plus English (26 language codes). Features immutable document transformations, safe chunking preserving paragraph, sentence, and word boundaries with character fallback for unbroken tokens strictly bounded by `max_chunk_chars`, bounded async concurrency, and user-declared metadata provenance tracking.
- **`BodhanTranslationTool`**: Pydantic v2-compliant LangChain `BaseTool` enabling conversational agents to dynamically invoke Bodhan Indic Translation with configurable target languages and script validation (omitting script automatically for English).
- **Grounded Multilingual RAG**: Complete reference example demonstrating end-to-end ingestion of authentic Indic PDF notices, translation to English, semantic chunking, vector indexing with `InMemoryVectorStore` (and optional ChromaDB), and grounded companion LLM answers with citation tracking and out-of-domain rejection.

---

## Setup

### Prerequisites

- Python 3.10 or higher for the core integration (Python 3.11+ required for optional PDF processing with PyMuPDF 2.1)
- A Bodhan AI API key from the [Bodhan Console](https://console.bodhan.ai/)

### Installation

Install core dependencies for the Bodhan LangChain integration (image OCR and translation):

```bash
pip install -r requirements.txt
```

Core dependencies:
- `httpx==0.28.1`
- `langchain-core==1.7.0`
- `pydantic==2.13.4`

For multi-page PDF document OCR processing (requires Python 3.11+):

```bash
pip install -r requirements-pdf.txt
```

PDF dependencies:
- `pymupdf==2.1`

> [!IMPORTANT]
> **PyMuPDF Licensing Notice**: PyMuPDF is licensed under GNU AGPLv3 and also available under a commercial license from Artifex Software, Inc. If you are developing proprietary software and cannot comply with the terms of the AGPLv3, please ensure you acquire an appropriate commercial license from Artifex before deploying PyMuPDF in production. Core image OCR and translation do not require PyMuPDF.

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

Extract structured Markdown and layout metadata from an image or multi-page PDF. `BodhanOCRLoader` supports `.png`, `.jpg`, and `.jpeg` images, as well as multi-page `.pdf` files (when `pymupdf` is installed):

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
    # Both 'page' and 'page_number' metadata are provided
    print(f"--- Page {doc.metadata['page_number']} ---")
    print(f"Source: {doc.metadata['source']}")
    print(f"Total Pages: {doc.metadata['total_pages']}")
    print(f"Content:\n{doc.page_content[:200]}...\n")

    # Layout blocks (order, label, type, bounding boxes, confidence, text) are preserved
    if "blocks" in doc.metadata:
        print(f"Detected {len(doc.metadata['blocks'])} layout blocks.")
```

Or stream large multi-page documents page-by-page using `lazy_load()` to minimize memory footprint:

```python
for doc in loader.lazy_load():
    process_page(doc)
```

> [!NOTE]
> **Image Formats**: Bodhan's `indic-ocr` API accepts PNG and JPEG formats. Unsupported image types such as WebP, GIF, or SVG are validated locally before API invocation and will raise an actionable `BodhanValidationError` with conversion instructions.

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
    source_language="hi",          # Optional: user-declared metadata provenance (API auto-infers source language)
    preserve_original_content=True, # Stores original text under metadata['original_content']
    max_chunk_chars=1500,          # Splits text safely at paragraph/sentence/word boundaries
    max_output_tokens=2048,        # API token parameter (max_tokens also accepted as client alias)
    max_concurrency=4,             # Max parallel chunk requests
)

# Transform documents immutably
translated_docs = transformer.transform_documents([doc])

print("Original Content:", doc.page_content)
print("Translated Content:", translated_docs[0].page_content)
print("Enriched Metadata:", translated_docs[0].metadata)
# Metadata includes:
#   source_language: 'hi'
#   source_language_origin: 'user_declared'
#   target_language: 'en'
#   translation_model: 'indic-translate'
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

# Direct tool execution (script is omitted automatically when target_language='en')
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

`BodhanTranslationTransformer` and `BodhanTranslationTool` support 22 Indian languages plus English across 26 target-language codes (with distinct script variants for Kashmiri, Manipuri, and Sindhi):

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
> **Language Codes & Script Constraints**:
> - **Language Codes**: Bodhan requires standard 2-letter or script-qualified ISO codes (e.g., `hi`, `ta`, `bn`, `ks-Deva`). Do not supply country-specific BCP-47 tags (such as `hi-IN` or `ta-IN`).
> - **Speech-to-Text Only**: The codes `bhb` (Bhili) and `bho` (Bhojpuri) belong to Bodhan's speech-to-text service and are **not** supported for translation.
> - **Script Selection**: Allowed script options are `native` (default for Indic targets), `roman`, and `codemix`.
> - **English Target**: Script selection does not apply when `target_language="en"`. The client omits `script` on English requests automatically; explicitly passing `roman` or `codemix` raises a `BodhanValidationError`.
> - **Codemix Restrictions**: The `codemix` script is not supported for `en`, `ks-Deva`, or `sd-Arab`.
> - **Source Language**: The Bodhan Translation API automatically infers the input source language and does not accept a `source_language` request parameter. Any `source_language` provided to `BodhanTranslationTransformer` is stored purely as user-declared metadata (`source_language_origin="user_declared"`).

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
|  - Multi-page PDF     |      |  - Chunking (boundary safe)  |
|  - Magic byte verify  |      |  - User-declared metadata    |
|  - Base64 data URLs   |      |  - Immutable documents       |
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
             |   - Rate limit detection  |
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
- `BodhanClient` constructor defaults to `transport_strategy="auto"`. Callers may also override per-request transport using `client.translate(..., transport=...)`.
- Allowed transport strategies: `Literal["auto", "chat_completions", "translate_endpoint"]`.
- Under `auto`, the client attempts the official primary endpoint `POST /translate` first.
- **Strict 404 Fallback**: If and only if an HTTP 404 status code is encountered, the client automatically falls back to `POST /v1/chat/completions`.
- Non-404 errors (such as 400 Bad Request, 401 Unauthorized, 429 Rate Limit, or 5xx server errors) fail immediately without falling back to prevent masking real issues.

### 2. Immutability & Metadata Provenance in Document Transformation
`BodhanTranslationTransformer` strictly obeys LangChain conventions:
- Original `Document` instances are never mutated in-place; a new `Document` is instantiated with the translated text.
- Original metadata is preserved.
- Enrichment keys added to transformed documents:
  - `target_language`: The translation target language code.
  - `translation_model`: Name of the translation model (`indic-translate`).
  - `provider`: Fixed identifier (`bodhan`).
  - `source_language`: Stored when provided by the user.
  - `source_language_origin`: Marked explicitly as `"user_declared"` (the official API does not guarantee detected-language metadata).
  - `original_content`: Contains the original untranslated text only when `preserve_original_content=True` is enabled.

### 3. Safe Translation Chunking
When input texts exceed `max_chunk_chars`, the transformer performs recursive semantic chunking:
- Splits progressively at paragraph boundaries (`\n\n`), line breaks (`\n`), sentence delimiters (`. `, `। `, `? `, `! `), and words (` `).
- If an unbroken token or sentence exceeds `max_chunk_chars`, a character-level slice fallback is applied to guarantee that no chunk ever exceeds `max_chunk_chars`.
- Empty and whitespace-only chunks are discarded.
- Translated chunks are stitched back together in strict reading order.

### 4. Layout Block Metadata Preservation & Image Validation
- **Image Validation**: `BodhanOCRLoader` validates image signatures before HTTP dispatch, supporting PNG and JPEG. Unsupported formats (WebP, GIF, SVG) are rejected early with clear error messages.
- **Page Metadata**: Documents expose both `page` (1-indexed) and `page_number` (1-indexed) metadata for compatibility across LangChain ecosystem tools.
- **Layout Block Preservation**: The OCR API returns structured bounding box metadata in `blocks`. `BodhanOCRLoader` preserves this in `doc.metadata["blocks"]`, exposing `order` (int), `label` (str), `type` (str | None), `bbox_xyxy` (exact 4-element float list `[x1, y1, x2, y2]`), `conf` (float `0.0`–`1.0`), and `text` (str). Missing or malformed coordinates and confidence values are preserved as `None` rather than fabricating zero coordinates or confidence scores.

---

## Error Handling

All client exceptions inherit from `BodhanError`, providing a clean, catchable hierarchy:

```
BodhanError
 ├── BodhanValidationError       (Client-side parameter validation failure, unsupported format)
 └── BodhanAPIError              (Server error, network failure, or HTTP error status)
      ├── BodhanAuthenticationError   (HTTP 401: Invalid or missing API key)
      └── BodhanRateLimitError        (HTTP 429: Rate limit exceeded, exposes retry_after)
```

### Rate Limiting Diagnostics
`BodhanRateLimitError` parses HTTP headers and exposes:
- `retry_after`: Suggested wait time in seconds (integer or `None`).
- `limit_requests`: Request quota limit from headers if provided.
- `limit_parallel_requests`: Concurrency quota limit from headers if provided.

> [!NOTE]
> The client does **not** execute automatic background retry loops. Applications should inspect `error.retry_after` and implement their own backoff, retry queue, or throttling strategy suitable for their concurrency architecture.

### Payload & Credential Sanitization
All exceptions and error messages automatically redact sensitive information:
- API keys and Bearer tokens are redacted as `[REDACTED]`.
- Large base64 data URLs and image payloads are replaced with safe summaries (`<base64-image-data redacted>`).
- Server response bodies attached to `BodhanAPIError.response_body` are bounded to prevent memory bloating and sanitized to remove credentials or tokens.

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
    wait_time = e.retry_after if e.retry_after is not None else 5
    print(f"Rate limited by server: {e}. Back off and retry after {wait_time}s.")
except BodhanValidationError as e:
    print(f"Invalid input parameter: {e.message} (parameter: {e.parameter})")
except BodhanError as e:
    print(f"Bodhan operation failed: {e}")
```

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
