import os
import sys
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import torch

MODEL_DIR = os.getenv("MODEL_DIR", "/models/indic-ocr")
if os.path.exists(MODEL_DIR) and MODEL_DIR not in sys.path:
    sys.path.insert(0, MODEL_DIR)

app = FastAPI(title="Bodhan Indic-OCR Service", version="1.0.0")

ocr_parser = None

def get_ocr_parser():
    global ocr_parser
    if ocr_parser is not None:
        return ocr_parser

    if not os.path.exists(MODEL_DIR) or not os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        raise HTTPException(
            status_code=503,
            detail=f"Model directory '{MODEL_DIR}' not found or empty. Please download weights first."
        )

    try:
        from indic_ocr import IndicOCR
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[OCR] Loading Indic-OCR from {MODEL_DIR} onto {device}...")
        ocr_parser = IndicOCR.from_pretrained(MODEL_DIR)
        # Pre-warm layout and recognizer models onto GPU
        _ = ocr_parser.layout
        _ = ocr_parser.recognizer
        print("[OCR] Model loaded successfully.")
        return ocr_parser
    except Exception as e:
        print(f"[OCR] Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load OCR model: {str(e)}")

@app.on_event("startup")
def startup_event():
    if os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        try:
            get_ocr_parser()
        except Exception as e:
            print(f"[OCR Startup Warning] {e}")

@app.get("/health")
def health():
    loaded = ocr_parser is not None
    gpu = torch.cuda.is_available()
    return {
        "status": "healthy" if loaded else "unloaded",
        "service": "indic-ocr",
        "model_dir": MODEL_DIR,
        "cuda_available": gpu,
        "device_name": torch.cuda.get_device_name(0) if gpu else "cpu"
    }

@app.post("/v1/ocr")
async def run_ocr(
    file: UploadFile = File(...)
):
    parser = get_ocr_parser()
    
    suffix = Path(file.filename or "page.png").suffix or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        page_result = parser.parse(tmp_path)
        return JSONResponse(page_result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR parsing error: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8004, reload=False)
