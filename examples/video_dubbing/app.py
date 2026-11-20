"""Web UI over the same pipeline: upload a video, bring your own keys, watch and download the dubs.

    streamlit run app.py

It runs `python -m bodhan_dub` as a subprocess with the user's keys in that process's environment
only: keys are never stored, and two users never share state. Progress is read from the run's
structured log (run.jsonl).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

SRC = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC))  # works with or without `pip install -e .` (e.g. on Streamlit Community Cloud)

from bodhan_dub.__main__ import LANGS  # noqa: E402
from bodhan_dub.bodhan import LANGUAGE_NAMES  # noqa: E402

st.set_page_config(page_title="Video dubbing · Bodhan AI Cookbook", page_icon=":material/translate:", layout="wide")
st.html("""<style>
.block-container {max-width: 1100px; padding-top: 4rem}  /* clears the toolbar Community Cloud draws on top */
.eyebrow {font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: #c94400}
.lede {color: #52525b; font-size: 1.02rem; max-width: 720px; margin: -.3rem 0 .6rem}
.step {display: flex; align-items: center; gap: 10px; font-weight: 600; margin: 1.1rem 0 .2rem}
.step b {display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%;
         background: #ff5500; color: #fff; font-size: 12px}
.cards {display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: .4rem 0 1.2rem}
.card {border: 1px solid #e7e7ea; border-radius: 14px; padding: 14px 16px; background: #fff;
       box-shadow: 0 1px 2px rgba(24,24,27,.05)}
.card span {font-size: 12px; color: #71717a; text-transform: uppercase; letter-spacing: .06em}
.card div {font-family: Poppins, sans-serif; font-weight: 600; font-size: 22px; margin-top: 2px}
table.targets {width: 100%; border-collapse: collapse; font-size: 14px}
table.targets td {padding: 9px 4px; border-bottom: 1px solid #efeff1}
table.targets td:first-child {white-space: nowrap; font-weight: 500}
table.targets td:last-child {text-align: right}
.pill {padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600}
.pill.met {background: rgba(15,122,78,.09); color: #0f7a4e}
.pill.missed {background: rgba(255,85,0,.09); color: #c94400}
.footer {text-align: center; font-size: 12px; color: #71717a; margin: 3rem 0 .5rem}
.footer a {color: #c94400; background: rgba(255,85,0,.09); border: 1px solid rgba(255,85,0,.35);
           padding: 1px 8px; border-radius: 999px; text-decoration: none; font-weight: 500}
.footer a:hover {background: rgba(255,85,0,.16)}
@media (max-width: 700px) {.cards {grid-template-columns: repeat(2, 1fr)}}
</style>""")

BROWSER_CODECS = {"h264", "vp8", "vp9", "av1"}  # what a browser's video player can show
TARGET_NAMES = {"segment_sync": "Segment sync", "speed_change": "Speed change", "coverage": "Coverage",
                "content_fidelity": "Content fidelity", "total_length": "Total length", "loudness": "Loudness"}


def step(n: int, title: str) -> None:
    st.html(f'<div class="step"><b>{n}</b>{title}</div>')


def keys_from_sidebar() -> dict:
    with st.sidebar:
        st.subheader("Your keys")
        st.caption("Used for this run only: passed to the dubbing process, never stored or logged. "
                   "Get Bodhan keys at [console.bodhan.ai](https://console.bodhan.ai).")
        env = {"BODHAN_API_KEY": st.text_input("Bodhan API key", type="password")}
        with st.expander("A separate key per model"):
            for model in ("transcribe", "translate", "speak"):
                env[f"BODHAN_{model.upper()}_KEY"] = st.text_input(f"Indic-{model.title()} key", type="password")
        st.subheader("LLM (optional)")
        st.caption("Any OpenAI-compatible endpoint. It only plans and checks English; it never writes Indian-language "
                   "text. Without it, the run still finishes: it uses the raw transcript sentences and speeds up "
                   "long lines instead of shortening them.")
        env["LLM_BASE_URL"] = st.text_input("Base URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
        env["LLM_MODEL"] = st.text_input("Model", "gemini-3.5-flash-lite")
        env["LLM_API_KEY"] = st.text_input("LLM API key", type="password")
    return env


def missing_keys(env: dict) -> list[str]:
    per_model = all(env[f"BODHAN_{m}_KEY"] for m in ("TRANSCRIBE", "TRANSLATE", "SPEAK"))
    return [] if env["BODHAN_API_KEY"] or per_model else ["Bodhan API key"]


def start_run(video, langs: list[str], env: dict, voice: str, music: str, keep: str, code_mixed: bool) -> dict:
    work = Path(tempfile.mkdtemp(prefix="dub-"))
    source = work / f"input{Path(video.name).suffix.lower()}"
    source.write_bytes(video.getbuffer())
    out = work / "out"
    cmd = [sys.executable, "-m", "bodhan_dub", "--input", str(source), "--langs", ",".join(langs),
           "--out", str(out), "--separate", music]
    terms = [t.strip() for t in keep.split(",") if t.strip()]
    if terms:
        (work / "glossary.yaml").write_text(yaml.safe_dump({"keep": terms}))
        cmd += ["--glossary", str(work / "glossary.yaml")]
    if voice != "auto":
        (work / "voices.yaml").write_text(yaml.safe_dump({lang: voice for lang in langs}))
        cmd += ["--voices", str(work / "voices.yaml")]
    if code_mixed:
        cmd.append("--code-mixed")
    # Never hand the server's own keys to a run: only what the user typed.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("BODHAN_", "LLM_"))}
    clean["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), clean.get("PYTHONPATH")]))
    with open(work / "console.log", "w") as log:
        proc = subprocess.Popen(cmd, env=clean | env, stdout=log, stderr=subprocess.STDOUT, cwd=Path(__file__).parent)
    return {"proc": proc, "out": out, "work": work, "langs": langs}


def progress(log: Path, langs: list[str]) -> tuple[float, str]:
    """Read run.jsonl: how far along the run is, and what it is doing."""
    units, label, frac = 0, "Reading the video", 0.03
    done = {lang: set() for lang in langs}  # units with speech so far; a unit logs more than once
    for line in log.read_text().splitlines() if log.exists() else []:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line still being written
        msg = event["msg"]
        if msg.startswith("separating"):
            label, frac = "Separating the voice from the music", 0.05
        elif msg.startswith("found"):
            label, frac = "Transcribing the English", 0.08
        elif msg.startswith("planned"):
            units, label, frac = int(msg.split()[1]), "Translating", 0.15
        elif event.get("stage") == "speech" and "unit" in event and event.get("lang") in done:
            done[event["lang"]].add(event["unit"])
    if units and any(done.values()):
        frac = 0.15 + 0.8 * sum(map(len, done.values())) / (units * len(langs))
        label = "Speaking and checking  ·  " + "   ".join(f"{LANGUAGE_NAMES[code]} {len(done[code])}/{units}" for code in langs)
    return min(frac, 0.97), label


def show_results(run: dict) -> None:
    out, report_path = run["out"], run["out"] / "report.json"
    if not report_path.exists():
        st.error("The run stopped before it could write a report.")
        st.code((run["work"] / "console.log").read_text()[-3000:], language=None)
        return
    report = json.loads(report_path.read_text())
    ok = sum(s == "ok" for s in report["status"].values())
    minutes, seconds = divmod(int(report["latency_s"]["total"]), 60)
    st.html(f"""<div class="cards">
      <div class="card"><span>Languages</span><div>{ok} / {len(report["langs"])}</div></div>
      <div class="card"><span>Sentences</span><div>{report.get("units", 0)}</div></div>
      <div class="card"><span>Run time</span><div>{minutes}:{seconds:02d}</div></div>
      <div class="card"><span>Bodhan cost</span><div>₹{report["cost_inr"]["total"]:.2f}</div></div></div>""")
    if report.get("error"):
        st.error(report["error"])
    if not report.get("llm", {}).get("available", True):
        st.warning(f"Dubbed without the LLM ({report['llm']['down_reason']}). Sentences follow the raw transcript "
                   "and long lines were sped up rather than shortened.")

    for tab, lang in zip(st.tabs([LANGUAGE_NAMES[code] for code in report["langs"]]), report["langs"], strict=True):
        with tab:
            result, folder = report["languages"].get(lang, {}), out / lang
            if result.get("status") != "ok":
                st.error(report["status"][lang])
                continue
            video, srt = folder / f"video.{lang}.mp4", folder / f"subtitles.{lang}.srt"
            left, right = st.columns([3, 2], gap="large")
            with left:
                if _codec(video) in BROWSER_CODECS:
                    st.video(str(video), subtitles={LANGUAGE_NAMES[lang]: str(srt)})
                else:
                    st.info(f"The video keeps your file's {_codec(video)} codec, which browsers can't play. Download it to watch.")
                st.caption(f"AI-dubbed with Bodhan's Indic models · voice {', '.join(result['voices'])}")
                a, b, c = st.columns(3)
                a.download_button("Video", video.read_bytes(), video.name, "video/mp4", use_container_width=True)
                b.download_button("Subtitles", srt.read_bytes(), srt.name, "text/plain", use_container_width=True)
                c.download_button("Segments", (folder / f"segments.{lang}.json").read_bytes(),
                                  f"segments.{lang}.json", "application/json", use_container_width=True)
            with right:
                rows = "".join(
                    f'<tr><td>{TARGET_NAMES[k]}</td><td style="color:#71717a">{t["target"]}</td>'
                    f'<td><span class="pill {"met" if t["met"] else "missed"}">{"Met" if t["met"] else "Missed"}</span></td></tr>'
                    for k, t in result["targets"].items())
                st.html(f'<table class="targets">{rows}</table>')
            segments = json.loads((folder / f"segments.{lang}.json").read_text())
            st.dataframe(pd.DataFrame([{
                "Time": f"{int(s['start'] // 60)}:{int(s['start'] % 60):02d}",
                "English": s.get("source_spoken", s["source"]),
                LANGUAGE_NAMES[lang]: s["translation"],
                "Speed": s.get("speed"),
                "CER": s.get("cer"),
                "Notes": "; ".join(s.get("qa", [])) or s.get("skipped", ""),
            } for s in segments]), hide_index=True, use_container_width=True, column_config={
                "Time": st.column_config.TextColumn(width=60),
                "English": st.column_config.TextColumn(width="medium"),
                LANGUAGE_NAMES[lang]: st.column_config.TextColumn(width="medium"),
                "Speed": st.column_config.NumberColumn(format="%.2f×", width=70),
                "CER": st.column_config.NumberColumn(format="%.2f", width=60),
            })

    left, right, _ = st.columns([1, 1, 3])
    left.download_button("Run report (JSON)", report_path.read_bytes(), "report.json", "application/json",
                         use_container_width=True)
    if right.button("Dub another video", use_container_width=True):
        del st.session_state["run"]
        st.rerun()


def _codec(video: Path) -> str:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    return probe.stdout.strip() or "unknown"


env = keys_from_sidebar()
st.html('<div class="eyebrow">Bodhan AI Cookbook · Recipe</div>')
st.title("Video dubbing")
st.html('<p class="lede">Dub an English video into Indian languages with Indic-Transcribe, Indic-Translate and '
        'Indic-Speak. Speech is timed to the original, background music is kept, and every line is checked by '
        'listening back to it.</p>')

run = st.session_state.get("run")
slot = st.empty()  # one slot: each view replaces the last instead of leaving it greyed out
if run and run["proc"].poll() is None:
    bar = slot.progress(0.0, text="Starting")
    while run["proc"].poll() is None:
        frac, label = progress(run["out"] / "run.jsonl", run["langs"])
        bar.progress(frac, text=label)
        time.sleep(1)
    st.rerun()
elif run:
    with slot.container():
        show_results(run)
else:
    with slot.container():
        step(1, "Video")
        video = st.file_uploader("English video, 1 to 15 minutes (MP4, MOV, MKV or WebM)", type=["mp4", "mov", "mkv", "webm"])
        step(2, "Languages")
        langs = st.multiselect("Target languages", LANGS, default=["hi"], format_func=LANGUAGE_NAMES.get)
        step(3, "Options")
        left, right = st.columns(2)
        voice = left.selectbox("Voice", ["auto", "female", "male"],
                               format_func={"auto": "Match the speakers", "female": "Female", "male": "Male"}.get)
        music = right.selectbox("Background music", ["auto", "on", "off"],
                                format_func={"auto": "Keep it, if there is any", "on": "Always separate", "off": "Don't separate"}.get)
        keep = st.text_input("Keep these terms exactly as written", placeholder="Comma-separated, e.g. names, products, code")
        code_mixed = st.toggle("Code-mixed translation, keeping common English words (e.g. Hinglish)")
        st.caption("Runs on your Bodhan account: typically ₹0.5 to ₹1 per minute of video per language.")
        if st.button("Dub video", type="primary", disabled=not (video and langs)):
            if missing := missing_keys(env):
                st.warning("Add your " + " and ".join(missing) + " in the sidebar.")
            else:
                st.session_state["run"] = start_run(video, langs, env, voice, music, keep, code_mixed)
                st.rerun()

st.html('<div class="footer">Made with ❤️ by <a href="https://github.com/santhosh-005" target="_blank">santhosh</a></div>')
