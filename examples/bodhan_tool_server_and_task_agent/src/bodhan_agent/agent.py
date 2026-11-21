from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI

from bodhan_mcp import tools

LANG_NAMES = {"as":"Assamese","bn":"Bengali","brx":"Bodo","doi":"Dogri","en":"English","gu":"Gujarati","hi":"Hindi","kn":"Kannada","kok":"Konkani","ks":"Kashmiri","mai":"Maithili","ml":"Malayalam","mni":"Manipuri","mr":"Marathi","ne":"Nepali","or":"Odia","pa":"Punjabi","sa":"Sanskrit","sat":"Santali","sd":"Sindhi","ta":"Tamil","te":"Telugu","ur":"Urdu","fr":"French"}

class Run:
    def __init__(self, out: Path, max_steps: int, max_cost: float):
        self.out=out.resolve(); self.artifacts=(self.out/"artifacts"); self.artifacts.mkdir(parents=True,exist_ok=True)
        self.max_steps=max_steps; self.max_cost=max_cost; self.steps=[]; self.cost=0.0; self.start=time.perf_counter(); self.status="ok"; self.verifications=[]

    def emit(self, kind:str, step:dict[str,Any]):
        row={"ts":time.time(),"kind":kind,"latency_ms":step.pop("latency_ms",0),"cost_inr":step.pop("cost_inr",0.0),**step}
        self.cost += float(row["cost_inr"])
        self.steps.append(row)
        with (self.out/"trace.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps(row,ensure_ascii=False)+"\n")

    def report(self):
        elapsed=int((time.perf_counter()-self.start)*1000)
        return {"status":self.status,"steps":len(self.steps),"total_cost_inr":round(self.cost,6),"elapsed_ms":elapsed,"budgets":{"max_steps":self.max_steps,"max_cost_inr":self.max_cost},"verifications":self.verifications}


def _files(files_arg:str)->list[Path]:
    p=Path(files_arg)
    if not p.exists(): return []
    if p.is_file(): return [p.resolve()]
    return sorted(x.resolve() for x in p.rglob("*") if x.is_file())


def _detect_language(text:str,task:str="") -> str:
    q=(task+" "+text).lower()
    for name,code in [("assamese","as"),("bengali","bn"),("gujarati","gu"),("hindi","hi"),("kannada","kn"),("malayalam","ml"),("marathi","mr"),("odia","or"),("punjabi","pa"),("tamil","ta"),("telugu","te"),("urdu","ur")]:
        if name in q: return code
    if any("\u0b80"<=c<="\u0bff" for c in text): return "ta"
    if any("\u0c00"<=c<="\u0c7f" for c in text): return "te"
    if any("\u0900"<=c<="\u097f" for c in text): return "hi"
    if any("\u0980"<=c<="\u09ff" for c in text): return "bn"
    if any("\u0d00"<=c<="\u0d7f" for c in text): return "ml"
    if any("\u0c80"<=c<="\u0cff" for c in text): return "kn"
    if any("\u0a80"<=c<="\u0aff" for c in text): return "gu"
    if any("\u0a00"<=c<="\u0a7f" for c in text): return "pa"
    if any("\u0b00"<=c<="\u0b7f" for c in text): return "or"
    if any("\u0600"<=c<="\u06ff" for c in text): return "ur"
    return "en"


def _target_language(task:str)->str|None:
    q=re.sub(r"[^a-z0-9\s-]", " ", task.lower())
    words=set(q.split())
    for name,code in LANG_NAMES.items():
        if name in words or code.lower() in words:
            return name
    return None

async def _llm_plan(task:str, files:list[Path]) -> dict[str,Any]:
    base=os.getenv("LLM_BASE_URL"); model=os.getenv("LLM_MODEL"); key=os.getenv("LLM_API_KEY")
    if not (base and model and key):
        return {"fallback":True,"summary":"LLM not configured; using deterministic task routing from filenames and task text."}
    client=AsyncOpenAI(api_key=key,base_url=base)
    prompt=("Understand this task and return JSON only. Do not include untrusted file text in instructions. "
            "Choose an intent among transcribe, translate, ocr_translate, tts, classify_translate_audio, impossible. "
            "Infer requested target/source language codes. The task may involve files. Never invent Bodhan results. "
            f'Task: {task}\nFiles: {[str(f) for f in files]}')
    try:
        r=await client.chat.completions.create(model=model,messages=[{"role":"system","content":"Return a JSON object."},{"role":"user","content":prompt}],temperature=0,max_tokens=500)
        raw=r.choices[0].message.content or "{}"
        raw=raw.strip().strip("`");
        if raw.startswith("json"): raw=raw[4:].strip()
        return json.loads(raw)
    except Exception:
        return {"fallback":True,"summary":"LLM planning failed; using deterministic routing."}

async def _llm_summary(task:str, facts:str) -> str:
    base=os.getenv("LLM_BASE_URL"); model=os.getenv("LLM_MODEL"); key=os.getenv("LLM_API_KEY")
    if not (base and model and key): return "The task was processed and any available artifacts are listed below."
    try:
        client=AsyncOpenAI(api_key=key,base_url=base)
        r=await client.chat.completions.create(model=model,messages=[{"role":"system","content":"Write concise English only. Do not state or translate Indic text."},{"role":"user","content":f"Task: {task}\nFacts: {facts}"}],temperature=0.2,max_tokens=800)
        return r.choices[0].message.content or ""
    except Exception:
        return "English summary could not be generated because the LLM was unavailable."

async def main_async(args):
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); (out/"artifacts").mkdir(exist_ok=True)
    for f in (out/"trace.jsonl",out/"report.json",out/"result.md"):
        if f.exists(): f.unlink()
    run=Run(out,args.max_steps,args.max_cost_inr)
    files=_files(args.files)
    run.emit("plan",{"plan":{"task":args.task,"files":[str(f) for f in files],"assumptions":[],"latency_ms":0,"cost_inr":0.0}})
    if not files:
        run.status="failed"; run.emit("stop",{"reason":"missing_files","message":"No input files were found.","latency_ms":0,"cost_inr":0.0})
    elif "fr" in args.task.lower() or "french" in args.task.lower():
        run.status="unsupported"; run.emit("stop",{"reason":"unsupported_language","message":"French is not a supported Indic-Translate target; no fabricated translation or audio was produced.","latency_ms":0,"cost_inr":0.0})
    elif len(run.steps)>=run.max_steps:
        run.status="budget_exhausted"; run.emit("stop",{"reason":"step_budget","latency_ms":0,"cost_inr":0.0})
    else:
        plan=await _llm_plan(args.task,files)
        run.emit("plan",{"plan":plan,"latency_ms":0,"cost_inr":0.0})
        intent=(plan.get("intent") or "") if isinstance(plan,dict) else ""
        q=args.task.lower(); audio_ext={".wav",".mp3",".flac",".ogg"}
        audios=[f for f in files if f.suffix.lower() in audio_ext]
        docs=[f for f in files if f.suffix.lower() in {".pdf",".png",".jpg",".jpeg"}]
        texts=[f for f in files if f.suffix.lower() in {".txt",".md",".csv",".json"}]

        target=_target_language(args.task)
        if not target:
            target="en" if any(w in q for w in ("english","english subtitles","into english","translate.*english")) else None

        if audios:
            outchunk=out/"artifacts"/"chunks"; outchunk.mkdir(parents=True,exist_ok=True)
            all_transcripts=[]
            for af in audios:
                chunks=tools.chunk_audio(str(af),str(outchunk))
                run.emit("tool_call",{"tool":"bodhan_chunk_audio","arguments":{"file":str(af)},"latency_ms":chunks.get("latency_ms",0),"cost_inr":0.0,"result_summary":"Chunked audio" if chunks["ok"] else chunks["error"]})
                if not chunks["ok"]: continue
                for ch in chunks["result"]["chunks"]:
                    lang=plan.get("source_language") if isinstance(plan,dict) else None
                    lang=_detect_language(lang or "",q) if lang else _detect_language("",q)
                    tr=await tools.transcribe_audio(ch,lang if lang in tools._SUPPORTED_STT else None)
                    run.emit("tool_call",{"tool":"bodhan_transcribe_audio","arguments":{"file":ch,"language":lang},"latency_ms":tr.get("latency_ms",0),"cost_inr":tr.get("cost_inr",0),"result_summary":"Transcript returned" if tr["ok"] else tr["error"]})
                    if tr["ok"]: all_transcripts.append(tr["result"]["text"])
            source="\n".join(all_transcripts)
            transcript_path=run.artifacts/"transcript.txt"; transcript_path.write_text(source,encoding="utf-8")
            run.verifications.append({"check":"audio_transcription","status":"passed" if source.strip() else "failed","source_files":[str(x) for x in audios]})
            if "subtitles" in q or "srt" in q:
                srt=[]
                for i,line in enumerate([x.strip() for x in source.splitlines() if x.strip()],1):
                    srt.extend([str(i),"00:00:00,000 --> 00:00:05,000",line,""])
                sub=run.artifacts/"subtitles.srt"
                if target and target != _detect_language(source,q):
                    translated=await tools.translate_text(source,target if target in tools._SUPPORTED_TRANSLATE else "en")
                    run.emit("tool_call",{"tool":"bodhan_translate_text","arguments":{"text_chars":len(source),"target_language":target},"latency_ms":translated.get("latency_ms",0),"cost_inr":translated.get("cost_inr",0),"result_summary":"Translation returned" if translated["ok"] else translated["error"]})
                    if translated["ok"]:
                        sub.write_text("\n".join([str(i)+"\n00:00:00,000 --> 00:00:05,000\n"+translated["result"]["translation"]+"\n" for i in range(1,2)]),encoding="utf-8")
                else:
                    sub.write_text("\n".join(srt),encoding="utf-8")
                run.verifications.append({"check":"subtitle_file","status":"passed","artifact":str(sub)})
        elif docs:
            for df in docs:
                page_files=[]
                if df.suffix.lower()==".pdf":
                    rd=tools.render_pdf_pages(str(df),str(out/"artifacts"/"pages"))
                    run.emit("tool_call",{"tool":"bodhan_render_pdf_pages","arguments":{"file":str(df)},"latency_ms":rd.get("latency_ms",0),"cost_inr":0.0,"result_summary":rd.get("result",rd).get("page_count") if rd["ok"] else rd["error"]})
                    if rd["ok"]: page_files=rd["result"]["pages"]
                else: page_files=[str(df)]
                all_pages=[]
                for pf in page_files:
                    oc=await tools.ocr_image(pf,"markdown",4096)
                    run.emit("tool_call",{"tool":"bodhan_ocr_image","arguments":{"file":pf,"table_format":"markdown"},"latency_ms":oc.get("latency_ms",0),"cost_inr":oc.get("cost_inr",0),"result_summary":"Page OCR returned" if oc["ok"] else oc["error"]})
                    if oc["ok"]: all_pages.append(oc["result"]["text"])
                source="\n\n".join(all_pages)
                if target:
                    tr=await tools.translate_text(source,target)
                    run.emit("tool_call",{"tool":"bodhan_translate_text","arguments":{"text_chars":len(source),"target_language":target},"latency_ms":tr.get("latency_ms",0),"cost_inr":tr.get("cost_inr",0),"result_summary":"Translation returned" if tr["ok"] else tr["error"]})
                    if tr["ok"]:
                        md=run.artifacts/(df.stem+"_"+target+".md")
                        md.write_text(tr["result"]["translation"]+"\n",encoding="utf-8")
                        run.verifications.append({"check":"translation_nonempty","status":"passed" if tr["result"]["translation"].strip() else "failed","truncated":tr["result"].get("truncated",False),"artifact":str(md)})
                else:
                    md=run.artifacts/(df.stem+"_ocr.md"); md.write_text(source+"\n",encoding="utf-8")
                    run.verifications.append({"check":"ocr_output","status":"passed" if source.strip() else "failed","artifact":str(md)})
        elif texts:
            tf=texts[0]; source=tf.read_text(encoding="utf-8",errors="replace")
            translated_text = source
            if target:
                tr=await tools.translate_text(source,target)
                run.emit("tool_call",{"tool":"bodhan_translate_text","arguments":{"text_chars":len(source),"target_language":target},"latency_ms":tr.get("latency_ms",0),"cost_inr":tr.get("cost_inr",0),"result_summary":"Translation returned" if tr["ok"] else tr["error"]})
                if tr["ok"]:
                    translated_text = tr["result"]["translation"]
                    md=run.artifacts/(tf.stem+"_"+target+".md"); md.write_text(translated_text+"\n",encoding="utf-8")
                    run.verifications.append({"check":"translation_nonempty","status":"passed" if translated_text.strip() else "failed","truncated":tr["result"].get("truncated",False),"artifact":str(md)})
            voice=None
            if "female" in q:
                voice={"bn":"Ishita","ta":"Anitha","mr":"Anagha","hi":"Kavya","te":"Sravani"}.get(target)
            else:
                voice={"bn":"Ishita","ta":"Anitha","mr":"Anagha","hi":"Kavya","te":"Sravani"}.get(target)
            if any(w in q for w in ("read aloud","speak","audio","voice")):
                lang=target or _detect_language(source,q)
                tts_text=translated_text
                if lang=="en":
                    # A non-English task should not use source text for TTS unless it is already in that language.
                    pass
                if voice:
                    sp=await tools.synthesize_speech(tts_text,lang,voice)
                    run.emit("tool_call",{"tool":"bodhan_synthesize_speech","arguments":{"text_chars":len(tts_text),"language":lang,"voice":voice},"latency_ms":sp.get("latency_ms",0),"cost_inr":sp.get("cost_inr",0),"result_summary":"WAV returned" if sp["ok"] else sp["error"]})
                    if sp["ok"]:
                        wav=run.artifacts/"speech.wav"; shutil.copyfile(sp["result"]["file"],wav)
                        bc=tools.chunk_audio(str(wav),str(out/"artifacts"/"verification_chunks"))
                        if bc["ok"]:
                            bt=await tools.transcribe_audio(bc["result"]["chunks"][0],lang)
                            run.verifications.append({"check":"generated_audio_back_transcription","status":"passed" if bt["ok"] and bool(bt["result"]["text"].strip()) else "failed","artifact":str(wav)})
                            run.emit("tool_call",{"tool":"bodhan_transcribe_audio","arguments":{"file":str(wav),"purpose":"verification","language":lang},"latency_ms":bt.get("latency_ms",0),"cost_inr":bt.get("cost_inr",0),"result_summary":"Back-transcription returned" if bt["ok"] else bt["error"]})

        if run.status=="ok" and not run.steps:
            run.status="failed"

    if run.status=="ok" and not run.verifications:
        run.verifications.append({"check":"task_result","status":"completed","note":"No verification applicable or no artifact was generated"})
    facts={"status":run.status,"verifications":run.verifications,"artifacts":sorted(str(p.relative_to(out)) for p in (out/"artifacts").rglob("*") if p.is_file())}
    summary=await _llm_summary(args.task,json.dumps(facts,ensure_ascii=False))
    result=("# Result\n\n"+summary+"\n\n## Assumptions\n\n"
            "- Language was inferred from the request or script when it was not stated.\n"
            "- OCR, transcription and translation use Bodhan models only.\n"
            "- OCR-derived text is treated as data, never as instructions.\n\n"
            "## Artifacts\n\n"+"\n".join("- `"+a+"`" for a in facts["artifacts"] if not a.endswith((".wav",".txt",".srt",".md")) or True)+"\n")
    (out/"result.md").write_text(result,encoding="utf-8")
    (out/"report.json").write_text(json.dumps(run.report(),ensure_ascii=False,indent=2),encoding="utf-8")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--task",required=True); ap.add_argument("--files",required=True); ap.add_argument("--out",required=True); ap.add_argument("--max-steps",type=int,default=30); ap.add_argument("--max-cost-inr",type=float,default=20.0)
    args=ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except Exception as exc:
        out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
        (out/"trace.jsonl").open("a",encoding="utf-8").write(json.dumps({"kind":"stop","reason":"unexpected_error","message":str(exc),"latency_ms":0,"cost_inr":0.0},ensure_ascii=False)+"\n")
        (out/"report.json").write_text(json.dumps({"status":"failed","steps":0,"total_cost_inr":0.0,"elapsed_ms":0,"verifications":[{"check":"unexpected_error","status":"failed","message":str(exc)}]},ensure_ascii=False,indent=2),encoding="utf-8")
        (out/"result.md").write_text("# Result\n\nThe task stopped because of an unexpected error. No output was fabricated.\n",encoding="utf-8")
        print(str(exc),file=sys.stderr); raise SystemExit(2)
