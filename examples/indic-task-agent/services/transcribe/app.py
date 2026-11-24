import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
import torch

MODEL_DIR = os.getenv("MODEL_DIR", "/models/indic-transcribe-core")
if os.path.exists(MODEL_DIR) and MODEL_DIR not in sys.path:
    sys.path.insert(0, MODEL_DIR)

app = FastAPI(title="Bodhan Indic-Transcribe Service", version="1.0.0")

asr_model = None

def get_asr_model():
    global asr_model
    if asr_model is not None:
        return asr_model
    
    if not os.path.exists(MODEL_DIR) or not os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        raise HTTPException(
            status_code=503,
            detail=f"Model directory '{MODEL_DIR}' not found or empty. Please download weights first."
        )
    
    try:
        from indic_transcribe import IndicTranscribe
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Transcribe] Loading Indic-Transcribe from {MODEL_DIR} onto {device}...")
        asr_model = IndicTranscribe.from_pretrained(MODEL_DIR)
        print("[Transcribe] Model loaded successfully.")
        return asr_model
    except Exception as e:
        print(f"[Transcribe] Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load ASR model: {str(e)}")

@app.on_event("startup")
def startup_event():
    if os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        try:
            get_asr_model()
        except Exception as e:
            print(f"[Transcribe Startup Warning] {e}")

@app.get("/health")
def health():
    loaded = asr_model is not None
    gpu = torch.cuda.is_available()
    return {
        "status": "healthy" if loaded else "unloaded",
        "service": "indic-transcribe",
        "model_dir": MODEL_DIR,
        "cuda_available": gpu,
        "device_name": torch.cuda.get_device_name(0) if gpu else "cpu"
    }

@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: str = Form("indic-transcribe-core"),
    language: Optional[str] = Form(None),
    response_format: str = Form("json")
):
    model_instance = get_asr_model()
    
    # Save uploaded audio to a temp file
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        lang = language.strip().lower() if language and language.strip().lower() not in ["", "auto", "none"] else None
        
        if lang:
            # Explicit language supplied
            text = model_instance(tmp_path, lang=lang)
            detected_lang = lang
        else:
            # Automatic Language Identification
            text, lid = model_instance.transcribe(tmp_path, return_lid=True)
            detected_lang = lid.get("lang") if isinstance(lid, dict) else str(lid)
        
        if response_format == "text":
            return PlainTextResponse(text)
        elif response_format == "verbose_json":
            return JSONResponse({
                "text": text,
                "language": detected_lang,
                "model": model,
                "duration": None
            })
        else:
            return JSONResponse({"text": text, "language": detected_lang})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription error: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=False)
