# Document OCR

Extract Markdown text and layout blocks with bounding boxes from a page image in any Indian script.

| | |
|---|---|
| Model | `indic-ocr` |
| Endpoint | `POST /v1/chat/completions` |
| Notebook | [document_ocr.ipynb](document_ocr.ipynb) |

## Setup

```bash
pip install -r requirements.txt
export BODHAN_API_KEY=<your key from https://console.bodhan.ai>
```

## Run

```bash
jupyter notebook document_ocr.ipynb
```

Reference: [console.bodhan.ai/api-docs](https://console.bodhan.ai/api-docs/)
