# BoardVaani — Street Sign & Shop Board Reader

> **Bodhan AI Cookbook Recipe #9** • Sovereign Indic Assistive Visual Copilot  
> 100% Client-Side React + Vite + TypeScript Application.  
> Styled strictly with the official design system, tokens, and dark/light mode palette of [**console.bodhan.ai**](https://console.bodhan.ai/).

**Bodhan models:** indic-ocr, indic-translate, indic-transliterate, indic-speak  
**Languages shown:** All 22 scheduled Indic languages (Tamil, Telugu, Kannada, Malayalam, Hindi, Marathi, Gujarati, Bengali, Odia, Assamese, Punjabi, Urdu, Kashmiri, Santali, Manipuri, Sindhi, Bodo, Sanskrit, Maithili, Dogri, Konkani, Nepali) + English  
**Format:** Standalone Mobile & Web Application (React 18, Vite, TypeScript, zero middleman server)

---

## What it does

**BoardVaani** empowers travelers, pedestrians, and visually impaired citizens to point their camera at any Indian street sign, shop board, highway milestone, or transit platform, and hear clean, natural spoken speech in their native language within seconds.

Key capabilities:
1. **Interactive Precision Cropper:** 8-point bounding handles with Rule-of-Thirds grid, 90° clockwise rotation, and 16:9 / 3:1 aspect ratio presets to isolate signs from outdoor street clutter.
2. **On-Device Noise Cleaner (~3ms):** Automatically strips GSTIN codes, trade licenses, FSSAI numbers, and flex-printing watermarks. Converts arrow symbols (`➔`, `➜`) into spoken spatial directions (`Straight ahead`, `On your right`).
3. **Sovereign Non-LLM Pipeline for Distinct Scripts:** High-resource languages use conversational synthesis, while low-resource and unique-script languages automatically bypass external LLMs to guarantee 100% native script accuracy without hallucinations:
   - **Santali (`sat`):** Direct native **Ol Chiki** (`ᱚᱞ ᱪᱤᱠᱤ`)
   - **Manipuri (`mni`):** Direct native **Meitei Mayek** (`ꯃꯤꯇꯩ ꯃꯌꯦꯛ`)
   - **Kashmiri (`ks`):** Direct native **Nastaliq** (`کٲشُر`)
   - **Sindhi (`sd`):** Direct native **Arabic script** (`سنڌي`)
   - **Bodo (`brx`):** Native Tibeto-Burman grammar (`दं`, `-आव`, `-नि`)
   - **Sanskrit (`sa`):** Classical inflections (`अस्ति`, `सौविध्यम्`)
4. **AR Interactive Overlays:** Color-coded semantic overlays (Blue for Store Name, Green for Services, Orange for Contact, Gray for filtered noise). Tap any box to hear that specific text in isolation.
5. **Studio Speech Synthesis (24 kHz):** 45 studio voices across 22 scheduled languages with 1.0x / 1.25x speed controls and playback scrubbers.
6. **Zero-Setup Mock Mode & BYOK:** Works out-of-the-box with pre-bundled realistic sample signs (Odia Clinic, Tamil Bakery, Hindi Pharmacy, Highway Milestones) and allows entering your own Bodhan keys in Settings.

---

## Setup

### Prerequisites
* **Node.js:** `v18.0.0` or higher (tested on Node `v20.x`)
* **npm:** `v9.0.0` or higher
* Modern mobile or desktop browser (Chrome, Edge, Safari, Firefox)

### 1. Install Dependencies
```bash
npm install
```

### 2. Configuration (Optional)
The application is 100% client-side and includes offline zero-setup mock signboards. 
To connect live Bodhan cloud models:
1. Open the in-app **Settings (⚙️)** modal.
2. Paste your Virtual Keys from [console.bodhan.ai](https://console.bodhan.ai/):
   - `indic-ocr` Virtual Key
   - `indic-translate` Virtual Key
   - `indic-transliterate` Virtual Key
   - `indic-speak` Virtual Key
3. Keys are stored locally in your browser's encrypted `localStorage` and sent directly to `https://api.bodhan.ai`.

For terminal reference, copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

---

## Run

### 1. Start Local Development Server
```bash
npm run dev
```

The application will start at:
* **Localhost:** `http://localhost:3000/`
* **Mobile LAN:** `http://<YOUR_LOCAL_IP>:3000/` *(Open on your mobile phone connected to the same Wi-Fi to test the camera!)*

### 2. Build for Production
```bash
npm run build
npm run preview
```

### 3. Deploying to Sandbox (Vercel / Netlify)
BoardVaani includes pre-configured `vercel.json` and `netlify.toml`:
* **Vercel:** Run `npx vercel` or import the repository.
* **Netlify:** Run `npx netlify deploy --prod --dir=dist` or drag-and-drop the `dist/` directory into Netlify Drop.

---

## How it works

### End-to-End Pipeline Physics

```
       [ Outdoor Signboard Photo / Live Camera ]
                          │
                          ▼
             [ Indic Document OCR (indic-ocr) ]
                          │
    ┌─────────────────────┴─────────────────────┐
    │                                           │
[Tier A: High-Resource Languages]        [Tier B: Unique Scripts & Regional]
(en, hi, ta, te, bn, mr, gu,            (Santali, Manipuri, Kashmiri, Sindhi,
 kn, ml, pa, ur)                         Bodo, Sanskrit, Maithili, Dogri, etc.)
    │                                           │
    ▼                                           ▼
[Conversational Synthesizer]             [Pure Bodhan IndicTrans2 (Zero LLM)]
  • Removes statutory tax/lic codes        • 100% Direct Sovereign Indic Translation
  • Filters flex-banner watermarks         • Zero hallucination, zero token bloat
  • Resolves hours & direction             • Direct native Ol Chiki / Meitei Mayek
    │                                           │
    ▼                                           │
[Unicode Script-Fidelity Guard]                 │
  Did output match correct script?              │
  • Ol Chiki (\u1C50-\u1C7F)?                   │
  • Meitei Mayek (\uABC0-\uABFF)?               │
  • Nastaliq (\u0600-\u06FF)?                   │
    ├── NO ──► [IndicTrans2 Pivot]              │
    └──────────────┬────────────────────────────┘
                   ▼
     [ Bodhan Indic-Speak TTS (24 kHz WAV) ]
  (45 Studio Voices: Kavya, Phulmani, Thoibi, Zoon, etc.)
                   ▼
       [ Natural Audio Playback in Earbuds ]
```

1. **Capture & Pre-Processing:** The camera captures a raw street photo. If the **Crop Tool** toggle is enabled, the 8-point cropper lets the user adjust boundaries, apply rule-of-thirds framing, and rotate 90°.
2. **Indic Document OCR:** The image is sent to Bodhan's `indic-ocr` model (`POST /v1/chat/completions`), returning text blocks with spatial bounding coordinates.
3. **Statutory & Directional Cleaner:** Runs on-device in ~3ms to clean GSTINs, licenses, and map arrows into spatial words.
4. **Script-Fidelity Translation:** Depending on the target language, queries are routed through Bodhan's `indic-translate` (`POST /translate`) or the sovereign script guard.
5. **Speech Synthesis:** Bodhan's `indic-speak` model (`POST /v1/audio/speech`) synthesizes studio-grade 24 kHz WAV audio in the selected regional persona.

---

## Supported Languages & Voices

| Zone | Languages | Scripts Used | Bodhan Studio Voices |
| :--- | :--- | :--- | :--- |
| **Southern** | Tamil (`ta`), Telugu (`te`), Kannada (`kn`), Malayalam (`ml`) | Tamil, Telugu, Kannada, Malayalam | Anitha, Arun, Sravani, Vamsi, Deepika, Adarsh, Lakshmi, Kiran |
| **Western & Central** | Hindi (`hi`), Gujarati (`gu`), Marathi (`mr`), Konkani (`kok`), Sindhi (`sd`) | Devanagari, Gujarati, Perso-Arabic | Kavya, Amit, Suhani, Dhara, Parth, Anagha, Chinmay, Anjali, Sandeep, Moomal, Rano |
| **Eastern** | Bengali (`bn`), Odia (`or`), Assamese (`as`), Maithili (`mai`), Santali (`sat`), Manipuri (`mni`) | Bengali, Odia, Assamese, Devanagari, **Ol Chiki**, **Meitei Mayek** | Ishita, Sourav, Itishree, Akash, Prastuti, Ankur, Vaidehi, Madhukar, Phulmani, Sibu, Thoibi, Chaoba |
| **Northern** | Punjabi (`pa`), Urdu (`ur`), Kashmiri (`ks`), Nepali (`ne`), Dogri (`doi`), Bodo (`brx`), Sanskrit (`sa`) | Gurmukhi, **Nastaliq**, Devanagari | Kaur, Manpreet, Saba, Zaid, Zoon, Ishfaq, Srijana, Sagar, Preeti, Sham, Gwrbw, Sansuma, Bharati, Aryaman |
| **Global** | English (`en`) | Latin | Kavya, Amit |

---

## Security & Privacy

* **Zero Cloud Key Storage:** API keys are never sent to external servers or logged. They live exclusively in the browser's hardware-isolated `localStorage`.
* **Zero Data Retention:** Captured street images are processed in-memory as base64 data URLs and never written to remote disks.
* **Open Source & Auditable:** All network requests target `https://api.bodhan.ai` directly.

---

## License

Built for the **Bodhan AI Cookbook Showcase**. Licensed under the [MIT License](LICENSE).
