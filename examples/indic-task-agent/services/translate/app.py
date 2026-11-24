import os
import sys
import time
import uuid
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL_DIR = os.getenv("MODEL_DIR", "/models/indic-translate")
SCRIPTS_DIR = os.path.join(MODEL_DIR, "scripts")
if os.path.exists(SCRIPTS_DIR) and SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

try:
    from prompt_contract import build_conversation, resolve_language, STOP_STRINGS, LANGUAGE_NAMES
except ImportError:
    STOP_STRINGS = ["<turn|>"]
    def resolve_language(lang: str) -> str:
        return lang
    def build_conversation(text: str, tgt_lang: str) -> list:
        return [{"role": "user", "content": f"Translate the following text into {tgt_lang}:\n\n{text}"}]

# Comprehensive mapping of ISO 639-1 / 639-3 codes to canonical prompt names
ISO_TO_PROMPT_NAME: Dict[str, str] = {
    "as": "Assamese",
    "asm": "Assamese",
    "bn": "Bengali",
    "ben": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "gom": "Konkani",
    "gu": "Gujarati",
    "guj": "Gujarati",
    "hi": "Hindi",
    "hin": "Hindi",
    "kn": "Kannada",
    "kan": "Kannada",
    "ks": "Kashmiri (Perso-Arabic script)",
    "kas": "Kashmiri (Perso-Arabic script)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mal": "Malayalam",
    "mni": "Manipuri (Meitei script)",
    "mr": "Marathi",
    "mar": "Marathi",
    "ne": "Nepali",
    "npi": "Nepali",
    "or": "Odia",
    "ory": "Odia",
    "pa": "Punjabi",
    "pan": "Punjabi",
    "sa": "Sanskrit",
    "san": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi (Devanagari script)",
    "snd": "Sindhi (Devanagari script)",
    "ta": "Tamil",
    "tam": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
    "ur": "Urdu",
    "urd": "Urdu",
    "en": "English",
    "eng": "English",
}

app = FastAPI(title="Bodhan Indic-Translate Service", version="1.0.0")

processor = None
model = None

def get_translate_engine():
    global processor, model
    if processor is not None and model is not None:
        return processor, model

    if not os.path.exists(MODEL_DIR) or not os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        raise HTTPException(
            status_code=503,
            detail=f"Model directory '{MODEL_DIR}' not found or empty. Please download weights first."
        )

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Translate] Loading Indic-Translate from {MODEL_DIR} onto {device} (bfloat16, attn=eager)...")
        processor = AutoProcessor.from_pretrained(MODEL_DIR)
        if getattr(processor, "tokenizer", None) is not None:
            processor.tokenizer.padding_side = "left"

        model = AutoModelForImageTextToText.from_pretrained(
            MODEL_DIR,
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None,
            attn_implementation="eager"
        )
        model.eval()
        model.generation_config.use_cache = True
        print("[Translate] Model loaded successfully.")
        return processor, model
    except Exception as e:
        print(f"[Translate] Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load translation model: {str(e)}")

@app.on_event("startup")
def startup_event():
    if os.path.exists(os.path.join(MODEL_DIR, "config.json")):
        try:
            get_translate_engine()
        except Exception as e:
            print(f"[Translate Startup Warning] {e}")

@app.get("/health")
def health():
    loaded = model is not None
    gpu = torch.cuda.is_available()
    return {
        "status": "healthy" if loaded else "unloaded",
        "service": "indic-translate",
        "model_dir": MODEL_DIR,
        "cuda_available": gpu,
        "device_name": torch.cuda.get_device_name(0) if gpu else "cpu"
    }

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "indic-translate"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.0
    top_p: Optional[float] = 1.0
    max_tokens: Optional[int] = 4096
    stream: Optional[bool] = False

class TranslationRequest(BaseModel):
    text: str
    target_lang: str
    source_lang: Optional[str] = "en"

def do_translate(text: str, target_lang: str, max_new_tokens: int = 4096, temperature: float = 0.0) -> str:
    proc, mdl = get_translate_engine()
    
    clean_tgt = target_lang.strip().lower()
    prompt_lang_name = ISO_TO_PROMPT_NAME.get(clean_tgt, target_lang)
    try:
        resolved_tgt = resolve_language(prompt_lang_name)
    except Exception:
        resolved_tgt = prompt_lang_name

    conversations = [build_conversation(text, resolved_tgt)]
    inputs = proc.apply_chat_template(
        conversations,
        add_generation_prompt=True,
        tokenize=True,
        padding=True,
        return_dict=True,
        return_tensors="pt"
    ).to(mdl.device)

    prompt_len = inputs["input_ids"].shape[-1]
    gen_kwargs = {
        "max_new_tokens": max_new_tokens,
        "use_cache": True,
        "stop_strings": STOP_STRINGS,
        "tokenizer": proc.tokenizer,
        "do_sample": False if temperature == 0.0 else True,
    }
    if temperature > 0.0:
        gen_kwargs["temperature"] = temperature

    with torch.inference_mode():
        output_ids = mdl.generate(**inputs, **gen_kwargs)

    completions = output_ids[:, prompt_len:]
    decoded = proc.batch_decode(completions, skip_special_tokens=True)
    return decoded[0].strip() if decoded else ""

@app.post("/v1/translations")
def translate_direct(req: TranslationRequest):
    if not req.text.strip():
        return {
            "translated_text": "",
            "source_lang": req.source_lang,
            "target_lang": req.target_lang
        }

    translated = do_translate(req.text, req.target_lang)
    return {
        "translated_text": translated,
        "source_lang": req.source_lang,
        "target_lang": req.target_lang
    }

@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    user_msg = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not user_msg:
        raise HTTPException(status_code=400, detail="No user message found in messages list.")

    proc, mdl = get_translate_engine()
    conversations = [[{"role": "user", "content": user_msg}]]
    inputs = proc.apply_chat_template(
        conversations,
        add_generation_prompt=True,
        tokenize=True,
        padding=True,
        return_dict=True,
        return_tensors="pt"
    ).to(mdl.device)

    prompt_len = inputs["input_ids"].shape[-1]
    with torch.inference_mode():
        output_ids = mdl.generate(
            **inputs,
            max_new_tokens=req.max_tokens or 4096,
            use_cache=True,
            stop_strings=STOP_STRINGS,
            tokenizer=proc.tokenizer,
            do_sample=False
        )

    completions = output_ids[:, prompt_len:]
    decoded = proc.batch_decode(completions, skip_special_tokens=True)
    translated_text = decoded[0].strip() if decoded else ""

    created_time = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_time,
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": translated_text
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": len(user_msg.split()),
            "completion_tokens": len(translated_text.split()),
            "total_tokens": len(user_msg.split()) + len(translated_text.split())
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8002, reload=False)
