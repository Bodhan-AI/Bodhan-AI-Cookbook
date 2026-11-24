import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from services.alerts import evaluate_report
from services.bodhan import (
    extract_report_details,
    speak_text,
    transcribe_audio,
    translate_text,
)
from src.database import (
    create_report,
    get_alerts,
    get_report,
    init_db,
    search_reports,
    update_alert,
)

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="CropShield",
    description="Multilingual crop observations and regional early-warning reports",
)

init_db()

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "bodhan_configured": {
            "transcribe": bool(os.getenv("BODHAN_TRANSCRIBE_API_KEY")),
            "translate": bool(os.getenv("BODHAN_TRANSLATE_API_KEY")),
            "speak": bool(os.getenv("BODHAN_SPEAK_API_KEY")),
        },
    }


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = ""):  # noqa: B008
    suffix = Path(file.filename or "audio.wav").suffix.lower()

    if suffix not in {".wav", ".flac", ".ogg", ".mp3"}:
        raise HTTPException(
            status_code=400,
            detail="Use WAV, FLAC, OGG or MP3 audio.",
        )

    data = await file.read(15 * 1024 * 1024 + 1)

    if not data:
        raise HTTPException(status_code=400, detail="Audio file is empty.")

    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="Audio file must be under 15 MB.",
        )

    path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name

        transcript = transcribe_audio(path, language or None)

        return {
            "text": transcript,
            "language": language or "auto",
        }

    except Exception as exc:
        print("TRANSCRIPTION ERROR:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail="Audio transcription failed.",
        ) from exc

    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


@app.post("/api/translate")
def translate(payload: dict):
    text = payload.get("text", "").strip()
    target = payload.get("target_language", "en")

    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    try:
        translation = translate_text(text, target)

        return {
            "translation": translation,
            "target_language": target,
        }

    except Exception as exc:
        print("TRANSLATION ERROR:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail="Translation failed.",
        ) from exc


@app.post("/api/extract")
def extract(payload: dict):
    text = payload.get("text", "").strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="English text is required",
        )

    try:
        details = extract_report_details(text)
        return details

    except Exception as exc:
        print("EXTRACTION ERROR:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail="Report extraction failed.",
        ) from exc


@app.post("/api/speak")
def speak(payload: dict):
    text = payload.get("text", "").strip()
    language = payload.get("language", "ta")

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text is required",
        )

    try:
        audio = speak_text(text=text, language=language)

        return Response(
            content=audio,
            media_type="audio/wav",
        )

    except Exception as exc:
        print("SPEAK ERROR:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail="Speech generation failed.",
        ) from exc


@app.post("/api/reports")
def submit_report(payload: dict):
    required = [
        "crop",
        "symptoms",
        "location",
        "original_language",
        "original_text",
        "translated_text",
    ]

    missing = [field for field in required if not str(payload.get(field, "")).strip()]

    if missing:
        raise HTTPException(
            status_code=400,
            detail="Missing fields: " + ", ".join(missing),
        )

    payload.setdefault(
        "date",
        datetime.now(timezone.utc).date().isoformat(),
    )

    report_id = create_report(payload)
    report = get_report(report_id)

    evaluate_report(report)

    return {
        "id": report_id,
        "report": report,
    }


@app.get("/api/reports")
def list_reports(crop: str = "", location: str = "", symptoms: str = ""):
    return search_reports(crop, location, symptoms)


@app.get("/api/reports/{report_id}")
def one_report(report_id: int):
    report = get_report(report_id)

    if not report:
        raise HTTPException(
            status_code=404,
            detail="Report not found",
        )

    return report


@app.get("/api/alerts")
def list_alerts():
    return get_alerts()


@app.patch("/api/alerts/{alert_id}")
def patch_alert(alert_id: int, payload: dict):
    status = payload.get("status", "")

    if status not in ["Pending", "Under review", "Investigated"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid alert status",
        )

    result = update_alert(alert_id, status)

    if result is False:
        raise HTTPException(
            status_code=404,
            detail="Alert not found",
        )

    return {
        "updated": True,
        "alert_id": alert_id,
        "status": status,
    }
