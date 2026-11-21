# Bodhan AI LangChain Integration

Production-ready LangChain integration for [Bodhan AI](https://bodhan.ai/)'s Indic OCR (`indic-ocr`) and Indic Translation (`indic-translate`) APIs.

This package empowers developers to build multilingual document processing pipelines, agent tools, and multilingual Retrieval-Augmented Generation (RAG) applications across 26 Indic languages and English.

---

## Features

- **`BodhanClient` & `BodhanAsyncClient`**: High-performance HTTP client built on HTTPX with connection pooling, exponential backoff, rate limit handling, automatic context cleanup, and dual-transport fallback for translation (resolving GitHub Issue #34).
- **`BodhanOCRLoader`**: Native LangChain `BaseLoader` supporting images (`.png`, `.jpg`, `.jpeg`, `.webp`) and multi-page PDFs (via PyMuPDF rasterization), base64 data URL formatting, markdown extraction, layout block metadata preservation, and both eager (`load()`) and streaming (`lazy_load()`) loading.
- **`BodhanTranslationTransformer`**: LangChain `BaseDocumentTransformer` for translating documents into 26 Indic languages + English. Features immutable transformation, paragraph-level chunking for long texts, batch concurrency, and comprehensive metadata tracking.
- **`BodhanTranslationTool`**: Pydantic v2-compliant LangChain `BaseTool` enabling conversational agents to dynamically invoke Bodhan Indic Translation.
- **End-to-End Multilingual RAG**: Complete reference example combining Indic OCR, Indic Translation, recursive text splitting, in-memory vector search with ChromaDB, and context retrieval.

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

For running the optional Multilingual RAG example with local vector storage:

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

Extract structured Markdown and layout metadata from an image or multi-page PDF:

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
    print(f"--- Page {doc.metadata['page_number']} ---")
    print(f"Source: {doc.metadata['source']}")
    print(f"Content:\n{doc.page_content[:200]}...\n")
```

Or stream large multi-page documents page-by-page using `lazy_load()`:

```python
for doc in loader.lazy_load():
    process_page(doc)
```

Run the standalone OCR script:

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
    page_content="छात्रवृत्ति के लिए आवेदन 31 दिसंबर तक खुले हैं। सभी छात्र आधिकारिक पोर्टल पर आवेदन कर सकते हैं।",
    metadata={"source": "notice.txt", "category": "education"},
)

# Initialize transformer for English target
transformer = BodhanTranslationTransformer(
    target_language="en",
    max_chunk_chars=1500,
)

# Transform documents
translated_docs = transformer.transform_documents([doc])

print("Original:", doc.page_content)
print("Translated:", translated_docs[0].page_content)
print("Metadata:", translated_docs[0].metadata)
```

Run the standalone translation script:

```bash
python examples/document_translation.py
```

---

### 3. Translation Tool for LangChain Agents

Bind Bodhan Indic Translation directly to an LLM agent as a LangChain tool:

```python
from bodhan_langchain import BodhanTranslationTool

tool = BodhanTranslationTool(target_language="en")

# Inspect schema
print("Tool Name:", tool.name)
print("Tool Description:", tool.description)

# Direct tool execution
result = tool.invoke({
    "text": "விவசாயிகளுக்கு புதிய மானிய திட்டம் அறிவிக்கப்பட்டுள்ளது.",
    "target_language": "en",
})
print("Translation Result:", result)
```

---

### 4. End-to-End Multilingual RAG Pipeline

Run the comprehensive multilingual RAG demonstration script, which processes Indic PDF notices, translates them, indexes them into a vector store, and answers English queries:

```bash
python examples/multilingual_rag.py
```

Workflow:
1. **OCR Ingestion**: Ingests multi-page government and institutional notices in Hindi, Tamil, and Bengali from `sample_data/`.
2. **Translation**: Translates extracted Indic text into normalized English using `BodhanTranslationTransformer`.
3. **Text Splitting**: Splits translated documents into semantic chunks with metadata tracking.
4. **Vector Indexing**: Embeds and indexes chunks into an in-memory ChromaDB vector store.
5. **Retrieval**: Executes similarity searches against English queries (e.g., scholarship deadlines, farmer subsidies) and retrieves cited context.

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

> **Note on Language Codes**: Bodhan requires standard 2-letter or script-qualified ISO codes (e.g., `hi`, `ta`, `bn`, `ks-Deva`). Do not supply country-specific BCP-47 tags (such as Hindi with an India country subtag). The client automatically validates language codes and raises an informative `BodhanValidationError` if an invalid tag is passed.

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
             |  POST /v1/chat/completions|
             |  POST /translate (fallback|
             +---------------------------+
```

### 1. Dual-Transport Translation Strategy (GitHub Issue #34)
GitHub Issue #34 reported that `POST /translate` returned HTTP 404 in certain deployment environments, while the OpenAI-compatible completion route `POST /v1/chat/completions` with `model="indic-translate"` remained functional.

To guarantee zero downtime and maximum resilience:
- `BodhanClient` defaults to `translation_transport="auto"`.
- It attempts the primary `POST /v1/chat/completions` route first.
- If a 404 status code is encountered, it seamlessly falls back to `POST /translate`.
- Developers can also force either transport via `translation_transport="chat_completions"` or `translation_transport="translate"`.

### 2. Immutability in Document Transformation
`BodhanTranslationTransformer` strictly obeys LangChain conventions:
- Original `Document` instances are never mutated in-place.
- A new `Document` is instantiated with the translated text.
- Original metadata is preserved, while enrichment keys (`source_language`, `target_language`, `model_name`, `provider`) are added.

### 3. Layout Block Metadata Preservation
The OCR API returns raw bounding box metadata in `blocks`. `BodhanOCRLoader` extracts this structure and places it in `doc.metadata["blocks"]`, enabling downstream layout analysis and bounding box visualization.

---

## Error Handling

All client errors inherit from `BodhanError`, providing a clean, catchable hierarchy:

```
BodhanError
 ├── BodhanAuthError             (401 Unauthorized / missing API key)
 ├── BodhanRateLimitError        (429 Rate limit exceeded)
 ├── BodhanInvalidRequestError   (400 Bad request / invalid image format)
 ├── BodhanModelNotFoundError    (404 Model or route unavailable)
 ├── BodhanServerError           (500, 502, 503, 504 server errors)
 └── BodhanValidationError       (Local parameter / language code validation failure)
```

Example:

```python
from bodhan_langchain.exceptions import BodhanRateLimitError, BodhanAuthError

try:
    docs = loader.load()
except BodhanAuthError:
    print("Authentication failed: Check your BODHAN_API_KEY.")
except BodhanRateLimitError as e:
    print(f"Rate limited by server: {e}. Retry after backoff.")
```

---

## Testing

The integration includes an automated test suite executed 100% offline using `httpx.MockTransport` (zero paid API calls):

```bash
# Run all LangChain integration tests
pytest tests/test_bodhan_client.py tests/test_ocr_loader.py tests/test_translation.py tests/test_tools.py tests/test_integration.py
```

Run cookbook validator checks:

```bash
python scripts/validate_recipe.py integrations/langchain
python scripts/ci_validate.py --base-ref main
```

---

## Contributing

Contributions to the Bodhan LangChain integration are welcome! Please ensure:
1. All changes adhere to [CONTRIBUTING.md](../../CONTRIBUTING.md).
2. Automated tests pass offline without external network calls.
3. Code complies with `python scripts/validate_recipe.py integrations/langchain`.
