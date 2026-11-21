# Authentic Indic Datasets Catalog & Evaluation Benchmark

This document indexes all original, real-world datasets, curated edge cases, and partitioned random sample suites for the **Bodhan Tool Server and Task Agent** recipe. All datasets are classified across two orthogonal axes:
1. **Operational Tier:** Nominal Passing, Generalization, Failure & Boundary, Nuanced Edge Cases, and Random Multimodal Sampling.
2. **Data Provenance:** **Non-Synthetic (Authentic In-The-Wild)** vs. **Synthetic (Calibrated Parametric Fixtures)**.

---

## 1. Complete Dataset Registry & Direct Links

### 1.1 Flagship Demo Tasks & Baseline Failures

| Resource Key | Local File (`sample_data/`) | Modality & Language | Data Provenance | Authentic Public Source & Direct Link | License / Provenance | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `demo1_pdf` | `demo1_indic_nlp_paper.pdf` | Document (14p PDF)<br>English / Indic | **Non-Synthetic** (Authentic) | [arXiv:2205.03021 (AI4Bharat IndicNLP Paper)](https://arxiv.org/pdf/2205.03021.pdf) | CC-BY 4.0 / Open Access (arXiv) | **Pass** (8-page scan $\to$ Tamil audio summary) |
| `demo2_audio` | `demo2_hindi_spoken_monologue.ogg` | Audio (3.5m OGG)<br>Hindi (`hi`) | **Non-Synthetic** (Authentic) | [Wikimedia Commons: Kavyanjali Speaking Hindi](https://upload.wikimedia.org/wikipedia/commons/f/f2/WIKITONGUES_Kavyanjali_speaking_Hindi.ogg) | CC-BY-SA 4.0 (Wikitongues) | **Pass** (3-min speech $\to$ English SRT) |
| `demo3_image` | `demo3_marathi_devanagari_table.png` | Image (PNG table)<br>Marathi / Devanagari | **Non-Synthetic** (Authentic) | [Wikimedia Commons: UCB Devanagari Table](https://upload.wikimedia.org/wikipedia/commons/4/47/UCB_Devanagari.png) | CC-BY-SA 3.0 (Unicode Consortium) | **Pass** (Worksheet $\to$ Marathi Markdown Table) |
| `demo4_clip1_te` | `demo4_telugu_speech_01.webm` | Audio (WebM speech)<br>Telugu (`te`) | **Non-Synthetic** (Authentic) | [Wikimedia Commons: Manjusha Speaking Telugu](https://upload.wikimedia.org/wikipedia/commons/7/71/WIKITONGUES-_Manjusha_speaking_Telugu.webm) | CC-BY-SA 4.0 (Wikitongues) | **Pass** (Identified as Telugu $\to$ Translated to English) |
| `demo4_clip2_ta` | `demo4_tamil_speech_02.webm` | Audio (WebM speech)<br>Tamil (`ta`) | **Non-Synthetic** (Authentic) | [Wikimedia Commons: Sai Speaking Tamil](https://upload.wikimedia.org/wikipedia/commons/5/59/WIKITONGUES-_Sai_speaking_Tamil.webm) | CC-BY-SA 4.0 (Wikitongues) | **Pass** (Filtered out as non-Telugu) |
| `demo4_clip3_bn` | `demo4_bengali_speech_03.webm` | Audio (WebM speech)<br>Bengali (`bn`) | **Non-Synthetic** (Authentic) | [Wikimedia Commons: Hasive Speaking Bengali](https://upload.wikimedia.org/wikipedia/commons/a/ab/WIKITONGUES-_Hasive_speaking_Bengali.webm) | CC-BY-SA 4.0 (Wikitongues) | **Pass** (Filtered out as non-Telugu) |
| `demo5_text` | `demo5_bengali_gitanjali_verses.txt` | Text (UTF-8 TXT)<br>Bengali (`bn`) | **Non-Synthetic** (Authentic) | [Bengali Wikisource: Gitanjali (Rabindranath Tagore)](https://bn.wikisource.org/wiki/%E0%A6%97%E0%A7%80%E0%A6%A4%E0%A6%BE%E0%A6%9E%E0%A7%8D%E0%A6%9C%E0%A6%B2%E0%A6%BF) | Public Domain (Tagore / Wikisource) | **Pass** (Danda sentence split $\to$ Bengali female speech) |
| `fail_refusal_video` | `fail_unsupported_french_video.webm` | Video (WebM)<br>Hindi speech | **Non-Synthetic** (Authentic) | [Wikimedia Commons: Krishna Speaking Hindi](https://upload.wikimedia.org/wikipedia/commons/8/82/WIKITONGUES-_Krishna_speaking_Hindi.webm) | CC-BY-SA 4.0 (Wikitongues) | **Honest Refusal** (Prompt asks for French translation; agent refuses cleanly) |
| `fail_corrupt_audio` | `fail_corrupted_audio_header.wav` | Audio (WAV, 37 B)<br>Corrupted | **Synthetic** (Adversarial Fixture) | Local byte fixture (`RIFF` header with truncated body) | Internal Adversarial Test | **ToolError** (Self-correction triggered; agent halts gracefully) |
| `fail_empty_file` | `fail_zero_byte_empty.pdf` | Document (0 Bytes)<br>Empty | **Synthetic** (Boundary Fixture) | Empty byte fixture (`0` bytes) | Internal Boundary Test | **Validation Error** (Input rejected before invoking Bodhan API) |

---

### 1.2 Nuanced Edge Cases Suite (`sample_data/edge_cases/`)

All 18 edge case fixtures reside in `sample_data/edge_cases/` with its own comprehensive technical [README.md](edge_cases/README.md). Generated offline via `src.common.edge_cases`:

| Resource Key | Local File (`sample_data/edge_cases/`) | Modality & Type | Data Provenance | Specific Stress Vector & Nuanced Boundary | Size | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `edge_audio_boundary_29s` | `edge_audio_boundary_29s.wav` | Audio (24kHz Mono) | **Synthetic** (Calibrated Waveform) | **Hard Limit Boundary:** 29.2s duration (under 30.0s ASR limit; must NOT split into chunks) | ~1.4 MB | **Pass** (Single chunk) |
| `edge_audio_unbroken_rapid` | `edge_audio_unbroken_rapid_speech.wav` | Audio (24kHz Mono) | **Synthetic** (Calibrated Waveform) | **Silence Absence:** 32.0s duration with continuous speech and NO silence below -35 dBFS | ~1.5 MB | **Pass** (Local energy dip fallback) |
| `edge_audio_clipped_high_gain` | `edge_audio_clipped_high_gain.wav` | Audio (24kHz Mono) | **Synthetic** (Calibrated Waveform) | **Acoustic Saturation:** 8.0s clip with 15% clipped samples ($\pm 32767$) | ~380 KB | **Pass** (Clipping diagnostics triggered) |
| `edge_audio_telephony_8khz` | `edge_audio_telephony_8khz.wav` | Audio (8kHz Mono) | **Synthetic** (Calibrated Waveform) | **Bandwidth & SNR:** Narrowband 8,000 Hz telephony speech with ambient background noise | ~128 KB | **Pass** (Resampling warning logged) |
| `edge_audio_micro_utterance` | `edge_audio_micro_utterance.wav` | Audio (24kHz Mono) | **Synthetic** (Calibrated Waveform) | **Sub-Second Boundary:** 0.85s single syllable burst ("हाँ जी") | ~40 KB | **Pass** (No FFT padding failure) |
| `edge_audio_pure_silence` | `edge_audio_pure_silence.wav` | Audio (24kHz Mono) | **Synthetic** (Zero Energy Waveform) | **Zero Energy:** 5.0s digital silence (0 amplitude, -100 dBFS) | ~240 KB | **Pass** (No hallucination) |
| `edge_audio_synthetic_tone` | `edge_audio_synthetic_tone.wav` | Audio (24kHz Mono) | **Synthetic** (Harmonic Tone) | **Non-Speech Harmonic:** Pure 1000 Hz sinusoidal tone (flatness = 0.0) | ~192 KB | **Pass** (Classified as non-speech) |
| `edge_doc_dual_column_hindi` | `edge_doc_dual_column_hindi.pdf` | Document (1p PDF) | **Synthetic** (Vector PDF Layout) | **Spatial Reading Order:** Dual-column Hindi text separated by 20pt whitespace gutter | ~1.5 MB | **Pass** (Gutter detected; no cross-bleed) |
| `edge_doc_borderless_table` | `edge_doc_borderless_table.pdf` | Document (1p PDF) | **Synthetic** (Vector PDF Layout) | **Tabular Structure:** Multi-column table with NO border lines and mixed Indic/Latin numerals | ~1.5 MB | **Pass** (Columns preserved in Markdown) |
| `edge_doc_mixed_script_bilingual` | `edge_doc_mixed_script_bilingual.pdf` | Document (1p PDF) | **Synthetic** (Vector PDF Layout) | **Multi-Script Routing:** Official circular with English, Hindi, Tamil, and Telugu text | ~1.5 MB | **Pass** (Multi-script OCR routing) |
| `edge_img_low_contrast_skewed` | `edge_img_low_contrast_skewed.png` | Image (PNG) | **Synthetic** (Degraded Scan PNG) | **Visual Degradation:** +3.5° rotation with faint pencil/photocopy contrast | ~58 KB | **Pass** (Diacritics survive binarization) |
| `edge_text_honorifics_abbreviations` | `edge_text_honorifics_abbreviations.txt` | Text (UTF-8) | **Synthetic** (Curated Boundary Text) | **Linguistic Boundary:** Dense abbreviations ("डॉ.", "पं.", "प्रो.", "सं.", dates) | ~900 B | **Pass** (Non-destructive clause split) |
| `edge_text_code_mixed_hinglish` | `edge_text_code_mixed_hinglish.txt` | Text (UTF-8) | **Synthetic** (Curated Colloquial Text) | **Linguistic Mixing:** Colloquial Hinglish blending Latin script and Devanagari | ~320 B | **Pass** (Code-Mixing Index profiled) |
| `edge_text_rare_scripts_conjuncts` | `edge_text_rare_scripts_conjuncts.txt` | Text (UTF-8) | **Synthetic** (Curated Unicode Text) | **Script Diversity:** Kashmiri (Perso-Arabic), Assamese, Ol Chiki, and ZWJ/ZWNJ ligatures | ~1.0 KB | **Pass** (NFC normalization verified) |
| `edge_text_runon_colloquial` | `edge_text_runon_colloquial.txt` | Text (UTF-8) | **Synthetic** (Curated Punctuationless) | **Punctuation Absence:** 600-char continuous monologue with zero punctuation | ~970 B | **Pass** (Word boundary fallback) |
| `edge_fail_prompt_injection` | `edge_fail_prompt_injection.pdf` | Document (1p PDF) | **Synthetic** (Adversarial Security Override) | **Security Quarantine:** Invoice containing adversarial instruction override | ~1.6 KB | **Pass** (Treated as data, not instruction) |
| `edge_fail_corrupted_pdf_eof` | `edge_fail_corrupted_pdf_eof.pdf` | Document (200 B) | **Synthetic** (Truncated Malformed Stream) | **Structural Corruption:** Truncated PDF body missing xref table and `%%EOF` | 155 B | **ToolError** (Early validation rejection) |
| `edge_fail_unsupported_language_pair` | `edge_fail_unsupported_language_pair.txt` | Text (UTF-8) | **Synthetic** (Curated Refusal Trigger) | **Language Policy:** Translation request for French (`fr`), German (`de`), and Japanese (`ja`) | ~280 B | **Honest Refusal** (Zero tokens spent) |

---

### 1.3 Random Multimodal Sampling Suite (`sample_data/{speech,images,documents,text}/`)

All 73 instances are **Synthetic (Stratified Algorithmic Sampling)** generated via `src.common.random_sampler` (`seed=42`) to provide unbiased empirical parameter distributions across the full spectrum of Indian linguistic and acoustic conditions:

| Subfolder | File Instances | Data Provenance | Stratification Dimensions Profiled | Total Size |
| :--- | :--- | :--- | :--- | :--- |
| `sample_data/speech/` | 20 WAV files (`sample_speech_01` to `20`) | **Synthetic** (Parametric Audio) | Durations 3.8s–18.5s, $F_0 = 110\text{–}210\text{ Hz}$, tempo 2.2–5.5 syl/s, pause ratio 20–60%, 8kHz vs 24kHz, clipping, whisper (-36 dBFS), reverberant room acoustics, multi-speaker turns | ~11.3 MB |
| `sample_data/images/` | 16 PNG files (`sample_img_01` to `16`) | **Synthetic** (Rendered Bitmap) | Michelson contrast ($0.40\text{–}1.2$), rotation skew ($-1.5^\circ\text{–}+2.5^\circ$), tax invoices, crop tables, notices, application forms, ration cards, highway signboards, handwritten notes, railway tickets, utility bills, blurred receipts, and calligraphy | ~450 KB |
| `sample_data/documents/` | 12 PDF files (`sample_doc_01` to `12`) | **Synthetic** (PyMuPDF Vector A4) | Single-column prose, dual-column with 20pt gutter, multi-row bordered tables, multilingual education circulars, administrative regulations, land patta revenue deeds, Sanskrit shloka commentaries, gazette circulars, court decrees, and balance sheets | ~17.8 MB |
| `sample_data/text/` | 25 TXT files (`sample_text_01` to `25`) | **Synthetic** (Curated Indic Corpora) | **Complete coverage of all 22 scheduled 8th Schedule languages** (Assamese, Bengali, Bodo, Dogri, Gujarati, Hindi, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri, Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, Urdu) + Hinglish, Tanglish, and legal petitions | ~14 KB |

---

## 2. Synthetic vs. Non-Synthetic Categorization Matrix & Rationale

Both dataset paradigms serve complementary, non-overlapping functions in agentic AI evaluation:

```
                            ┌───────────────────────────────────────────────┐
                            │      Bodhan Multimodal Dataset Suite          │
                            │           (101 Total Assets)                  │
                            └───────────────────────┬───────────────────────┘
                                                    │
                   ┌────────────────────────────────┴────────────────────────────────┐
                   ▼                                                                 ▼
      ┌─────────────────────────┐                                       ┌─────────────────────────┐
      │   NON-SYNTHETIC DATA    │                                       │     SYNTHETIC DATA      │
      │   (8 Authentic Assets)  │                                       │   (93 Calibrated Assets)│
      ├─────────────────────────┤                                       ├─────────────────────────┤
      │ • Wikitongues Monologues│                                       │ • 18 Edge Case Fixtures │
      │ • arXiv Research Paper  │                                       │ • 2 Corrupted Baselines │
      │ • Wikisource Literature │                                       │ • 73 Random Multimodal  │
      │ • Unicode Tables        │                                       │   Sample Instances      │
      └────────────┬────────────┘                                       └────────────┬────────────┘
                   │                                                                 │
                   ▼                                                                 ▼
      • Natural Acoustic Reverberation                                  • Exact Duration Ceilings (≤30.0s)
      • Genuine Field Dialects & Accents                                • Zero-Crossing Fallback Verification
      • Real-World OCR Scanning Artifacts                               • Deterministic Circuit-Breaker Tripping
      • Unconstrained Multi-Turn Generalization                         • Zero Token Waste (< 3 MB Suite)
```

### 2.1 Comparative Analysis Matrix

| Evaluation Dimension | Non-Synthetic Datasets (Authentic In-The-Wild) | Synthetic Datasets (Calibrated Parametric Fixtures) |
| :--- | :--- | :--- |
| **Primary Evaluation Role** | Evaluates **end-to-end model generalization**: genuine phonetic accents, unconstrained natural prosody, real-world document formatting, and authentic literary vocabulary. | Evaluates **deterministic boundary safety**: exact duration cutoffs ($\le 30.0\text{s}$), zero-crossing silence splitting, clipping distortion diagnostics, prompt injection quarantine, and circuit breakers. |
| **Origin & Provenance** | Peer-reviewed publications and public archives: [arXiv:2205.03021](https://arxiv.org/pdf/2205.03021.pdf), Wikitongues / Wikimedia Commons, Unicode Consortium, Bengali Wikisource. | Algorithmically synthesized and curated via Python standard library (`wave`), PyMuPDF (`fitz`), Pillow (`PIL`), and Unicode boundary generators. |
| **Reproducibility & Offline Portability** | Downloaded via authentic HTTP URLs during environment bootstrapping; cached locally on disk. | **100% reproducible offline** on any air-gapped machine with zero network access; deterministic random seed (`seed=42`). |
| **Size & Token Economics** | Highly variable ($1.7\text{ KB}$ text to $31.5\text{ MB}$ WebM video); ingestion of raw uncompressed containers can exhaust token budgets if unchunked. | **Micro-footprint** ($155\text{ B}$ to $1.5\text{ MB}$; entire 18-fixture suite $< 3\text{ MB}$); strictly enforces the Small Data Principle to prevent token waste during LLM task planning. |
| **Caching & Regression Behavior** | Successful model results may be cached; keys derive from canonicalized payloads. | Repeated identical calls can avoid provider requests when a matching cache entry exists; cache hit rate depends on local cache state. |
| **Asset Count & Disk Footprint** | **8 assets** | **108.46 MB** (due to raw full-length video/audio) | **93 assets** | **37.8 MB** (18 edge cases: 8.28 MB; 73 random samples: 29.5 MB; 2 fail baselines: 0.04 MB) |

---

## 3. Token Economics, Size Optimization & Cache Behavior

### 3.1 The Small Data Principle
Large media files (e.g., 30 MB raw video clips or 15-page academic scans) rapidly exhaust token windows and blow rupee budgets during multi-agent iterative cycles. Both the **Nuanced Edge Cases Suite** and the **Random Multimodal Sampling Suite** adhere strictly to the Small Data Principle:
- **Audio:** Sliced or synthesized to the minimal duration required to exhibit the target acoustic phenomenon (0.8s to 32s).
- **Documents:** 1-page high-density PDFs (< 100 KB text/layout payload) providing full spatial and tabular complexity without multi-page token overhead.
- **Images:** Web-optimized PNGs (< 80 KB per image) preserving sub-pixel font features and high contrast.
- **Text:** Under 2 KB per text fixture, containing concentrated linguistic phenomena (honorifics, nuktas, transliteration).

### 3.2 Cache-Backed Regression
The Bodhan MCP Server features an integrated zero-dependency SQLite Write-Ahead Logging cache (`src/bodhan_mcp/cache.py`):
1. **Deterministic Keying:** Tool inputs are hashed using SHA-256 over normalized text (`unicodedata.normalize("NFC", ...)`) and binary file streams.
2. **Side-Effect Restoration:** For tools with disk side effects (`synthesize_speech`), the cache restores both the result metadata and the binary audio bytes on disk.
3. **Provider usage:** A cache hit avoids the Bodhan request for that call; misses still invoke the provider. The agent's rupee ledger currently applies nominal Bodhan rates without observing cache-hit state, so its estimate may count cached calls that incurred no provider request. Check provider usage records for actual charges.

### 3.3 Inferential Population Bounds under Central Limit Theorem (CLT)
From the empirical random sample distributions:
- **Speech Duration:** $n=10$, $\bar{x} = 11.60\text{ s} \pm 2.76\text{ s}$ ($95\%\text{ CI}: [8.84\text{ s}, 14.36\text{ s}]$).
- **Image Contrast:** $n=8$, $\bar{x} = 0.75 \pm 0.16$ ($95\%\text{ CI}: [0.58, 0.91]$).
- **Text Diversity (TTR):** $n=10$, $\bar{x} = 0.55 \pm 0.13$ ($95\%\text{ CI}: [0.42, 0.68]$).
- **CLT Workload Projection ($N = 1,000$ queries):**
  $$\text{Uncached Total Cost} = ₹405.00 \pm ₹7.44 \quad (\text{vs. SQLite Cache Total} = ₹8.10)$$
  These are illustrative rate-based projections under the assumed request mix and cache-hit rate, not observed provider charges, an invoice, or a validated deployment forecast. The projected savings apply only if those assumptions hold.

---

## 4. How to Fetch and Generate Datasets

```bash
# Fetch authentic public demo datasets and generate all local edge cases & random samples:
python -m src.common.download --dest-dir sample_data

# Generate only the random multimodal sampling suite:
python -c "from pathlib import Path; from src.common.random_sampler import generate_random_multimodal_sample; generate_random_multimodal_sample(Path('sample_data'))"

# Verify catalog integrity offline:
pytest tests/test_download.py -v
```
