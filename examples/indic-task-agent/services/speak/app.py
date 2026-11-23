import io
import os
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
import soundfile as sf
import torch

MODEL_DIR = os.getenv("MODEL_DIR", "/models/indic-speak")
if os.path.exists(MODEL_DIR) and MODEL_DIR not in sys.path:
    sys.path.insert(0, MODEL_DIR)

app = FastAPI(title="Bodhan Indic-Speak Service", version="1.0.0")

tts_engine = None

def get_tts_engine():
    global tts_engine
    if tts_engine is not None:
        return tts_engine

    if not os.path.exists(MODEL_DIR) or not os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        raise HTTPException(
            status_code=503,
            detail=f"Model directory '{MODEL_DIR}' not found or empty. Please download weights first."
        )

    try:
        from inference import TTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Speak] Loading Indic-Speak from {MODEL_DIR} onto {device}...")
        tts_engine = TTS(MODEL_DIR)
        print("[Speak] TTS model loaded successfully.")
        return tts_engine
    except Exception as e:
        print(f"[Speak] Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load TTS model: {str(e)}")

@app.on_event("startup")
def startup_event():
    if os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        try:
            get_tts_engine()
        except Exception as e:
            print(f"[Speak Startup Warning] {e}")

@app.get("/health")
def health():
    loaded = tts_engine is not None
    gpu = torch.cuda.is_available()
    return {
        "status": "healthy" if loaded else "unloaded",
        "service": "indic-speak",
        "model_dir": MODEL_DIR,
        "cuda_available": gpu,
        "device_name": torch.cuda.get_device_name(0) if gpu else "cpu"
    }

class SpeechRequest(BaseModel):
    model: str = "indic-speak"
    input: str = Field(..., description="The text to synthesize")
    voice: Optional[str] = Field("Amit", description="Speaker name (e.g. Amit, Ishita, Ankur)")
    style: Optional[str] = Field("", description="Optional emotion/style")
    response_format: Optional[str] = Field("wav", description="Audio format: wav")
    speed: Optional[float] = Field(1.0, description="Speed adjustment")

@app.post("/v1/audio/speech")
def generate_speech(req: SpeechRequest):
    engine = get_tts_engine()
    
    text = req.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Input text cannot be empty.")
    
    speaker = req.voice or "Amit"
    style = req.style or ""
    
    try:
        # Generate audio numpy array @ 24kHz
        wav_array = engine(text, speaker=speaker, style=style)
        
        # Write to in-memory buffer as WAV
        buf = io.BytesIO()
        sf.write(buf, wav_array, 24000, format='WAV')
        buf.seek(0)
        
        return Response(
            content=buf.read(),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS synthesis error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8003, reload=False)
