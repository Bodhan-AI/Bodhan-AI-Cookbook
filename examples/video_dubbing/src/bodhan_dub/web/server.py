"""Thin web UI over the same pipeline. No dubbing logic lives here: it uploads, starts `run_pipeline`, reads the
logs/report/segments the pipeline wrote, and saves edits as per-segment overrides that the next (cached) run applies.
"""
import hmac
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import audio, config, logs, textutil
from .. import bodhan as bodhan_mod
from ..bodhan import BodhanClient, BodhanError
from ..llm import Brain
from ..pipeline import Options, run_pipeline, speak_pieces

DATA = Path(os.environ.get("BODHAN_DUB_WEB_DATA", "./webdata")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).parent
app = FastAPI(title="Bodhan Dub Studio")
ACCESS_TOKEN = os.environ.get("DUB_ACCESS_TOKEN", "")          # set it whenever the server is public
CORS_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("DUB_CORS_ORIGINS", "").split(",") if o.strip()]


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Everything that spends Bodhan credit or lists projects needs the token. Media GETs (/file/) are authorised by
    the unguessable project id so <video>/<audio> tags work cross-origin without headers."""
    p = request.url.path
    open_ = request.method in ("OPTIONS", "HEAD") or p in ("/", "/config.json", "/api/health") or (request.method == "GET" and p == "/api/diagnose") or (
        request.method == "GET" and p.startswith("/api/projects/") and "/file/" in p)
    if ACCESS_TOKEN and p.startswith("/api") and not open_:
        if not hmac.compare_digest(request.headers.get("x-access-token", ""), ACCESS_TOKEN):
            return JSONResponse({"detail": "Access token required"}, status_code=401)
    return await call_next(request)


if CORS_ORIGINS:      # added last so it is the outermost layer and answers preflights first
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
                       allow_headers=["Content-Type", "X-Access-Token"], max_age=600)


def _startup_log() -> None:
    k = config.Keys.from_env()
    logs.log("server_start", base_url=config.BASE_URL, transcribe_key="set" if k.transcribe else "MISSING",
             translate_key="set" if k.translate else "MISSING", speak_key="set" if k.speak else "MISSING",
             llm=f"{os.environ.get('LLM_MODEL') or 'not configured'}", access_token="set" if ACCESS_TOKEN else "NOT SET (open to anyone)",
             cors=",".join(CORS_ORIGINS) or "none", concurrency=os.environ.get("DUB_CONCURRENCY", "2"), data_dir=str(DATA))


STARTED = time.time()
JOB_LOCK = threading.Lock()      # the logger is process-wide, so jobs run one at a time
JOBS: dict[str, dict] = {}
OVERRIDE_FIELDS = {"text", "english", "voice", "style", "speed", "max_stretch"}
STYLE_CHOICES = set(config.STYLES) | {"none"}      # "none" = explicitly neutral, even when the project has a style
MAX_VOICE_BYTES, MAX_VOICE_SECONDS = 25 << 20, 60.0


def pdir(pid: str) -> Path:
    if not pid.isalnum():
        raise HTTPException(404)
    d = DATA / pid
    if not d.exists():
        raise HTTPException(404, "project not found")
    return d


def check_lang(lang: str) -> str:
    if lang not in config.LANGUAGES:
        raise HTTPException(404, "unknown language")
    return lang


def meta(pid: str) -> dict:
    return json.loads((pdir(pid) / "meta.json").read_text())


def save_meta(pid: str, m: dict) -> None:
    textutil.atomic_write(DATA / pid / "meta.json", json.dumps(m, ensure_ascii=False, indent=1))


def start_job(pid: str, langs: list[str], merge: bool) -> None:
    m = meta(pid)
    out = DATA / pid / "out"
    JOBS[pid] = {"state": "queued", "langs": langs, "error": "", "log_offset": 0, "started": time.time()}

    def work():
        with JOB_LOCK:
            job = JOBS[pid]
            job["state"] = "running"
            log_file = out / "run.log.jsonl"
            job["log_offset"] = log_file.stat().st_size if log_file.exists() else 0
            try:
                keys = config.Keys.from_env()
                if keys.missing():
                    raise RuntimeError("Missing environment variables: " + ", ".join(keys.missing()))
                out.mkdir(exist_ok=True)
                logs.setup(log_file, echo=True, tag=pid)
                conc = max(1, int(os.environ.get("DUB_CONCURRENCY") or m.get("concurrency") or 2))   # today's setting, not the one saved with the project
                client = BodhanClient(keys, out / "work" / "cache", max_concurrency=conc)
                gl, vo = DATA / pid / "glossary.yaml", DATA / pid / "voices.yaml"
                opt = Options(input=DATA / pid / m["input"], langs=langs, out=out, glossary=gl if gl.exists() else None,
                              voices=vo if vo.exists() else None, code_mixed=m.get("code_mixed", False),
                              concurrency=conc, style=m.get("style") or None, merge=merge, ocr=bool(m.get("ocr")))
                logs.log("job_start", langs=",".join(langs), merge=merge, concurrency=conc, llm=Brain().available)
                rc = run_pipeline(opt, client, Brain())
                logs.log("job_end", exit_code=rc)
                job["state"] = "done" if rc == 0 else "finished_with_errors"
                if rc:
                    try:
                        job["error"] = "; ".join(json.loads((out / "report.json").read_text()).get("errors", []))[:400]
                    except Exception:
                        pass
            except Exception as e:  # surface any failure to the UI instead of dying silently
                job["state"], job["error"] = "error", str(e)[:400]
                logs.log("job_crashed", error=f"{type(e).__name__}: {e}")
            job["finished"] = time.time()

    threading.Thread(target=work, daemon=True).start()


def read_log(pid: str) -> list[dict]:
    f = DATA / pid / "out" / "run.log.jsonl"
    job = JOBS.get(pid)
    if not f.exists() or not job:
        return []
    with open(f, "rb") as fh:
        fh.seek(job["log_offset"])
        raw = fh.read().decode("utf-8", "ignore")
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def progress(pid: str) -> dict:
    ev = read_log(pid)
    stages = [e["stage"] for e in ev if e.get("event") == "stage"]
    langs: dict[str, dict] = {}
    for e in ev:
        if "lang" not in e:
            continue
        L = langs.setdefault(e["lang"], {"attempts": 0, "idx": set(), "stage": "translate+speak", "total": None, "retries": 0})
        if e["event"] == "segment_attempt":
            L["idx"].add(e["idx"])
            L["attempts"] += 1
        elif e["event"] == "lang_stage":
            L["stage"] = e["stage"]
            if e["stage"] == "segments":
                L["total"] = e.get("total")
        elif e["event"] in ("condensed", "segment_failed"):
            L["retries"] += 1
        elif e["event"] == "language_failed":
            L["stage"] = "failed"
    for L in langs.values():
        L["done"] = len(L.pop("idx"))
    retries = [e for e in ev if e.get("event") == "api_retry"]
    last = retries[-1] if retries else {}
    last_retry = f'{last.get("api", "")}: ' + (f'HTTP {last["status"]}' if last.get("status") else str(last.get("error", ""))) if last else ""
    return {"source_stages": stages, "languages": langs, "api_retries": len(retries), "last_retry": last_retry}


# ------------------------------------------------------------------ API
_startup_log()


@app.get("/api/health")
def health():
    return {"ok": True, "auth": bool(ACCESS_TOKEN), "uptime_s": int(time.time() - STARTED)}


@app.get("/config.json")
def config_json():
    return {"api": ""}          # the page is served by this backend, so API calls go to the same origin


@app.get("/api/diagnose")
@app.post("/api/diagnose")
def diagnose(request: Request):
    """Tiny live test of the Bodhan connection, run from the server, so key/endpoint problems are visible in the UI."""
    import httpx
    if request.method == "GET" and ACCESS_TOKEN and not hmac.compare_digest(request.query_params.get("token", ""), ACCESS_TOKEN):
        raise HTTPException(401, "Add ?token=YOUR_ACCESS_TOKEN to the address")   # browser-friendly form of the same check
    keys = config.Keys.from_env()
    base = config.BASE_URL.rstrip("/")

    def fp(k: str) -> dict:
        return {"set": bool(k), "length": len(k), "last4": k[-4:] if k else "", "has_whitespace": k != k.strip() or " " in k}

    out: dict = {"base_url": base, "ocr_key_set": bool(keys.ocr), "keys": {"transcribe": fp(keys.transcribe), "translate": fp(keys.translate), "speak": fp(keys.speak)},
                 "translate_key_same_as_transcribe": bool(keys.translate) and keys.translate == keys.transcribe,
                 "translate_key_same_as_speak": bool(keys.translate) and keys.translate == keys.speak, "tests": []}
    for path in bodhan_mod.BodhanClient.TRANSLATE_ROUTES:
        try:
            r = httpx.post(base + path, headers={"Authorization": f"Bearer {keys.translate.strip()}"}, timeout=30,
                           json=bodhan_mod.BodhanClient._translate_body(path, "Hello", "hi", "native"))
            out["tests"].append({"call": "translate POST " + path, "status": r.status_code, "body": r.text[:300],
                                 "request_id": r.headers.get("x-request-id", ""),
                                 "headers": {k: v for k, v in r.headers.items() if k.lower().startswith(("retry-after", "x-ratelimit"))}})
        except Exception as e:
            out["tests"].append({"call": "translate POST " + path, "error": f"{type(e).__name__}: {e}"[:200]})
    # Which key actually serves translate? Try each variable's key on /translate (a few tokens each).
    out["translate_with_each_key"] = {}
    for name, k in (("BODHAN_TRANSCRIBE_KEY", keys.transcribe), ("BODHAN_TRANSLATE_KEY", keys.translate), ("BODHAN_SPEAK_KEY", keys.speak)):
        try:
            r = httpx.post(base + "/translate", headers={"Authorization": f"Bearer {k.strip()}"}, timeout=30,
                           json={"text": "Hello", "target_language": "hi"})
            out["translate_with_each_key"][name] = {"status": r.status_code, "server": r.headers.get("server", ""),
                                                    "has_request_id": bool(r.headers.get("x-request-id")), "body": r.text[:120]}
        except Exception as e:
            out["translate_with_each_key"][name] = {"error": f"{type(e).__name__}: {e}"[:120]}
    try:
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        r = httpx.post(base + "/v1/audio/transcriptions", headers={"Authorization": f"Bearer {keys.transcribe.strip()}"}, timeout=60,
                       files={"file": ("probe.wav", buf.getvalue(), "audio/wav")}, data={"model": "indic-transcribe", "language": "en"})
        out["tests"].append({"call": "transcribe (1 s of silence)", "status": r.status_code, "body": r.text[:300],
                             "request_id": r.headers.get("x-request-id", ""),
                             "headers": {k: v for k, v in r.headers.items() if k.lower().startswith(("retry-after", "x-ratelimit"))}})
    except Exception as e:
        out["tests"].append({"call": "transcribe", "error": f"{type(e).__name__}: {e}"[:200]})
    if keys.ocr:
        try:
            import base64
            import io as _io

            from PIL import Image
            im = Image.new("RGB", (96, 48), "white")
            b = _io.BytesIO()
            im.save(b, format="PNG")
            r = httpx.post(base + "/v1/chat/completions", headers={"Authorization": f"Bearer {keys.ocr.strip()}"}, timeout=90,
                           json={"model": "indic-ocr", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {
                               "url": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()}}]}]})
            out["tests"].append({"call": "ocr (blank image)", "status": r.status_code, "body": r.text[:200],
                                 "request_id": r.headers.get("x-request-id", "")})
        except Exception as e:
            out["tests"].append({"call": "ocr", "error": f"{type(e).__name__}: {e}"[:200]})
    try:
        c = BodhanClient(keys, None, max_attempts=1, backoff=0)
        wav = c.speak("नमस्ते", config.DEFAULT_VOICES["male"], "hi")
        out["tests"].append({"call": "speak", "status": 200, "body": f"{len(wav)} bytes of audio"})
    except Exception as e:
        out["tests"].append({"call": "speak", "error": str(e)[:300]})
    return out


@app.get("/api/config")
def get_config():
    keys = config.Keys.from_env()
    return {"languages": [{"code": k, "label": v} for k, v in config.LANGUAGES.items()], "voices": config.VOICES,
            "styles": config.STYLES, "missing_keys": keys.missing(), "ocr_key": bool(keys.ocr), "llm": Brain().available,
            "stretch": [config.STRETCH_MIN, config.STRETCH_MAX], "default_langs": ["hi", "ta", "bn"]}


@app.get("/api/projects")
def list_projects():
    out = []
    for d in sorted(DATA.iterdir(), key=lambda p: -p.stat().st_mtime):
        if (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            out.append({**m, "id": d.name, "job": JOBS.get(d.name, {}).get("state", "idle")})
    return out


@app.post("/api/projects")
async def create_project(file: UploadFile = File(...), langs: str = Form("hi,ta,bn"), name: str = Form(""),
                         code_mixed: bool = Form(False), style: str = Form(""), ocr: bool = Form(False)):
    lang_list = [x for x in (s.strip().lower() for s in langs.split(",")) if x]
    bad = [x for x in lang_list if x not in config.LANGUAGES]
    if not lang_list or bad:
        raise HTTPException(400, f"Choose at least one supported language. Unknown: {bad}")
    if style and style not in config.STYLES:
        raise HTTPException(400, "Unknown style")
    ext = Path(file.filename or "v.mp4").suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm"):
        raise HTTPException(400, "Upload an MP4, MOV, MKV or WebM video")
    pid = uuid.uuid4().hex[:10]
    d = DATA / pid
    d.mkdir()
    with open(d / f"input{ext}", "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    m = {"name": name or Path(file.filename or "video").stem, "input": f"input{ext}", "langs": lang_list, "code_mixed": code_mixed,
         "style": style, "ocr": ocr, "created": time.time(), "concurrency": int(os.environ.get("DUB_CONCURRENCY", "2"))}
    save_meta(pid, m)
    start_job(pid, lang_list, merge=False)
    return {"id": pid, **m}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    d = pdir(pid)
    out = {"id": pid, **meta(pid), "job": JOBS.get(pid, {"state": "idle"}), "progress": progress(pid)}
    for key, fn in (("report", "out/report.json"), ("transcript", "out/transcript.en.json")):
        f = d / fn
        out[key] = json.loads(f.read_text()) if f.exists() else None
    gl, vo = d / "glossary.yaml", d / "voices.yaml"
    out["user_glossary"] = (yaml.safe_load(gl.read_text()) or {}).get("terms", []) if gl.exists() else []
    out["voices_cfg"] = yaml.safe_load(vo.read_text()) if vo.exists() else {}
    return out


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    shutil.rmtree(pdir(pid))
    JOBS.pop(pid, None)
    return {"ok": True}


@app.get("/api/projects/{pid}/lang/{lang}")
def get_lang(pid: str, lang: str):
    f = pdir(pid) / "out" / check_lang(lang) / f"segments.{lang}.json"
    if not f.exists():
        raise HTTPException(404, "not ready")
    ov = pdir(pid) / "out" / lang / "overrides.json"
    on = pdir(pid) / "out" / lang / f"onscreen.{lang}.json"
    return {**json.loads(f.read_text()), "overrides": json.loads(ov.read_text()) if ov.exists() else {},
            "onscreen": json.loads(on.read_text()) if on.exists() else None}


def busy(pid: str) -> bool:
    return JOBS.get(pid, {}).get("state") in ("queued", "running")


def seg_count(d: Path, lang: str) -> int:
    f = d / f"segments.{lang}.json"
    return len(json.loads(f.read_text()).get("segments", [])) if f.exists() else 0


def read_ov(f: Path) -> dict:
    return json.loads(f.read_text()) if f.exists() else {}


@app.patch("/api/projects/{pid}/lang/{lang}/segments/{idx}")
def edit_segment(pid: str, lang: str, idx: int, body: dict = Body(...)):
    """Merge the fields sent into this line's overrides. Fields not sent are kept; names in "unset" are removed."""
    if busy(pid):
        raise HTTPException(409, "A run is in progress; wait for it to finish")
    d = pdir(pid) / "out" / check_lang(lang)
    clean = {k: v for k, v in body.items() if k in OVERRIDE_FIELDS and v not in (None, "")}
    if "speed" in clean:
        clean["speed"] = min(config.STRETCH_MAX, max(config.STRETCH_MIN, float(clean["speed"])))
    if "style" in clean and clean["style"] not in STYLE_CHOICES:
        raise HTTPException(400, "Unknown style")
    unset = [k for k in (body.get("unset") or []) if k in OVERRIDE_FIELDS | {"custom_audio"}]
    f = d / "overrides.json"
    ov = read_ov(f)
    if body.get("reset"):
        ov.pop(str(idx), None)
        (d / "custom" / f"seg{idx}.wav").unlink(missing_ok=True)
    else:
        cur = ov.get(str(idx), {})
        if "english" in clean and "text" not in clean:
            cur.pop("text", None)             # new English and no hand-typed text: translate again
        for k in unset:
            cur.pop(k, None)
        if "custom_audio" in unset:
            (d / "custom" / f"seg{idx}.wav").unlink(missing_ok=True)
        ov[str(idx)] = {**cur, **clean}
    textutil.atomic_write(f, json.dumps(ov, ensure_ascii=False, indent=1))
    start_job(pid, [lang], merge=True)
    return {"ok": True}


@app.patch("/api/projects/{pid}/lang/{lang}/onscreen/{oid}")
def edit_onscreen(pid: str, lang: str, oid: int, body: dict = Body(...)):
    """Fix, hide or restore one translated on-screen text. Stored as overrides key "o<id>"; the language re-runs from cache."""
    if busy(pid):
        raise HTTPException(409, "A run is in progress; wait for it to finish")
    d = pdir(pid) / "out" / check_lang(lang)
    f = d / f"onscreen.{lang}.json"
    if not f.exists() or not any(i["id"] == oid for i in json.loads(f.read_text()).get("items", [])):
        raise HTTPException(404, "no such on-screen text")
    ov = read_ov(d / "overrides.json")
    if body.get("reset"):
        ov.pop(f"o{oid}", None)
    else:
        cur = ov.get(f"o{oid}", {})
        text = str(body.get("text") or "").strip()
        if text:
            cur["text"] = text[:500]
        if "hide" in body:
            cur["hide"] = bool(body["hide"])
            if not cur["hide"]:
                cur.pop("hide")
        ov[f"o{oid}"] = cur
    textutil.atomic_write(d / "overrides.json", json.dumps(ov, ensure_ascii=False, indent=1))
    start_job(pid, [lang], merge=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/lang/{lang}/segments/{idx}/voice")
async def upload_voice(pid: str, lang: str, idx: int, file: UploadFile = File(...)):
    """Use the person's own recording for one sentence. Any common audio format; converted to mono 24 kHz WAV."""
    if busy(pid):
        raise HTTPException(409, "A run is in progress; wait for it to finish")
    d = pdir(pid) / "out" / check_lang(lang)
    if not 0 <= idx < seg_count(d, lang):
        raise HTTPException(404, "no such sentence")
    cdir = d / "custom"
    cdir.mkdir(exist_ok=True)
    raw, dst = cdir / f"seg{idx}.upload", cdir / f"seg{idx}.wav"
    size = 0
    with open(raw, "wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_VOICE_BYTES:
                fh.close()
                raw.unlink(missing_ok=True)
                raise HTTPException(413, "Recording is larger than 25 MB")
            fh.write(chunk)
    try:
        tmp = cdir / f"seg{idx}.new.wav"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(tmp)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode or not tmp.exists():
            raise HTTPException(400, "Could not read that audio file. Use WAV, MP3, M4A, OGG or WebM.")
        dur = audio.probe_duration(tmp)
        x, _ = sf.read(str(tmp), dtype="float32")
        if dur < 0.3 or dur > MAX_VOICE_SECONDS:
            raise HTTPException(400, f"Recording is {dur:.1f} s; it must be between 0.3 and {MAX_VOICE_SECONDS:.0f} s")
        if float(np.sqrt(np.mean(x ** 2))) < 0.003:
            raise HTTPException(400, "The recording is silent. Check the microphone and try again.")
        tmp.replace(dst)
    finally:
        raw.unlink(missing_ok=True)
        (cdir / f"seg{idx}.new.wav").unlink(missing_ok=True)
    f = d / "overrides.json"
    ov = read_ov(f)
    ov[str(idx)] = {**ov.get(str(idx), {}), "custom_audio": f"custom/seg{idx}.wav"}
    textutil.atomic_write(f, json.dumps(ov, ensure_ascii=False, indent=1))
    start_job(pid, [lang], merge=True)
    return {"ok": True, "seconds": round(dur, 2)}


@app.post("/api/projects/{pid}/lang/{lang}/preview")
def preview_voice(pid: str, lang: str, body: dict = Body(...)):
    """Hear a line in a chosen voice and style without re-running the video (one cached Indic-Speak call)."""
    d = pdir(pid)
    check_lang(lang)
    text, voice, style = str(body.get("text") or "").strip(), str(body.get("voice") or ""), body.get("style") or None
    voices = {v for g in config.VOICES.values() for k in g.values() for v in k}
    if not text or len(text) > 400:
        raise HTTPException(400, "Text must be 1-400 characters")
    if voice not in voices:
        raise HTTPException(400, "Unknown voice")
    if style in ("none", ""):
        style = None
    if style and style not in config.STYLES:
        raise HTTPException(400, "Unknown style")
    keys = config.Keys.from_env()
    if not keys.speak:
        raise HTTPException(503, "Missing environment variable: BODHAN_SPEAK_KEY")
    client = BodhanClient(keys, d / "out" / "work" / "cache", max_concurrency=1)
    try:
        wav = speak_pieces(client, text, voice, lang, style, 0, 0)
    except BodhanError as e:
        logs.log("preview_failed", error=str(e)[:200])
        raise HTTPException(502, str(e)[:300]) from e
    logs.log("preview", lang=lang, voice=voice, style=style or "-", chars=len(text))
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.put("/api/projects/{pid}/glossary")
def put_glossary(pid: str, body: dict = Body(...)):
    if busy(pid):
        raise HTTPException(409, "A run is in progress; wait for it to finish")
    terms = [{"term": t["term"].strip(), "pin": bool(t.get("pin", True)), "kind": t.get("kind", "term"),
              "translations": {k: v for k, v in (t.get("translations") or {}).items() if v and k in config.LANGUAGES}}
             for t in body.get("terms", []) if str(t.get("term", "")).strip()]
    (pdir(pid) / "glossary.yaml").write_text(yaml.safe_dump({"terms": terms}, allow_unicode=True))
    start_job(pid, meta(pid)["langs"], merge=True)
    return {"ok": True, "terms": len(terms)}


@app.put("/api/projects/{pid}/voices")
def put_voices(pid: str, body: dict = Body(...)):
    if busy(pid):
        raise HTTPException(409, "A run is in progress; wait for it to finish")
    spk = {k: v for k, v in (body.get("speakers") or {}).items() if v}
    (pdir(pid) / "voices.yaml").write_text(yaml.safe_dump({"speakers": spk}, allow_unicode=True))
    start_job(pid, meta(pid)["langs"], merge=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/rerun")
def rerun(pid: str):
    if busy(pid):
        raise HTTPException(409, "A run is in progress")
    start_job(pid, meta(pid)["langs"], merge=True)
    return {"ok": True}


@app.get("/api/projects/{pid}/file/{kind}")
def get_file(pid: str, kind: str, lang: str = "", idx: int = -1):
    d = pdir(pid)
    m = meta(pid)
    paths = {"original": d / m["input"], "report": d / "out" / "report.json", "transcript": d / "out" / "transcript.en.json",
             "log": d / "out" / "run.log.jsonl"}
    if kind in ("video", "srt", "segments", "clip", "mix", "custom"):
        check_lang(lang)
        paths.update({"video": d / "out" / lang / f"video.{lang}.mp4", "srt": d / "out" / lang / f"subtitles.{lang}.srt",
                      "segments": d / "out" / lang / f"segments.{lang}.json",
                      "clip": d / "out" / "work" / lang / f"seg{idx}.wav", "custom": d / "out" / lang / "custom" / f"seg{idx}.wav", "mix": d / "out" / "work" / lang / "mix.wav"})
    f = paths.get(kind)
    if not f or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")
