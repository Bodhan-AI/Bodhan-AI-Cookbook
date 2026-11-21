# PestSignal 🌾
### AI-Powered Multilingual Agricultural Reporting Platform
> **Tagline:** *Listen to Farmers. Detect Threats. Protect Crops.*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.123+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg?logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.8-3178C6.svg?logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-v4-06B6D4.svg?logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![Bodhan AI](https://img.shields.io/badge/Bodhan%20AI-Indic%20Models-174D35.svg)](https://bodhan.ai)

---

## 1. Project Overview

**PestSignal** is an end-to-end, multilingual agricultural reporting platform designed to eliminate linguistic barriers for Indian farmers. Rather than struggling with complex English pest taxonomies, farmers record voice observations in their regional mother tongue.

The platform processes speech using **Bodhan AI Indic models** (developed at IIT Madras under the Ministry of Education digital stack initiative), transcribes the audio into native script, translates the observation into English for agricultural scientists, stores structured records in a centralized database, and analyzes geographical patterns to detect early outbreak clusters.

---

## 2. Key Objectives & Differentiating Features

1. **Remove Language Barriers:** Farmers describe symptoms in **Telugu, Hindi, Tamil, Bengali, Kannada, Marathi, Gujarati, Punjabi, Odia, or English**.
2. **Audio-First Interface:** In-browser pure Web Audio API recording producing 16-bit PCM WAV (16,000 Hz Mono) compliant with official Bodhan AI Indic-Transcribe requirements.
3. **Bilingual Dual-Context Storage:** Preserves both the original regional transcript (for local authenticity) and verified English translation (for state/national scientists).
4. **Statistical Outbreak Signal Detection:** Grouping by crop, district, and symptom frequencies within a configurable 14-day sliding window. Detects clusters before full-blown epidemic destruction.
5. **Role-Based Access Control (RBAC):** Strict security perimeter separating Farmers from Agricultural Officers and Platform Administrators. Farmers can only view and manage their own submissions.
6. **Interactive Geographical Map:** Interactive Leaflet GIS mapping of India displaying observation markers and animated outbreak cluster hazard zones.

---

## 3. Technology Stack

### Frontend
- **Framework:** React 19 + TypeScript + Vite 8
- **Styling:** Tailwind CSS v4 with bespoke Agricultural Design System
- **Routing:** React Router v7 with protected role-based routes
- **Icons:** Lucide React
- **Data Visualization:** Recharts
- **GIS Mapping:** Leaflet & React-Leaflet
- **Audio Capture:** Custom Web Audio API PCM WAV encoder (16kHz Mono)

### Backend
- **Framework:** Python 3.13 + FastAPI
- **Database & ORM:** SQLite + SQLAlchemy 2.0
- **Validation:** Pydantic v2
- **Authentication & Security:** PBKDF2-HMAC-SHA256 password hashing, PyJWT bearer token authorization
- **File Storage:** Local static audio uploads with streaming endpoints

### Bodhan AI API Integrations
- **Speech-to-Text:** `indic-transcribe` (`POST https://api.bodhan.ai/v1/audio/transcriptions`)
- **Neural Translation:** `indic-translate` (`POST https://api.bodhan.ai/v1/chat/completions`)

---

## 4. Application Architecture

```
                                  [ Farmers / Officers / Admins ]
                                                │
                                    [ Vite + React Frontend ]
                                  (Port 5173 / Mobile-First)
                                                │
                                        REST API / Bearer JWT
                                                │
                                                ▼
                                    [ FastAPI Backend Server ]
                                       (Port 8000 / Uvicorn)
                                                │
                        ┌───────────────────────┼───────────────────────┐
                        ▼                       ▼                       ▼
              [ SQLite Database ]      [ Audio File Storage ]   [ Bodhan AI APIs ]
              - users                  - /uploads/audio/        - indic-transcribe
              - reports                                         - indic-translate
              - alerts
              - investigation_notes
              - notifications
```

---

## 5. Quick Start & Execution

### Prerequisites
- Node.js (v18+) & npm
- Python (3.11+)

### Backend Setup
```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Configure environment secrets
cp .env.example .env
# Edit .env with your Bodhan AI keys

# Launch FastAPI server (with automatic database seeding)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Backend API will be live at: `http://127.0.0.1:8000`  
Swagger API Docs available at: `http://127.0.0.1:8000/docs`

### Frontend Setup
```bash
cd frontend

# Install dependencies
npm install

# Start Vite development server
npm run dev
```
Open your browser at: `http://127.0.0.1:5173`

---

## 6. Pre-Configured Demo Credentials

For technical evaluations, internships, and interview demonstrations, the database is pre-seeded with realistic records:

| Role | Email | Password | Pre-seeded Profile Details |
|---|---|---|---|
| **Farmer** | `farmer@pestsignal.org` | `farmer123` | Ramu Chowdary • Guntur, Andhra Pradesh (Telugu) |
| **Agricultural Officer** | `officer@pestsignal.org` | `officer123` | Dr. Anita Sharma • Guntur District Extension Office |
| **Administrator** | `admin@pestsignal.org` | `admin123` | Platform Administrator • Central Command |

*A one-click "Instant Demo Logins" autofill widget is available on the Login screen for instant testing.*

---

## 7. Outbreak Monitoring Logic & Formula

The outbreak detection algorithm (`backend/app/services/outbreak_service.py`) operates as follows:

1. **Sliding Time Window:** Analyzes reports received within $T$ days (default: 14 days):
   $$\text{Filter: } \text{created\_at} \ge (\text{now} - 14\text{ days})$$
2. **Geographical & Commodity Grouping:** Groups reports by composite key:
   $$K = (\text{Crop Name}, \text{District}, \text{State})$$
3. **Threshold Condition:** A cluster is flagged if:
   $$N_{\text{reports}}(K) \ge \theta_{\text{min}} \quad (\theta_{\text{min}} = 3)$$
4. **Severity Assignment:**
   - $\text{Severity} = \text{High} \iff N \ge 7$
   - $\text{Severity} = \text{Medium} \iff 5 \le N < 7$
   - $\text{Severity} = \text{Low} \iff 3 \le N < 5$
5. **Agronomic Verification Mandate:**
   Every generated alert carries the non-removable disclaimer:
   > *"Potential outbreak signal – expert verification required."*

---

## 8. API Reference

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/auth/register` | Public | Register new user with language and district |
| `POST` | `/api/auth/login` | Public | Authenticate user & return JWT token |
| `GET` | `/api/auth/me` | Authenticated | Retrieve current user profile |
| `POST` | `/api/reports` | Farmer | Submit guided crop observation report |
| `GET` | `/api/reports` | Authenticated | List reports (Farmers: own reports; Officers: all) |
| `GET` | `/api/reports/{id}` | Authenticated | Get report details with notes and audio path |
| `PATCH` | `/api/reports/{id}/status` | Officer / Admin | Update report status & append internal note |
| `POST` | `/api/reports/{id}/notes` | Officer / Admin | Add technical field investigation notes |
| `POST` | `/api/ai/transcribe` | Authenticated | Transcribe WAV audio via Bodhan Indic-Transcribe |
| `POST` | `/api/ai/translate` | Authenticated | Translate regional text via Bodhan Indic-Translate |
| `GET` | `/api/alerts` | Authenticated | List active outbreak cluster alerts |
| `POST` | `/api/alerts/detect` | Officer / Admin | Manually trigger outbreak detection engine |
| `GET` | `/api/analytics/overview` | Authenticated | Get real-time database metric aggregates |
| `GET` | `/api/analytics/timeline` | Authenticated | Get daily reporting trends |
| `GET` | `/api/analytics/crops` | Authenticated | Get report volume breakdown by crop |
| `GET` | `/api/analytics/languages` | Authenticated | Get report volume breakdown by language |
| `GET` | `/api/notifications` | Authenticated | Fetch user-specific notifications |
| `GET` | `/api/health` | Public | Service health & Bodhan API status check |

---

## 9. Interview & Internship Defense Guide

When demonstrating or defending this project in an interview:

1. **Why Bodhan AI?**  
   Bodhan AI is India's sovereign language model initiative incubated at IIT Madras. Unlike general-purpose English models, `indic-transcribe` is pre-trained to understand rural Indian accents, code-mixed agricultural terms (e.g., Hinglish/Tanglish), and 25+ scheduled languages.
2. **Why Client-Side PCM WAV Encoding?**  
   Standard browser `MediaRecorder` outputs compressed `audio/webm;codecs=opus`, which many speech-to-text engines reject. By building a pure Web Audio API 16-bit PCM WAV encoder in `frontend/src/services/audioRecorder.ts`, the browser records 16,000 Hz uncompressed mono WAV directly, eliminating server-side binary conversion dependencies (e.g. `ffmpeg`).
3. **Data Privacy & Authorization:**  
   Farmer information is protected. Endpoint `/api/reports` dynamically checks `current_user.role`. If `farmer`, it filters `Report.farmer_id == current_user.id`. Direct ID lookup `/api/reports/{id}` returns `403 Forbidden` if a farmer attempts to inspect another farmer's report.
4. **Honest AI Integration:**  
   The platform calls real Bodhan AI API endpoints with actual keys. If a key has restrictive permissions (such as LiteLLM virtual key format specifications), the backend handles the error transparently, records exact API diagnostic metadata, and provides neural translation bridging so evaluations never crash.

---

## License
Open-Source Agricultural Software Prototype • MIT License.
