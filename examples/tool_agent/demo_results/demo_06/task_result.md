# Task output

### Summary

1. **Document Text Extraction**:
   - The clinic notice (`clinic_notice.png`) was processed using Bodhan Indic-OCR (`ocr_document`), extracting the following text:
     > Community Clinic Notice
     > Open Monday to Friday, 9 AM to 5 PM.
     > Please bring your health card.

2. **Language Support Status**:
   - Translation to French was requested via Bodhan Indic-Translate (`translate_text` with target language `fr`).
   - The service returned an error: `unsupported_language: Unsupported language code 'fr' for Indic-Translate`. Bodhan Indic-Translate is specialized for Indian languages and does not support French.

3. **Outcome**:
   - Because French is unsupported by the Bodhan suite and per instructions not to invent or fabricate translations, no French translation could be generated.
