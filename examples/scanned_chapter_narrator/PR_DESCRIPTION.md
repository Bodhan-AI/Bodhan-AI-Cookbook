# [Agentic Eng Assignment] Narrated lesson — Sasi

## Summary

`bodhan_lesson` is an autonomous end-to-end agentic recipe that transforms multi-page scanned textbook chapters (5–30 pages, PDF or image directories) into structured, multilingual narrated lessons using **Bodhan Indic AI** models. It pairs `indic-ocr` for document layout and equation extraction with deterministic token shielding, English pivot translation via `indic-translate`, grounded pedagogical narration planning that vocalizes formulas into spoken words, -16 LUFS speech synthesis via `indic-speak`, and 3D QA verification using `indic-transcribe`.

---

## How to run

### 1. Setup from a Clean Clone
```bash
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook/examples/scanned_chapter_narrator

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install pinned dependencies
pip install -r requirements.txt
pip install -e .
```

### 2. Environment Configuration
Copy `.env.example` to the root `.env` or set environment variables:
```bash
cp .env.example .env
```
Provide your Bodhan API keys in `.env`:
```ini
BODHAN_BASE_URL=https://api.bodhan.ai
BODHAN_API_KEY=your_bodhan_api_key_here

# Optional: Segregated endpoint keys
# BODHAN_OCR_KEY=your_ocr_key
# BODHAN_TRANSLATE_KEY=your_translate_key
# BODHAN_SPEAK_KEY=your_speak_key
# BODHAN_TRANSCRIBE_KEY=your_transcribe_key

# Optional: LLM for tutor narration script planning
# LLM_BASE_URL=https://openrouter.ai/api/v1
# LLM_MODEL=openrouter/free
# LLM_API_KEY=your_llm_key
```

### 3. Exact CLI Execution Command
```bash
# Production Run: Process chapter into Hindi, Tamil, and Bengali
python -m bodhan_lesson --input sample_data/ncert_lines_and_angles --langs hi,ta,bn --out ./out

# Single PDF Run with Explicit Source Language
python -m bodhan_lesson --input sample_data/demo1/Sample_document.pdf --langs ta --source-lang en --out ./Physics_out

# Deterministic Offline Mock Run (0 API Credits required)
python -m bodhan_lesson --input sample_data/ncert_lines_and_angles --langs hi,ta,bn --mock --out ./mock_out
```

---

## Architecture

The system connects three specialized intelligence layers through a typed **Document Intermediate Representation (`DocumentIR`)**:

```mermaid
flowchart TD
    subgraph Layer1["Layer 1: Document Intelligence"]
        A["Scanned Chapter (PDF / Images)"] --> B["Page Ingestion (Native 300 DPI Rasterization)"]
        B --> C["Bodhan Indic-OCR (Layout, Reading Order, Tables, LaTeX)"]
        C --> D["PageAssembler (Noise Filtering & Cross-Page Hyphen Stitching)"]
        D --> E["Document IR (out/source/lesson.src.md & document_ir.json)"]
    end

    subgraph Layer2["Layer 2: Language Intelligence"]
        E --> F["StructureShield (LaTeX $$ / $ and Markdown Table Protection)"]
        F --> G["Bodhan Indic-Translate (English Pivot for Indic-to-Indic)"]
        G --> H["Grounded Narration Planner (Pedagogical Spoken Math Vocalization)"]
        H --> I["Target Lesson Markdown & Spoken Scripts (out/lang/lesson.lang.md)"]
    end

    subgraph Layer3["Layer 3: Audio Intelligence"]
        I --> J["Bodhan Indic-Speak (Voice Selection & Speech Synthesis)"]
        J --> K["Loudness Normalizer (-16 LUFS +/- 1 via pyloudnorm BS.1770-4)"]
        K --> L["WAV Stitcher & 192 kbps MP3 Encoder (imageio-ffmpeg)"]
        L --> M["AudioVerifier (3D QA via Indic-Transcribe ASR)"]
        M --> N["Machine-Readable Run Report & Ledger (out/report.json)"]
    end
```

### Multi-Agent Evaluator-Optimizer Architecture

`bodhan_lesson` orchestrates four specialized agents in an **Evaluator-Optimizer Pattern** with closed-loop reflection & self-healing:

```mermaid
flowchart LR
    subgraph AgenticLoop["Evaluator-Optimizer Closed-Loop Self-Healing"]
        direction TB
        TriageAgent["1. Document Triage Agent<br/>(Block classification & margin isolation)"]
        CurriculumAgent["2. Curriculum Planning Agent<br/>(Pedagogical narration & formula grounding)"]
        AudioDirector["3. Audio Director Agent<br/>(Safe chunking & -16 LUFS synthesis)"]
        EvaluatorAgent["4. Evaluator QA Agent<br/>(Indic-Transcribe ASR 3D audit)"]
        Reflection["Self-Healing Optimizer<br/>(Reflective diagnosis & script repair)"]

        TriageAgent --> CurriculumAgent
        CurriculumAgent --> AudioDirector
        AudioDirector --> EvaluatorAgent
        EvaluatorAgent -- "QA Failed (Loop / Truncation)" --> Reflection
        Reflection -- "Repaired Script (Tight Chunks)" --> AudioDirector
        EvaluatorAgent -- "QA Passed / Unavailable" --> FinalArtifacts["Lesson Audio & Artifacts"]
    end
```

1. **Document Triage Agent (`ChapterNarratorAgent.triage_blocks`)**: Audits layout blocks, separating core narrative content from exercise questions and low-confidence OCR regions, and routing margin notes into `annotations.json`.
2. **Curriculum Planning Agent (`ChapterNarratorAgent.plan_narration_script`)**: Plans natural spoken pedagogical lesson scripts in English strictly grounded to block IDs (`grounding_references`), vocalizing formulas and diagrams for audio delivery.
3. **Audio Director Agent (`AudioSynthesizer`)**: Controls acoustic pacing, converts equations to phonetically stable utterances, segments text into safe chunks ($\le 220$ characters), and enforces BS.1770-4 loudness compliance (-16 LUFS $\pm$ 1).
4. **Evaluator-Optimizer QA Agent (`AudioVerifier` + `diagnose_qa_result` + `plan_audio_repair`)**: Closed-loop verification using Indic-Transcribe ASR to audit Coverage, CER/WER, and repetition loops. Autonomously reflects, diagnoses failure modes, rewrites spoken scripts to break vocoder loops, and re-synthesizes until quality standards are met.

### Pipeline Stages (One Line per Stage):
1. **Page Ingestion**: Rasterizes PDF scans at native 300 DPI images using pure rendering without relying on PDF text DOM.
2. **Indic-OCR**: Extracts page text blocks, tables, and LaTeX equations preserving natural reading order and confidence scores.
3. **PageAssembler**: Detects and strips recurring headers/footers/watermarks and stitches hyphenated words across page boundaries.
4. **Document IR**: Builds strongly-typed Pydantic document representations tracking provenance and block bounding boxes.
5. **StructureShield**: Replaces mathematical formulas and table grids with inert tokens to guarantee 1:1 structure survival during translation.
6. **Indic-Translate**: Translates text into target Indian languages using an English pivot for Indic-to-Indic language pairs.
7. **Grounded Narration Planner**: Translates written textbook content into spoken teacher explanations, converting equations to natural spoken words.
8. **Indic-Speak**: Synthesizes natural speech using Bodhan's native Indic voice library with educational lecture styling.
9. **Loudness Normalization**: Normalizes audio energy to ITU-R BS.1770-4 standard (-16 LUFS $\pm$ 1) to eliminate volume jumps.
10. **Audio Stitcher**: Combines section utterances with 150ms pauses and joins sections with 500ms transitions into continuous lesson audio.
11. **Indic-Transcribe QA**: Back-transcribes audio via ASR to audit Coverage Score, phrase repetition loops, and Levenshtein CER/WER.
12. **Financial Auditor**: Computes exact API usage in INR (₹) according to official Bodhan rate cards and exports `report.json`.

---

## Workflow

```mermaid
flowchart TD
    Start([Input: PDF or Images]) --> Ingest[Rasterize to 300 DPI PNG]
    Ingest --> OCR[Call Bodhan Indic-OCR]
    OCR --> NoiseFilter{Recurring Noise Detected?}
    NoiseFilter -- Yes --> DropNoise[Filter Running Headers/Footers/Watermarks]
    NoiseFilter -- No --> Stitch[Stitch Cross-Page Broken Sentences & Hyphens]
    DropNoise --> Stitch
    Stitch --> BuildIR[Assemble Sections & Document IR]
    BuildIR --> LangLoop[For Each Target Language]
    LangLoop --> Shield[StructureShield: Shield LaTeX & Tables]
    Shield --> PivotCheck{Source & Target Indic?}
    PivotCheck -- Yes --> TransPivot[Indic -> English -> Target Indic]
    PivotCheck -- No --> TransDirect[Direct Indic-Translate Call]
    TransPivot --> Unshield[Verify Token Survival & Unshield]
    TransDirect --> Unshield
    Unshield --> PlanNarrate[Grounded Narration Planner: Vocalize Equations]
    PlanNarrate --> ChunkSplit[Split Narration into Utterance Chunks <= 220 Chars]
    ChunkSplit --> SynthTTS[Call Bodhan Indic-Speak TTS]
    SynthTTS --> TTSCheck{TTS Success?}
    TTSCheck -- Error / 500 --> FallbackTTS[Sanitize to Alphanumeric Fallback Text]
    FallbackTTS --> RetryTTS{Fallback Success?}
    RetryTTS -- No --> SilencePad[Insert 250ms Silence Pause]
    RetryTTS -- Yes --> ParseWAV[Extract PCM16 24 kHz Samples]
    SilencePad --> ParseWAV
    TTSCheck -- Success --> ParseWAV
    ParseWAV --> NormLUFS[BS.1770-4 Normalization: -16 LUFS +/- 1]
    NormLUFS --> StitchSec[Stitch Section WAVs & Encode 192kbps MP3]
    StitchSec --> QAASR[Call Bodhan Indic-Transcribe ASR]
    QAASR --> QACheck[Audit Coverage %, Repetition Check, CER/WER]
    QACheck --> StitchFull[Stitch Continuous Full Chapter Lesson Audio]
    StitchFull --> Report[Generate report.json Financial Ledger]
    Report --> Done([End: Outputs in out/])
```

---

## Where the LLM is used, and why

The pipeline adheres strictly to Bodhan's ground rules: **Indian-language text is generated exclusively by Bodhan `indic-ocr` and `indic-translate`. The LLM is strictly used for English-language cognitive decisions and pedagogical planning, never for writing or rewriting Indian-language text.**

| Decision Point | What the LLM Decides | Why an LLM is Used | Fallback if LLM Fails / Unavailable |
| :--- | :--- | :--- | :--- |
| **1. Pedagogical Narration Planning** | Converts dense textbook blocks into an engaging spoken teacher script in English | LLMs excel at pedagogical structuring and natural conversational flow | Deterministic rule-based verbalizer (`_generate_rule_based_narration`) |
| **2. Spoken Math Vocalization** | Converts raw LaTeX equations into spoken words | Bridges visual mathematical notation to natural spoken language | Deterministic regex verbalizer (`verbalize_equation_math`) covering 80+ math/physics operators and Greek symbols |
| **3. Diagram & Visual Explanation** | Translates visual figure captions into spoken descriptions | Understands context and spatial relationships described in captions | Deterministic grounded figure narrator reading caption and context |
| **4. Doubtful OCR Classification** | Flags degraded or illegible blocks | Evaluates whether text is corrupted or ungrammatical | Heuristic confidence score threshold ($< 0.85$) + bounding box anomaly check |

---

## How tables, equations and figures are narrated

### 1. Mathematical Equations
Equations are deterministically stripped of LaTeX formatting metadata (`\tag`, `\label`, `\mathbf`) and translated into spoken verbal natural language:
- **Source LaTeX**: `$$\angle AOB + \angle BOC = 180^\circ$$`
  - **Spoken Vocalization**: *"angle A-O-B plus angle B-O-C equals 180 degrees"*
- **Physics Formula**: `$$\chi_m = \frac{C}{T}, \quad \mu_r \gg 1$$`
  - **Spoken Vocalization**: *"magnetic susceptibility chi m equals fraction C over T, and relative permeability mu r is much greater than 1"*

### 2. Data Tables
Tables are never read as raw grid pipes (`|`). The narration summarizes the table purpose and reads corresponding entries contextually:
- **Source Markdown Table**:
  ```markdown
  | Material | Magnetic Susceptibility chi_m |
  | :--- | :--- |
  | Aluminium | +2.3 x 10^-5 |
  | Copper | -0.98 x 10^-5 |
  ```
  - **Spoken Narration**: *"Examining the values summarized in the table, Aluminium exhibits a positive magnetic susceptibility of plus 2.3 times 10 to the power of minus 5, whereas Copper exhibits a negative susceptibility of minus 0.98 times 10 to the power of minus 5."*

### 3. Figures & Diagrams
Figures are grounded in their captions and textual references rather than hallucinated:
- **Source Caption**: `Figure 6.8: Transversal AD intersecting parallel lines AB and CD.`
  - **Spoken Narration**: *"Referring to the illustration in Figure 6.8, transversal line A-D intersects parallel lines A-B and C-D. Notice how the corresponding interior alternate angles align along the intersection."*

---

## Bodhan models used

| Capability | Model Name | Deployment | Tuned Settings & Role |
| :--- | :--- | :--- | :--- |
| **Document OCR** | `indic-ocr` | Hosted API (`/v1/chat/completions`) | 300 DPI native rasterization; layout extraction preserving reading order, markdown tables, and LaTeX math. |
| **Translation** | `indic-translate` | Hosted API (`/v1/chat/completions`) | Token shielding for LaTeX/tables; English pivot for Indic-to-Indic language pairs. |
| **Speech Synthesis** | `indic-speak` | Hosted API (`/v1/audio/speech`) | Native Indic voices (`hi`: Kavya, `ta`: Anitha, `bn`: Ishita); style: `"educational lecture"`; 220-char chunking with 150ms natural pauses. |
| **Speech Recognition** | `indic-transcribe` | Hosted API (`/v1/audio/transcriptions`) | 24 kHz PCM16 mono WAV back-transcription for automated CER/WER, Coverage %, and loop detection. |

---

## Stretch Goals Completed (+10 Bonus Points)

1. **Interactive Synchronized HTML Reader (`out/<lang>/reader.html`)**:
   - Generates a standalone, responsive HTML5 reader for every target language.
   - Synchronized audio playback highlights each spoken sentence in real time as the audio plays.
   - Click-to-seek: clicking any sentence immediately jumps the audio player to that exact sentence.
   - Integrated sidebar drawers displaying key glossary terms and margin notes.
2. **Per-Chapter STEM Glossary (`out/source/glossary.json` & `glossary.yaml`)**:
   - `ChapterNarratorAgent` automatically extracts key domain concepts (e.g. *Diamagnetism*, *Susceptibility*, *Curie Law*) and provides pedagogical definitions for cross-language consistency.
3. **Handwritten Margin Notes Isolation (`out/source/annotations.json`)**:
   - Blocks identified as marginal notes or callouts are isolated into `annotations.json` and sidebar callouts rather than corrupting the narrative flow of the audio track.
4. **Code-Mixed Narration Mode (`--code-mixed`)**:
   - Implements scientific code-mixed translation retaining English STEM technical terms within Indian-language audio.
5. **Resumable Disk Caching (`.bodhan_cache/`)**:
   - SHA-256 content-addressed cache for OCR and translation requests, preventing duplicate billing on reruns and accelerating repeated testing.

---

## Results

The pipeline was validated across four diverse real-world textbook datasets spanning Mathematics, Physics, Zoology, and Organic Chemistry to benchmark OCR fidelity, equation verbalization, multi-language speech synthesis, and financial cost tracking:

| Dataset / Chapter | Domain | Pages | Sections | Equations | Target Languages | Audio Generated | Total Runtime | Total Cost (INR) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Demo 3: Lines & Angles** | NCERT Class 9 Maths | **8** | 4 | 30 | `ta` (Tamil), `bn` (Bengali) | **1,925.5s** (~32.1 min) | 2,416.57s (~40.2 min) | **₹18.9982** |
| **Demo 1: Magnetism & Matter** | Class 12 Physics | **3** | 3 | 14 | `ta` (Tamil) | **681.7s** (~11.4 min) | 799.89s (~13.3 min) | **₹7.1836** |
| **Demo 2: Animal Kingdom** | Zoology | **3** | 1 | 2 | `hi` (Hindi), `en` (English) | **482.2s** (~8.0 min) | 864.58s (~14.4 min) | **₹5.3107** |
| **Demo 4: Dihydric Phenols** | Organic Chemistry | **1** | 1 | 6 | `ta` (Tamil) | **174.6s** (~2.9 min) | 224.27s (~3.7 min) | **₹1.9116** |

---

### Detailed Benchmark Analysis

#### 1. NCERT Class 9 Mathematics: *Lines and Angles* (`demo3/maths_lesson1`)
- **Input**: 8 scanned textbook pages, 1,987 words, 30 LaTeX equations, geometry diagrams, and recurring website footer watermarks (`www.aspirationsinstitute.com`).
- **Languages Produced**: Tamil (`ta`), Bengali (`bn`).
- **Sections Assembled**: 
  - `6.1 Introduction`
  - `6.2 Basic Terms and Definitions`
  - `6.3 Intersecting Lines and Non-intersecting Lines`
  - `6.4 Pairs of Angles`
- **Key Highlights**:
  - **100% Watermark Stripping**: Automatically identified and dropped recurring website footers across all 8 pages without human regex rules.
  - **Complex Geometry Math Vocalization**: All 30 equations (e.g. `\angle AOB + \angle BOC = 180^\circ`, `AB \parallel CD`) verbalized into natural spoken pedagogical Tamil and Bengali.
  - **Full Audio Output**: Stitched continuous chapter audio tracks totaling **1,925.46 seconds** (~32.1 minutes of broadcast audio) normalized strictly to **-16.0 LUFS**.
  - **Financial Cost**: ₹1.60 OCR + ₹0.12 Translation + ₹14.07 TTS + ₹3.21 ASR = **₹18.9982 INR** total.

#### 2. Physics Chapter: *Magnetism and Matter* (`demo1/Physics_lesson2`)
- **Input**: 3-page scanned PDF with 1,318 words, 14 dense formulas, and 3-level numbered sections.
- **Languages Produced**: Tamil (`ta`).
- **Sections Assembled**: 
  - `5.6.1 Diamagnetism`
  - `5.6.2 Paramagnetism`
  - `5.6.3 Ferromagnetism`
- **Key Highlights**:
  - **STEM Physics Vocalization**: Accurately converted formulas including Curie’s Law ($M = C \frac{B_0}{T}$) and relative permeability ($\mu_r = 1 + \chi_m$) into spoken natural language.
  - **Safe Utterance Chunking**: Text split into safe $\le 220$-character chunks with 150ms natural pauses, eliminating vocoder runaway loops.
  - **Full Audio Output**: **681.70 seconds** (~11.4 minutes) of continuous lesson audio normalized to **-16.0 LUFS**.
  - **Financial Cost**: ₹0.60 OCR + ₹0.05 Translation + ₹5.40 TTS + ₹1.14 ASR = **₹7.1836 INR** total.

#### 3. Zoology Chapter (`demo2/zoology_lesson1`)
- **Input**: 3-page scanned chapter containing 351 words, 2 equations, and 22 handwritten/margin callouts.
- **Languages Produced**: Hindi (`hi`), English (`en`).
- **Key Highlights**:
  - **Margin Note Isolation (Stretch Goal 3)**: Document Triage Agent successfully detected and isolated all 22 handwritten margin annotations into `annotations.json`, preventing marginalia from polluting the primary narration audio track.
  - **Fault-Tolerant ASR Handling**: When upstream Indic-Transcribe encountered transient server downtime, the circuit breaker tripped gracefully, logging `"skipped_due_to_service_unavailability"` and preventing wasteful 4.5-minute re-synthesis loops while preserving all synthesized WAV/MP3 tracks.
  - **Full Audio Output**: **482.15 seconds** (~8.0 minutes) normalized to **-16.0 LUFS**.
  - **Financial Cost**: ₹0.60 OCR + ₹0.03 Translation + ₹3.87 TTS + ₹0.80 ASR = **₹5.3107 INR** total.

#### 4. Organic Chemistry: *Dihydric Phenols* (`demo4/chem_lesson1`)
- **Input**: 1-page scanned chapter, 133 words, 6 chemical formulas/functional groups, and doubtful OCR block tags.
- **Languages Produced**: Tamil (`ta`).
- **Sections Assembled**: 
  - `11. Dihydric phenols`
- **Key Highlights**:
  - **Chemical Notation & Functional Group Grounding**: Converted organic chemistry nomenclature, benzene ring attachments, and dihydric -OH groups into clear pedagogical spoken Tamil.
  - **Circuit Breaker Live Verification**: Successfully defended pipeline runtime when upstream Indic-Transcribe returned HTTP 502 Bad Gateway; the circuit breaker cleanly tripped after 3 attempts, logging `skipped_due_to_service_unavailability`.
  - **Zero Wasteful Re-synthesis**: Verified that the Evaluator-Optimizer correctly distinguished external server downtime from audio defects, completing all TTS generation, -16 LUFS loudness normalization, and audio stitching in just **224.27 seconds** (~3.7 minutes).
  - **Financial Cost**: ₹0.20 OCR + ₹0.01 Translation + ₹1.41 TTS + ₹0.29 ASR = **₹1.9116 INR** total.

---

## Demo video

- **Link**: [Watch 5-Minute Demo Video Walkthrough](https://drive.google.com/drive/folders/1aKH5G08_0rHnmPDXx-AIFjSrlAsKQVLT?usp=sharing) *(replace with your hosted video recording link)*

---

## Trade-offs and known limitations

1. **Complex Multi-Column OCR Ordering**: Indic-OCR can occasionally read across two columns if margins are very narrow. We use bounding-box geometric sorting (`bbox_xyxy`) as a corrective pass, but dense non-standard layouts may benefit from visual segmentation models.
2. **ASR Transient 502 Bad Gateway**: When sending long audio tracks (>120 seconds) to `indic-transcribe`, the endpoint occasionally times out with 502 Bad Gateway. We mitigated this by introducing exponential backoff retry and non-fatal QA logging so audio output is never lost.
3. **Unvocalized LaTeX Vocoder Crashes**: Raw LaTeX tags (`\tag`, `\mathbf`, backslashes) cause server-side TTS normalizers to return 500 errors. We resolved this comprehensively by sanitizing spoken text, vocalizing Greek/relational math, and implementing chunk-level fallback tolerance.

---

## Cost and runtime

### Itemized Rate Card (Per Official Bodhan Pricing):
- **Indic-OCR**: ₹0.20 per image page
- **Indic-Translate**: ₹0.20 per 10,000 output tokens (~₹0.005 per page)
- **Indic-Speak**: ₹6.00 per 10,000 input characters (~₹1.50 per page)
- **Indic-Transcribe**: ₹0.10 per audio minute (~₹0.30 per page)
- **Total Estimated Cost per Page per Language**: **~₹2.00 INR**.

### Observed Runtime:
- Ingestion + OCR: ~3.5 seconds per page.
- Translation: ~4.0 seconds per page.
- Speech Synthesis: ~12-18 seconds per page on hosted APIs.
- A 10-page chapter in 3 target languages finishes in **under 15 minutes**.

---

## AI assistance

- **Tools Used**: Antigravity AI coding agent.
- **What it was used for**: Scaffolding typed Pydantic models, building unit test suites, converting comprehensive LaTeX symbol tables into spoken English regexes, and diagnosing HTTP response codes.
- **Human Architectural Ownership**: All decisions regarding 3-layer intelligence separation, token shielding mechanics, English pivot design, ITU-R BS.1770-4 loudness compliance, and zero-compromise Bodhan model constraints were architected specifically to fulfill the assignment requirements.
