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
- **Font Licensing & Subsetting**: Rendered using genuinely redistributable open-source fonts licensed under the **SIL Open Font License, Version 1.1 (OFL-1.1)**:
  - Hindi (`sample_hindi_scholarship.pdf`): **Noto Sans Devanagari** (Copyright The Noto Project Authors, https://github.com/notofonts/devanagari).
  - Tamil (`sample_tamil_agriculture.pdf`): **Noto Sans Tamil** (Copyright The Noto Project Authors, https://github.com/notofonts/tamil).
  - Bengali (`sample_bengali_health.pdf`): **Noto Sans Bengali** (Copyright The Noto Project Authors, https://github.com/notofonts/bengali).
  Only the exact required font glyph subsets are embedded within the PDF streams, keeping file sizes practical (~50–170 KB) for cookbook distribution while eliminating any proprietary font bundling. The full SIL OFL 1.1 license text is provided in [`OFL.txt`](./OFL.txt).
- **OCR Testing Note**: Local PDF text extraction is utilized for fast offline regression tests. In production, `BodhanOCRLoader` rasterizes each page into high-resolution PNG image bytes before dispatching to Bodhan AI's cloud-hosted `indic-ocr` model.
