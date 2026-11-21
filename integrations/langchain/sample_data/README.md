# Synthetic Indic Sample Documents

This directory contains synthetic public notices in Indian languages used for demonstrating and testing the Bodhan LangChain integration (OCR, Translation, and Multilingual RAG).

## Documents

| File | Language | Script | Content Description |
|---|---|---|---|
| `sample_hindi_scholarship.pdf` | Hindi (`hi`) | Devanagari | Synthetic circular describing an educational merit scholarship scheme, eligibility, amounts, and deadlines. |
| `sample_tamil_agriculture.pdf` | Tamil (`ta`) | Tamil | Synthetic farmer welfare notice describing crop insurance subsidies and registration deadlines. |
| `sample_bengali_health.pdf` | Bengali (`bn`) | Eastern Nagari | Synthetic public health circular detailing rural medical clinic timings, services, and helpline numbers. |

## Provenance & Verification

- **Script Authenticity**: Each document contains genuine, correctly rendered Indic script (verified via visual pixmap rasterization and text stream extraction).
- **Synthetic Content**: All notices are synthetically created for demonstration purposes. They contain no personally identifiable information (PII), proprietary government records, or copyrighted material.
- **Font Subsetting**: Generated using Microsoft Nirmala UI font subsets embedded directly in the PDF streams, ensuring consistent visual rendering across Linux, macOS, and Windows.
- **OCR Testing Note**: Local PDF text extraction is utilized for fast offline regression tests. In production, `BodhanOCRLoader` rasterizes each page into high-resolution PNG image bytes before dispatching to Bodhan AI's cloud-hosted `indic-ocr` model.
