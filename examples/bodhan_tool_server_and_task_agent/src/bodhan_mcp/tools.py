from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

BODHAN_BASE = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")

_SUPPORTED_STT = {"as","bhb","bho","bn","brx","doi","en","gu","hi","kn","kok","ks","mai","ml","mni","mr","ne","or","pa","sa","sat","sd","ta","te","ur","bho","bho"}
_SUPPORTED_TRANSLATE = {"as","bn","brx","doi","en","gu","hi","kn","kok","ks","ks-Deva","mai","ml","mni","mni-Beng","mr","ne","or","pa","sa","sat","sd","sd-Arab","ta","te","ur"}
_VOICES = {"as":{"Prastuti","Ankur"},"bn":{"Ishita","Sourav"},"brx":{"Gwrbw","Sansuma"},"doi":{"Preeti","Sham"},"gu":{"Dhara","Parth"},"hi":{"Kavya","Suhani","Amit"},"kn":{"Deepika","Adarsh"},"kok":{"Anjali","Sandeep"},"ks":{"Zoon","Ishfaq"},"mai":{"Vaidehi","Madhukar"},"ml":{"Lakshmi","Kiran"},"mni":{"Thoibi","Chaoba"},"mr":{"Anagha","Chinmay"},"ne":{"Srijana","Sagar"},"or":{"Itishree","Akash"},"pa":{"Kaur","Manpreet"},"sa":{"Bharati","Aryaman"},"sat":{"Phulmani","Sibu"},"sd":{"Moomal","Rano"},"ta":{"Anitha","Arun"},"te":{"Sravani","Vamsi"},"ur":{"Saba","Zaid"},"en":{"en"}}

_CACHE: dict[str, Any] = {}
_SEM = asyncio.Semaphore(int(os.getenv("BODHAN_CONCURRENCY", "4")))


def _ok(tool: str, result: dict[str, Any], cost: float, latency_ms: int) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "result": result, "cost_inr": cost, "latency_ms": latency_ms}


def _error(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


def _key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _request(method: str, url: str, key: str, timeout: float = 55.0, **kwargs: Any) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    headers = {**headers, "Authorization": f"Bearer {key}"}
    for attempt in range(4):
        try:
            async with _SEM:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code in (408, 429) or response.status_code >= 500:
                if attempt < 3:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
            return response
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 3:
                raise
            await asyncio.sleep(0.5 * (2 ** attempt))
    raise RuntimeError("request failed")


def inspect_media(file: str) -> dict[str, Any]:
    p = Path(file)
    if not p.is_file():
        return _error("file_not_found", f"Input file does not exist: {file}")
    ext = p.suffix.lower()
    result: dict[str, Any] = {"file": str(p.resolve()), "size_bytes": p.stat().st_size, "kind": "unknown", "extension": ext}
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        try:
            with Image.open(p) as im:
                result.update(kind="image", width=im.width, height=im.height)
        except Exception as exc:
            result.update(kind="invalid_image", error=str(exc))
    elif ext == ".pdf":
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(str(p))
            result.update(kind="pdf", pages=len(doc))
        except Exception as exc:
            result.update(kind="invalid_pdf", error=str(exc))
    elif ext in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm", ".amr"}:
        result.update(kind="audio")
        if ext != ".wav":
            result["format_warning"] = "Convert to WAV for the most reliable transcription; MP3 and FLAC are documented as accepted."
    elif ext in {".txt", ".md", ".csv", ".json", ".srt", ".vtt"}:
        result.update(kind="text")
    elif ext == ".mp4":
        result.update(kind="video", note="Video translation is not directly supported. Extract audio only if the task is to transcribe the spoken track.")
    else:
        result.update(kind="unknown")
    return _ok("inspect_media", result, 0.0, 1)


def render_pdf_pages(file: str, output_dir: str, dpi: int = 150) -> dict[str, Any]:
    p = Path(file)
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return _error("invalid_pdf", f"PDF file not found or unsupported: {file}")
    dpi = max(72, min(dpi, 300))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(p))
        pages = []
        scale = dpi / 72
        for i, page in enumerate(doc):
            bitmap = page.render(scale=scale)
            image = bitmap.to_pillow()
            path = out / f"page-{i+1:04d}.png"
            image.save(path, "PNG")
            pages.append(str(path.resolve()))
        return _ok("render_pdf_pages", {"pages": pages, "page_count": len(pages)}, 0.0, int((time.perf_counter()-start)*1000))
    except Exception as exc:
        return _error("pdf_render_failed", str(exc), True)


def _audio_duration(path: Path) -> float:
    ffprobe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], capture_output=True, text=True)
    if ffprobe.returncode == 0:
        try:
            return float(ffprobe.stdout.strip())
        except ValueError:
            pass
    if path.suffix.lower() == ".wav":
        import wave
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    raise RuntimeError("Could not determine audio duration; ffprobe or WAV metadata is required")


def chunk_audio(file: str, output_dir: str, chunk_seconds: int = 30) -> dict[str, Any]:
    p = Path(file)
    if not p.is_file():
        return _error("file_not_found", f"Audio file does not exist: {file}")
    chunk_seconds = min(max(chunk_seconds, 1), 30)
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    try:
        duration = _audio_duration(p)
    except Exception as exc:
        return _error("duration_unknown", str(exc), False)
    if duration <= 30:
        wav = out / (p.stem + ".wav")
        if p.suffix.lower() == ".wav" and p.resolve() == wav.resolve():
            pass
        else:
            r = subprocess.run(["ffmpeg","-y","-v","error","-i",str(p),"-ar","16000","-ac","1",str(wav)], capture_output=True)
            if r.returncode:
                return _error("audio_conversion_failed", r.stderr.decode(errors="replace"), True)
        chunks = [str(wav.resolve())]
    else:
        chunks = []
        r = subprocess.run(["ffmpeg","-y","-v","error","-i",str(p),"-ar","16000","-ac","1","-f","segment","-segment_time",str(chunk_seconds),str(out/"chunk-%04d.wav")], capture_output=True)
        if r.returncode:
            return _error("audio_chunk_failed", r.stderr.decode(errors="replace"), True)
        chunks = [str(x.resolve()) for x in sorted(out.glob("chunk-*.wav"))]
    cost = duration / 60 * 0.10
    return _ok("chunk_audio", {"chunks": chunks, "duration_seconds": duration, "estimated_transcribe_cost_inr": round(cost, 6)}, cost, 1)


async def transcribe_audio(file: str, language: str | None = None) -> dict[str, Any]:
    p = Path(file)
    if not p.is_file():
        return _error("file_not_found", f"Audio file does not exist: {file}")
    if language and language not in _SUPPORTED_STT:
        return _error("unsupported_language", f"Unsupported transcription language: {language}")
    cache = _key("transcribe", str(p.resolve()), p.stat().st_mtime_ns, language)
    if cache in _CACHE:
        return _CACHE[cache]
    key = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
    if not key:
        return _error("missing_api_key", "Set BODHAN_TRANSCRIBE_KEY")
    start = time.perf_counter()
    data = {"model":"indic-transcribe"}
    if language: data["language"] = language
    with p.open("rb") as fp:
        response = await _request("POST", BODHAN_BASE+"/v1/audio/transcriptions", key, 60, data=data, files={"file":(p.name,fp,p.suffix.lower().replace(".","")+"/wav")})
    elapsed = int((time.perf_counter()-start)*1000)
    try:
        duration = _audio_duration(p)
        cost = duration/60*0.10
    except Exception:
        cost = 0.0
    if response.status_code != 200:
        return _error("bodhan_api_error", response.text[:1000], response.status_code in (429,500,502,503,504))
    text = response.json().get("text","")
    out = _ok("transcribe_audio", {"text":text,"language":language,"file":str(p.resolve())}, round(cost,6), elapsed)
    _CACHE[cache] = out
    return out


async def translate_text(text: str, target_language: str, script: str = "native", max_output_tokens: int | None = None) -> dict[str, Any]:
    if not text.strip(): return _error("invalid_request_error", "text must not be empty")
    if target_language not in _SUPPORTED_TRANSLATE: return _error("unsupported_language", f"Unsupported target language: {target_language}")
    if script not in {"native","roman","codemix"}: return _error("invalid_script", f"Unsupported script: {script}")
    if target_language == "en" and script != "native": return _error("invalid_script", "English does not support roman or codemix output")
    cache=_key("translate",text,target_language,script,max_output_tokens)
    if cache in _CACHE: return _CACHE[cache]
    key=os.getenv("BODHAN_TRANSLATE_KEY","")
    if not key: return _error("missing_api_key","Set BODHAN_TRANSLATE_KEY")
    target_names={"as":"Assamese","bn":"Bengali","brx":"Bodo","doi":"Dogri","en":"English","gu":"Gujarati","hi":"Hindi","kn":"Kannada","kok":"Konkani","ks":"Kashmiri","mai":"Maithili","ml":"Malayalam","mni":"Manipuri","mr":"Marathi","ne":"Nepali","or":"Odia","pa":"Punjabi","sa":"Sanskrit","sat":"Santali","sd":"Sindhi","ta":"Tamil","te":"Telugu","ur":"Urdu"}
    body={"model":"indic-translate","messages":[{"role":"user","content":f"Translate the following text into {target_names[target_language]} ({target_language}). Use {script} script. Preserve formatting and translate the complete text:\n{text}"}]}
    if max_output_tokens:
        body["max_tokens"] = max_output_tokens
    start=time.perf_counter()
    response=await _request("POST",BODHAN_BASE+"/v1/chat/completions",key,timeout=55,json=body)
    elapsed=int((time.perf_counter()-start)*1000)
    if response.status_code!=200:
        return _error("bodhan_api_error",response.text[:1000],response.status_code in (429,500,502,503,504))
    data=response.json()
    translation=data["choices"][0]["message"]["content"]
    usage=data.get("usage",{})
    normalized={"translation":translation,"target_language":target_language,"script":script,"truncated":data["choices"][0].get("finish_reason") not in (None,"stop","end_turn"),"usage":usage}
    out=_ok("translate_text",normalized,round(usage.get("completion_tokens",0)/10000*0.20,6),elapsed)
    _CACHE[cache]=out
    return out


async def synthesize_speech(text: str, language: str, voice: str, style: str | None = None) -> dict[str, Any]:
    if not text.strip(): return _error("invalid_request_error","text must not be empty")
    allowed=_VOICES.get(language)
    if not allowed: return _error("unsupported_language",f"Unsupported speech language: {language}")
    if voice not in allowed: return _error("invalid_voice",f"Voice {voice} is not available for {language}; choose one of {sorted(allowed)}")
    cache=_key("synthesize",text,language,voice,style)
    if cache in _CACHE: return _CACHE[cache]
    key=os.getenv("BODHAN_SPEAK_KEY","")
    if not key: return _error("missing_api_key","Set BODHAN_SPEAK_KEY")
    instructions={"lang":language}
    if style: instructions["style"]=style
    body={"model":"indic-speak","input":text,"voice":voice,"instructions":json.dumps(instructions,ensure_ascii=False)}
    start=time.perf_counter(); response=await _request("POST",BODHAN_BASE+"/v1/audio/speech",key,timeout=90,json=body)
    elapsed=int((time.perf_counter()-start)*1000)
    if response.status_code!=200:
        return _error("bodhan_api_error",response.text[:1000],response.status_code in (429,500,502,503,504))
    with tempfile.TemporaryDirectory() as td:
        wav=Path(td)/"speech.wav"; wav.write_bytes(response.content)
    fd, tmp=tempfile.mkstemp(suffix=".wav"); os.close(fd); Path(tmp).write_bytes(response.content)
    out=_ok("synthesize_speech",{"file":tmp,"format":"wav","sample_rate":24000,"channels":1,"language":language,"voice":voice,"ai_generated":True},round(len(text)/10000*6,6),elapsed)
    _CACHE[cache]=out
    return out


async def ocr_image(file: str, table_format: str = "markdown", max_tokens: int = 2048) -> dict[str, Any]:
    p=Path(file)
    if not p.is_file(): return _error("file_not_found",f"Image file does not exist: {file}")
    if p.suffix.lower() not in {".png",".jpg",".jpeg"}: return _error("unsupported_image_format","Indic-OCR accepts PNG or JPEG only")
    if table_format not in {"html","markdown"}: return _error("invalid_request_error","table_format must be html or markdown")
    cache=_key("ocr",str(p.resolve()),p.stat().st_mtime_ns,table_format,max_tokens)
    if cache in _CACHE: return _CACHE[cache]
    key=os.getenv("BODHAN_OCR_KEY","")
    if not key: return _error("missing_api_key","Set BODHAN_OCR_KEY")
    mime="image/png" if p.suffix.lower()==".png" else "image/jpeg"
    b64=base64.b64encode(p.read_bytes()).decode("ascii")
    body={"model":"indic-ocr","messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}]}],"table_format":table_format,"max_tokens":max_tokens}
    start=time.perf_counter(); response=await _request("POST",BODHAN_BASE+"/v1/chat/completions",key,timeout=90,json=body)
    elapsed=int((time.perf_counter()-start)*1000)
    if response.status_code!=200:
        return _error("bodhan_api_error",response.text[:1000],response.status_code in (429,502,500,503,504))
    data=response.json(); content=data["choices"][0]["message"]["content"]
    out=_ok("ocr_image",{"text":content,"blocks":data.get("blocks",[]),"usage":data.get("usage",{})},0.20,elapsed)
    _CACHE[cache]=out
    return out
