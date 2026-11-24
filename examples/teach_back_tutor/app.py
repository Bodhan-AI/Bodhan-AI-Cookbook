"""Samjhao (Teach-Back Tutor) app: photograph a textbook page, then teach it to a classmate who missed the class.

    streamlit run app.py
"""

from __future__ import annotations

import hashlib
import io
import json
from html import escape
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import bodhan  # noqa: E402
import exercise  # noqa: E402
import intent  # noqa: E402
import lesson  # noqa: E402
import memory  # noqa: E402
import parent  # noqa: E402
import quiz  # noqa: E402
import tutor  # noqa: E402

LANGS = {"en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
         "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese",
         "ur": "Urdu"}
SAMPLE = HERE / "sample_data" / "photosynthesis_page.png"
EXERCISE_SAMPLE = HERE / "sample_data" / "exercise_page.png"
SMALL_TEXT = ("The text in this photo is very small, so some words may have been misread. If the lesson below "
              "looks wrong, take the photo again closer to the page.")
STEP = {"explained": ("you explained it", "green", "on"), "after_nudge": ("after a nudge", "amber", "half"),
        "revealed": ("Riya worked it out", "gray", "rev"), "skipped": ("skipped", "gray", ""),
        "not_yet": ("not reached", "gray", "")}
STATUS = {"explained": ("explained", "green"), "partial": ("partly", "amber"), "not_covered": ("not yet", "gray")}
PLANT = {"caught": ("caught it", "green"), "caught_after_hint": ("caught after a hint", "amber"),
         "revealed": ("missed it", "red"), "planted": ("no answer yet", "gray")}
PERSONA_COLOR = {"Chintu": "#F59E0B", "Riya": "#8B5CF6", "Arjun": "#0EA5E9"}
STUDENT_COLOR = "#4F46E5"

TOKENS = {
    "light": {
        "card": "#FFFFFF", "fill": "#F4F5FA", "border": "#E3E6F0", "text": "#0F172A", "muted": "#5B6478",
        "faint": "#8A93A8", "accent": "#6366F1", "accent2": "#A855F7", "accent-soft": "#EEF0FF",
        "accent-text": "#4338CA", "me": "#EEF0FF", "me-border": "#DCE0FB", "shadow": "0 1px 2px rgba(15,23,42,.05), 0 8px 24px -12px rgba(15,23,42,.10)",
        "green-bg": "#DCFCE7", "green": "#166534", "amber-bg": "#FEF3C7", "amber": "#92400E",
        "gray-bg": "#F1F3F8", "gray": "#5B6478", "red-bg": "#FEE2E2", "red": "#991B1B", "track": "#E7E9F2",
        "blob": ".38",
    },
    "dark": {
        "card": "#141A2A", "fill": "#1A2134", "border": "#252E45", "text": "#E6E9F2", "muted": "#A3ACC2",
        "faint": "#7C869E", "accent": "#8B93FF", "accent2": "#C084FC", "accent-soft": "rgba(139,147,255,.14)",
        "accent-text": "#C7CBFF", "me": "rgba(139,147,255,.13)", "me-border": "rgba(139,147,255,.28)",
        "shadow": "0 1px 2px rgba(0,0,0,.4), 0 10px 30px -14px rgba(0,0,0,.6)",
        "green-bg": "rgba(34,197,94,.16)", "green": "#86EFAC", "amber-bg": "rgba(245,158,11,.16)", "amber": "#FCD34D",
        "gray-bg": "rgba(148,163,184,.14)", "gray": "#CBD5E1", "red-bg": "rgba(239,68,68,.16)", "red": "#FCA5A5",
        "track": "#262F47", "blob": ".30",
    },
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root { __TOKENS__ }
.stApp, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp p, .stApp li, .stApp label, .stApp button,
.stApp input, .stApp textarea { font-family: 'Plus Jakarta Sans', sans-serif; }
.block-container { padding-top: 2.6rem; max-width: 880px; }
.stAppDeployButton { display: none; }
.stButton > button, .stFormSubmitButton > button { border-radius: 12px; font-weight: 700; }
[data-testid="stExpander"] details { border-radius: 14px; border-color: var(--border); background: var(--card); }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 18px; }

/* page header */
.pagehead { margin: 0 0 18px; }
.eyebrow { display: inline-flex; gap: 8px; align-items: center; font-size: .74rem; font-weight: 800;
  letter-spacing: .08em; text-transform: uppercase; color: var(--accent-text); background: var(--accent-soft);
  padding: 5px 12px; border-radius: 999px; }
.pagehead h1 { font-size: 2.35rem; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; margin: 12px 0 8px;
  padding: 0; color: var(--text); }
.pagehead h1 .grad { background: linear-gradient(90deg, var(--accent), var(--accent2)); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.pagehead p { color: var(--muted); font-size: 1.04rem; margin: 0; max-width: 640px; }
.feats { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 4px; }
.feat { display: inline-flex; align-items: center; gap: 6px; font-size: .85rem; font-weight: 600; color: var(--muted);
  background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px; }
.feat b { color: var(--text); }
.stepno { display: inline-flex; width: 24px; height: 24px; border-radius: 50%; align-items: center; justify-content: center;
  background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #fff; font-weight: 800; font-size: .8rem;
  margin-right: 8px; }
.steptitle { font-weight: 800; font-size: 1rem; color: var(--text); margin: 4px 0 8px; display: flex; align-items: center; }

/* cards */
.card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 18px 20px;
  margin-bottom: 14px; box-shadow: var(--shadow); }
.card h3 { margin: 0 0 10px; font-size: 1.06rem; font-weight: 800; color: var(--text); }
.muted { color: var(--muted); font-size: .88rem; }
.label { color: var(--faint); font-size: .72rem; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .76rem; font-weight: 700;
  background: var(--accent-soft); color: var(--accent-text); margin: 3px 4px 3px 0; white-space: nowrap; }
.chip.green { background: var(--green-bg); color: var(--green); } .chip.amber { background: var(--amber-bg); color: var(--amber); }
.chip.gray { background: var(--gray-bg); color: var(--gray); } .chip.red { background: var(--red-bg); color: var(--red); }

/* session header */
.head { display: flex; justify-content: space-between; gap: 18px; flex-wrap: wrap; align-items: flex-end; }
.head .topic { font-size: 1.4rem; font-weight: 800; margin: 2px 0 6px; color: var(--text); letter-spacing: -.01em; }
.dots { display: flex; gap: 6px; margin: 6px 0 4px; }
.dot { width: 28px; height: 9px; border-radius: 999px; background: var(--track); }
.dot.on { background: #22C55E; } .dot.half { background: #F59E0B; } .dot.rev { background: var(--accent); opacity: .55; }
.banner { border: 1px solid var(--me-border); background: var(--me); border-radius: 14px; padding: 12px 16px;
  margin-bottom: 12px; color: var(--text); font-size: .93rem; }
.modepill { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: .92rem; color: var(--muted); }
.modepill b { color: var(--text); }

/* chat */
[data-testid="stChatMessage"] { background: var(--card); border: 1px solid var(--border); border-radius: 18px;
  padding: 14px 16px; margin-bottom: 10px; box-shadow: var(--shadow); }
[data-testid="stChatMessage"]:has(.from-student) { background: var(--me); border-color: var(--me-border); box-shadow: none; }
.who { font-weight: 800; font-size: .78rem; color: var(--faint); margin-bottom: 2px; letter-spacing: .02em; }
.said { font-size: 1.03rem; line-height: 1.55; color: var(--text); }
.translation { color: var(--muted); font-size: .85rem; font-style: italic; border-left: 3px solid var(--border);
  padding-left: 8px; margin-top: 6px; }

/* scores */
.score { display: flex; gap: 22px; align-items: center; flex-wrap: wrap; }
.ring { width: 124px; height: 124px; border-radius: 50%; flex: none; display: flex; align-items: center;
  justify-content: center; background: conic-gradient(var(--accent) calc(var(--p) * 1%), var(--track) 0); }
.ring > div { width: 98px; height: 98px; border-radius: 50%; background: var(--card); display: flex; flex-direction: column;
  align-items: center; justify-content: center; font-size: 1.7rem; font-weight: 800; color: var(--text); line-height: 1; }
.ring small { font-size: .66rem; font-weight: 700; color: var(--faint); margin-top: 4px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; flex: 1; min-width: 260px; }
.stat { background: var(--fill); border-radius: 14px; padding: 12px 14px; }
.stat b { display: block; font-size: 1.3rem; font-weight: 800; color: var(--text); }
.row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 10px 0;
  border-bottom: 1px solid var(--border); }
.row:last-child { border-bottom: none; padding-bottom: 0; }
.row .name { font-weight: 700; color: var(--text); }
.quote { color: var(--muted); font-size: .86rem; font-style: italic; margin-top: 2px; }
.skill { margin-bottom: 14px; }
.skill .top { display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 6px; color: var(--text); }
.bar { height: 9px; background: var(--track); border-radius: 999px; overflow: hidden; }
.bar > span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent2)); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 14px; }
.kpi { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 14px 16px; box-shadow: var(--shadow); }
.kpi b { display: block; font-size: 1.55rem; font-weight: 800; color: var(--text); letter-spacing: -.01em; }
.next { display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; color: var(--text); }
.next .n { flex: none; width: 24px; height: 24px; border-radius: 50%; background: var(--accent-soft); color: var(--accent-text);
  display: inline-flex; align-items: center; justify-content: center; font-weight: 800; font-size: .8rem; }

/* sidebar brand */
.brand { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.brand .logo { width: 36px; height: 36px; border-radius: 11px; background: linear-gradient(135deg, var(--accent), var(--accent2));
  color: #fff; font-weight: 800; display: flex; align-items: center; justify-content: center; font-size: .95rem; }
.brand .t { font-weight: 800; font-size: 1.08rem; color: var(--text); line-height: 1.1; }
.brand .s { color: var(--muted); font-size: .8rem; }
.powered { color: var(--faint); font-size: .76rem; margin-top: 14px; line-height: 1.5; }

/* landing: centred wordmark over slowly drifting colour */
.landing { position: relative; overflow: hidden; text-align: center; border-radius: 28px; padding: 50px 24px 36px;
  margin-bottom: 18px; border: 1px solid var(--border); background: var(--card); box-shadow: var(--shadow); }
.landing .blob { position: absolute; border-radius: 50%; filter: blur(64px); opacity: var(--blob); pointer-events: none;
  animation: drift 16s ease-in-out infinite; }
.landing .b1 { width: 340px; height: 340px; background: #6366F1; top: -140px; left: -90px; }
.landing .b2 { width: 300px; height: 300px; background: #EC4899; bottom: -160px; right: -70px; animation-delay: -6s; }
.landing .b3 { width: 220px; height: 220px; background: #06B6D4; top: 40px; right: 16%; animation-delay: -11s; }
@keyframes drift { 0%, 100% { transform: translate(0, 0) scale(1); } 50% { transform: translate(34px, 22px) scale(1.1); } }
.landing .in { position: relative; z-index: 1; }
.wordmark { font-size: clamp(3.6rem, 11vw, 6.2rem) !important; font-weight: 800 !important; letter-spacing: -.045em;
  line-height: 1 !important; margin: 16px 0 4px !important; padding: 0 !important;
  background: linear-gradient(90deg, #6366F1, #A855F7, #EC4899, #F59E0B, #6366F1); background-size: 300% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent !important; animation: shine 10s linear infinite; }
@keyframes shine { to { background-position: 300% 0; } }
.landing .deva { font-size: 1.12rem; font-weight: 700; color: var(--text); opacity: .8; margin-bottom: 14px; }
.landing p { color: var(--muted); font-size: 1.04rem; max-width: 560px; margin: 0 auto; }
.landing .feats { justify-content: center; margin-top: 18px; }
.st-key-composer { background: var(--card); border-radius: 22px; box-shadow: var(--shadow); }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 18px 0 6px; }
@media (max-width: 680px) { .tiles { grid-template-columns: 1fr; } }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 16px 16px 14px;
  box-shadow: var(--shadow); transition: transform .15s ease; }
.tile:hover { transform: translateY(-2px); }
.tile .ic { width: 38px; height: 38px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
  font-size: 1.15rem; background: var(--accent-soft); }
.tile .t { font-weight: 800; color: var(--text); margin: 10px 0 2px; }
.tile .d { color: var(--muted); font-size: .86rem; line-height: 1.45; }

/* compact header for the other screens */
.hero { border-radius: 22px; padding: 22px 24px; margin-bottom: 16px; border: 1px solid var(--border);
  background: linear-gradient(135deg, var(--accent-soft), transparent 65%), var(--card); box-shadow: var(--shadow); }
.hero .tag { display: inline-block; font-size: .72rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase;
  color: var(--accent-text); background: var(--accent-soft); padding: 4px 11px; border-radius: 999px; }
.hero h1 { font-size: 1.85rem; font-weight: 800; letter-spacing: -.02em; margin: 10px 0 6px; padding: 0; color: var(--text); }
.hero p { color: var(--muted); margin: 0; font-size: .98rem; }
</style>
"""


def css(theme: str) -> str:
    tokens = TOKENS["dark" if theme == "dark" else "light"]
    return CSS.replace("__TOKENS__", " ".join(f"--{k}: {v};" for k, v in tokens.items()))


st.set_page_config(page_title="Samjhao", page_icon=":material/school:", layout="centered")
st.markdown(css(st.context.theme.type or "light"), unsafe_allow_html=True)
ss = st.session_state


def ui(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def chip(text: str, tone: str = "") -> str:
    return f'<span class="chip {tone}">{escape(text)}</span>'


@st.cache_data
def avatar(letter: str, color: str) -> bytes:
    """A round initial-letter avatar, so each persona has its own face without image assets."""
    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=color)
    font = None
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 46)
            break
        except OSError:
            continue
    draw.text((size / 2, size / 2 + 2), letter.upper(), fill="white", font=font or ImageFont.load_default(size=46),
              anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


with st.sidebar:
    ui('<div class="brand"><div class="logo">स</div><div><div class="t">Samjhao</div>'
       '<div class="s">Learn it by teaching it</div></div></div>')
    st.write("")
    view = {"Learn": "Learn", "Progress": "My progress", "Results": "Evaluation"}[
        st.segmented_control("View", ["Learn", "Progress", "Results"], default="Learn",
                             label_visibility="collapsed", width="stretch") or "Learn"]
    st.write("")
    student = st.text_input("Your name", "Aarav", help="The tutor remembers each student between sessions.")
    col_a, col_b = st.columns(2)
    band = col_a.selectbox("Class", list(tutor.BANDS), index=1, format_func=lambda b: tutor.BANDS[b]["label"])
    lang = col_b.selectbox("Language", list(LANGS), index=1, format_func=LANGS.get)
    buddy = st.segmented_control(
        "Your classmate", ["Riya", "Chintu", "Arjun"], default=tutor.BANDS[band]["name"], width="stretch",
        help="Riya: curious explorer (her voice). Chintu: playful and bubbly. Arjun: a sharp debater "
             "(their own voices). Your class still sets how hard the questions are.") or tutor.BANDS[band]["name"]
    roman = lang != "en" and st.toggle("I type in English letters (Hinglish)", value=True)
    voice_on = lang in bodhan.VOICES and st.toggle("Classmate speaks out loud", value=True)
    with st.expander("Options", icon=":material/tune:"):
        teacher_view = st.toggle("Teacher view", help="Show the score, the next move, the level and the fact-check "
                                                      "of every turn.")
        fact_check_on = st.toggle("Fact-check the classmate", value=True,
                                  help="Chain-of-Verification: every classmate line is checked against the page "
                                       "before you see it, and rewritten if it is wrong or uses words you have not "
                                       "taught yet.")
        quick_check = st.toggle("Quick check before and after", value=True,
                                help="Three questions before and after teaching, to measure your learning gain.")
    st.write("")
    if "session" in ss and "report" not in ss and ss.session.turn and st.button(
            "Finish session", icon=":material/flag:", width="stretch", type="primary"):
        ss.finish_now = True
    if st.button("New page", icon=":material/add_photo_alternate:", width="stretch"):
        for key in list(ss.keys()):
            del ss[key]
        st.rerun()
    ui('<div class="powered">Powered by Bodhan · indic-ocr · indic-transcribe · indic-translate · '
       'indic-transliterate · indic-speak<br>Switch light / dark in ⋮ → Settings.</div>')


def page_error(err: Exception) -> str:
    """A message a student can act on; the technical detail stays at the end for a teacher or developer."""
    msg = str(err)
    if isinstance(err, bodhan.BodhanError) and ("budget" in msg.lower() or "rate limit" in msg.lower()):
        return f"The reading service is busy or out of credit. Try again in a minute. ({msg[:120]})"
    if isinstance(err, bodhan.BodhanError):
        return ("indic-ocr could not read this page. Handwriting, faded old print or a blurred photo can do this. "
                f"Try a clearer photo of a printed page. ({msg[:120]})")
    return msg


def localize(text: str) -> str:
    return text if lang == "en" else bodhan.translate(text, target_language=lang)


def add_buddy(english: str, check: dict | None = None) -> None:
    english = english.replace("**", "").replace("__", "")  # the LLM sometimes bolds words; shown and spoken plain
    shown = localize(english)
    audio = None
    if voice_on:
        try:
            cfg = ss.session.cfg  # each classmate has their own voice and speaking style
            audio = bodhan.speak_long(shown, lang, voice=bodhan.voice_for(lang, cfg["voice"]), style=cfg["voice_style"])
        except bodhan.BodhanError as err:
            st.toast(f"Could not speak the reply: {err}")
    ss.chat.append({"role": "assistant", "text": shown, "english": english, "audio": audio, "check": check})


def finish() -> None:
    if ss.get("mode") == "homework":
        ss.report = exercise.report(ss.session)
        memory.save(memory.record_homework(ss.profile, ss.report))
    else:
        ss.report = tutor.report(ss.session)
        memory.save(memory.record(ss.profile, ss.session, ss.report))


def render_chat(name: str, buddy_face: bytes, student_face: bytes) -> None:
    for i, m in enumerate(ss.chat):
        mine = m["role"] == "user"
        with st.chat_message("user" if mine else "assistant", avatar=student_face if mine else buddy_face):
            who = escape(student or "You") if mine else escape(name)
            body = (f'<div class="{"from-student" if mine else "from-buddy"}"><div class="who">{who}</div>'
                    f'<div class="said">{escape(m["text"])}</div>')
            if m["english"] != m["text"]:
                body += f'<div class="translation">{escape(m["english"])}</div>'
            if teacher_view and m.get("info"):
                ev, info = m["info"]["eval"], m["info"]
                body += ('<div style="margin-top:8px">' + chip(f"quality {ev.get('quality')}/3")
                         + chip(f"next: {info['move'].replace('_', ' ')}", "gray") + chip(f"level {info['level']}", "gray")
                         + (f'<div class="muted">{escape(ev.get("short_feedback", ""))}</div>'
                            if ev.get("short_feedback") else "") + "</div>")
            if teacher_view and not mine:
                body += check_html(m.get("check"))
            ui(body + "</div>")
            if m.get("audio"):
                st.audio(m["audio"], format="audio/wav", autoplay=i == len(ss.chat) - 1)


def read_message(placeholder: str) -> tuple[str, str] | None:
    """The student's turn, typed (with transliteration) or spoken: (what they said, English for grading)."""
    heard = st.audio_input("Or answer out loud", help="Speak in the language chosen in the sidebar.")
    typed = st.chat_input(placeholder)
    try:
        if heard is not None and ss.get("last_audio") != hashlib.sha1(heard.getvalue()).hexdigest():
            ss.last_audio = hashlib.sha1(heard.getvalue()).hexdigest()
            with st.spinner("Listening with indic-transcribe..."):
                said = bodhan.transcribe(heard.getvalue(), language=None if lang == "en" else lang,
                                         filename="speech.wav")
                return said, said if lang == "en" else bodhan.translate(said, target_language="en")
        if typed:
            said = bodhan.transliterate(typed, language=lang) if roman else typed
            return said, said if lang == "en" else bodhan.translate(said, target_language="en")
    except bodhan.BodhanError as err:
        st.error(f"Bodhan API error: {err}")
    return None


def take_turn(name: str, message: tuple[str, str], engine) -> None:
    said, english = message
    ss.chat.append({"role": "user", "text": said, "english": english})
    with st.spinner(f"{name} is thinking..."):
        reply, info = engine.step(ss.session, english)
        ss.chat[-1]["info"] = info
        add_buddy(reply, info["check"])
    if ss.session.done:
        finish()
    st.rerun()


VERDICT_TONE = {"true": "green", "false": "red", "unverified": "gray"}


def check_html(c: dict | None) -> str:
    """Teacher view of one fact-check: the verdict per claim with the page's own words, and what was fixed."""
    if not c:
        return ""
    if "verdicts" not in c:  # Homework mode: the answer guard
        head = (chip("answer guard: rewritten", "amber") if c["regenerated"] else
                chip("answer guard: passed", "green") if c["ok"] else chip("answer guard: issues remain", "red"))
        fixed = (f'<div class="muted">First draft blocked: {escape("; ".join(c["first_problems"]))}</div>'
                 if c["regenerated"] else "")
        return f'<div style="margin-top:8px">{head}{fixed}</div>'
    head = (chip("fact-check: rewritten", "amber") if c["regenerated"] else
            chip("fact-check: passed", "green") if c["ok"] else chip("fact-check: issues remain", "red"))
    if c.get("plant_present"):
        head += chip("planted mistake confirmed", "gray")
    if not c["verdicts"]:
        head += chip("no factual claims", "gray")
    rows = "".join(
        f'<div class="quote">{chip(v["verdict"], VERDICT_TONE[v["verdict"]])}{escape(v["claim"])}'
        + (f' <span class="muted">· page: "{escape(v["evidence"])}"</span>' if v["evidence"] else "") + "</div>"
        for v in c["verdicts"])
    fixed = (f'<div class="muted">First draft blocked: {escape("; ".join(c["first_problems"]))}</div>'
             if c["regenerated"] else "")
    return f'<div style="margin-top:8px">{head}{rows}{fixed}</div>'


def quiz_form(stage: str) -> None:
    """The quick check (Feynman Bot style learning gain). stage is "pre" or "post"."""
    qz = ss.quiz
    title = "Quick check before you teach" if stage == "pre" else "Quick check again, after teaching"
    with st.form(f"quiz_{stage}"):
        st.markdown(f"**{title}**")
        st.caption(f"{len(qz['questions'])} questions. Each one was double-checked against your page by a separate "
                   f"solver; {len(qz['dropped'])} draft questions were thrown out.")
        picks = [st.radio(f"{i}. {q['q']}", q["options"], index=None, key=f"{stage}_{i}")
                 for i, q in enumerate(qz["questions"], 1)]
        col1, col2 = st.columns(2)
        submitted = col1.form_submit_button("Submit answers", type="primary", width="stretch")
        skipped = stage == "pre" and col2.form_submit_button("Skip for now", width="stretch")
    if submitted:
        idx = [q["options"].index(p) if p is not None else None for p, q in zip(picks, qz["questions"], strict=True)]
        ss[stage], ss[f"{stage}_picks"] = quiz.score(qz, idx), idx
        if stage == "post":
            memory.save(memory.add_quiz(ss.profile, ss.get("pre"), ss.post, len(qz["questions"])))
        st.rerun()
    if skipped:
        ss[stage] = None
        st.rerun()


# ------------------------------------------------------------------ progress dashboard

def show_progress(name: str) -> None:
    profile = memory.load(name)
    data, homework = memory.skills(profile), profile.get("homework", [])
    ui(f'<div class="hero"><div class="tag">My progress</div><h1>{escape(name)}\'s learning journey</h1>'
       '<p>Every session you teach is remembered. Here is what you have mastered and what to practise next.</p></div>')
    if homework:
        rows = []
        for h in homework:
            chips = chip("steps on own " + h["steps_on_own"], "gray") + chip("nudges {}".format(h["nudges"]), "gray")
            chips += (chip("solved", "green") if h["solved"] else chip("not graded", "gray") if not h["graded"]
                      else chip("not solved yet", "amber"))
            rows.append(f'<div class="row"><div><div class="name">{escape(h["question"])}</div>'
                        f'<div class="quote">{h["date"]}</div></div><div>{chips}</div></div>')
        ui('<div class="card"><h3>Homework solved together</h3>' + "".join(rows) + "</div>")
    if not data["sessions"] and homework:
        return
    if not data["sessions"]:
        ui('<div class="card"><h3>No sessions yet</h3><p class="muted">Teach a page to your classmate and your '
           'progress will show up here.</p></div>')
        return
    gain = "-" if data["learning_gain"] is None else f'{data["learning_gain"]:+g}'
    ui('<div class="kpis">'
       f'<div class="kpi"><span class="label">Sessions</span><b>{data["sessions"]}</b></div>'
       f'<div class="kpi"><span class="label">Level</span><b>{data["level"]} / 3</b></div>'
       f'<div class="kpi"><span class="label">Ideas mastered</span><b>{data["mastered_total"]} / '
       f'{data["concepts_total"]}</b></div>'
       f'<div class="kpi"><span class="label">Avg learning gain</span><b>{gain}</b></div></div>')
    if data["quizzes"]:
        rows = []
        for q in data["quizzes"]:
            pre, post, total = q["pre"], q["post"], q["total"]
            chips = (chip(f"before {pre}/{total}", "gray") + chip(f"after {post}/{total}")
                     + chip(f"gain {post - pre:+d}", "green" if post > pre else "gray"))
            rows.append(f'<div class="row"><div><div class="name">{escape(q["topic"])}</div>'
                        f'<div class="quote">{q["date"]}</div></div><div>{chips}</div></div>')
        ui('<div class="card"><h3>Quick checks: before and after teaching</h3>' + "".join(rows) + "</div>")

    rows = []
    for skill in data["skills"]:
        if skill["value"] is None:
            value, width, trend = "not tested yet", 0, ""
        else:
            value, width = f"{skill['value']}%", skill["value"]
            trend = "" if not skill["trend"] else chip(f"{skill['trend']:+d} vs before",
                                                       "green" if skill["trend"] > 0 else "red")
        rows.append(f'<div class="skill"><div class="top"><span>{escape(skill["name"])}</span>'
                    f'<span>{trend} {value}</span></div><div class="bar"><span style="width:{width}%"></span>'
                    '</div></div>')
    ui('<div class="card"><h3>Thinking skills</h3>' + "".join(rows) + "</div>")

    for t in data["topics"]:
        mastered = "".join(chip(c, "green") for c in t["mastered"]) or '<span class="muted">none yet</span>'
        practise = "".join(chip(c, "amber") for c in t["to_practice"]) or '<span class="muted">nothing, well done!</span>'
        ui(f'<div class="card"><h3>{escape(t["topic"])} <span class="chip">{t["mastery"]}% last time</span></h3>'
           f'<div class="label">Mastered</div><div>{mastered}</div>'
           f'<div class="label" style="margin-top:10px">Still to practise</div><div>{practise}</div></div>')

    if len(data["history"]) > 1:
        with st.container(border=True):
            st.markdown("**Understanding over sessions**")
            st.line_chart({"Understanding %": [h["mastery"] for h in data["history"]]}, color=STUDENT_COLOR,
                          height=220)

    steps = "".join(f'<div class="next"><span class="n">{i}</span><span>{escape(s)}</span></div>'
                    for i, s in enumerate(data["next_steps"], 1))
    ui(f'<div class="card"><h3>Practise next</h3>{steps}</div>')


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def show_evaluation() -> None:
    """How we know it works: the two LLM judges checked against hand labels, and the simulated students."""
    ui('<div class="hero"><div class="tag">Evaluation</div><h1>How we know it works</h1>'
       '<p>Every LLM decision in this app is checked: the grader against hand-labelled turns, the fact-checker '
       'against hand-labelled statements, and the whole tutor against simulated students.</p></div>')
    path = HERE / "eval" / "results.json"
    if not path.exists():
        ui('<div class="card"><h3>No results yet</h3><p class="muted">Run <code>python evaluate_judges.py</code> '
           'and <code>python simulate.py --all</code>.</p></div>')
        return
    r = json.loads(path.read_text(encoding="utf-8"))
    j, v = r["judge"], r["verifier"]
    ui(f'<div class="muted" style="margin-bottom:10px">Last run {r["date"]} with <code>{escape(r["model"])}</code>. '
       'Labels are in <code>eval/</code>; re-run with <code>python evaluate_judges.py</code>.</div>')

    ui('<div class="card"><h3>1. The grader (LLM-as-a-judge) vs a teacher\'s labels</h3>'
       f'<p class="muted">{j["items"]} student turns, {j["concept_labels"]} concept labels. Does it give the same '
       'credit a careful teacher would, including no credit for just agreeing or echoing?</p><div class="kpis">'
       f'<div class="kpi"><span class="label">Concept credit</span><b>{pct(j["concept_accuracy"])}</b></div>'
       f'<div class="kpi"><span class="label">Planted mistake caught/missed</span><b>{pct(j["plant_accuracy"])}</b></div>'
       f'<div class="kpi"><span class="label">Quality score in range</span><b>{pct(j["quality_in_range"])}</b></div>'
       f'<div class="kpi"><span class="label">Over-credits</span><b>{j["over_credit"]}</b></div></div></div>')
    with st.expander("Grader: every labelled turn"):
        st.dataframe([{"#": x["id"], "student said": x["message"], "agrees": x["ok"], "differences": "; ".join(x["issues"])}
                      for x in j["rows"]], hide_index=True, width="stretch")

    main, held = v.get("main", v), v.get("held_out")
    ui('<div class="card"><h3>2. The fact-checker (Chain-of-Verification) vs labelled statements</h3>'
       '<p class="muted">Each statement is said by the classmate. False ones must be blocked, true ones must pass. '
       'Held-out statements were written after the last fix, to check it generalises.</p><div class="kpis">'
       f'<div class="kpi"><span class="label">Precision</span><b>{pct(main["precision"])}</b></div>'
       f'<div class="kpi"><span class="label">Recall</span><b>{pct(main["recall"])}</b></div>'
       f'<div class="kpi"><span class="label">F1</span><b>{main["f1"]:.2f}</b></div>'
       + (f'<div class="kpi"><span class="label">Held-out accuracy</span><b>{pct(held["accuracy"])}</b>'
          f'<span class="muted">{held["statements"]} statements</span></div>' if held else "") + "</div>")
    v1_path = HERE / "eval" / "results_v1.json"
    if v1_path.exists():
        v1 = json.loads(v1_path.read_text(encoding="utf-8"))["verifier"]
        ui('<p class="muted">Before the quantity-word fix (v1): recall '
           f'{pct(v1["recall"])}. "Plants need <i>only</i> water" was checked as "do plants need water?" and passed. '
           'The planning prompt now keeps words like only / all / none in the check question.</p>')
    ui("</div>")
    with st.expander("Fact-checker: every labelled statement"):
        st.dataframe([{"set": x.get("group", "main"), "statement": x["statement"], "label": x["label"],
                       "blocked": x["flagged"], "correct": x["ok"]} for x in v["rows"]], hide_index=True, width="stretch")

    sim = r.get("simulation")
    if sim:
        rows = []
        for x in sim["rows"]:
            chips = chip("{}% understanding".format(x["mastery"])) + chip("tricks " + x["tricks"], "gray")
            if x.get("quiz_post") is not None:
                chips += chip("quick check {} → {}".format(x["quiz_pre"], x["quiz_post"]), "green")
            chips += chip("{} fact-checked lines".format(x["checked_lines"]), "gray")
            rows.append(f'<div class="row"><div><div class="name">{escape(x["profile"])} student</div>'
                        f'<div class="quote">{escape(x["detail"])}</div></div><div>{chips}</div></div>')
        ui(f'<div class="card"><h3>3. Simulated students</h3><p class="muted">An LLM role-plays three students who '
           f'know different amounts ({escape(sim["model"])}, {sim["date"]}). The scores should rank them strong &gt; '
           'partial &gt; confused.</p>' + "".join(rows) + "</div>")

    hw = r.get("homework")
    if hw:
        ui('<div class="card"><h3>4. Homework mode: is the answer key right?</h3>'
           f'<p class="muted">{hw["items"]} exercise questions with known answers ({escape(hw["model"])}). One solve '
           'vs self-consistency: 3 independent solves, use the answer only if a majority agree, otherwise do not '
           'grade.</p><div class="kpis">'
           f'<div class="kpi"><span class="label">One solve correct</span><b>{pct(hw["single_solve_accuracy"])}</b></div>'
           f'<div class="kpi"><span class="label">Answered (majority agreed)</span><b>{hw["answered"]}/{hw["items"]}</b></div>'
           f'<div class="kpi"><span class="label">Correct when answered</span><b>{pct(hw["accuracy_when_answered"])}</b></div>'
           f'<div class="kpi"><span class="label">Wrong but trusted</span><b>{hw["wrong_but_trusted"]}</b></div>'
           '</div></div>')
        with st.expander("Homework: every labelled exercise"):
            st.dataframe([{"question": x["question"], "agreement": x["agreement"], "answer": x["answer"],
                           "correct": x["voted_correct"]} for x in hw["rows"]], hide_index=True, width="stretch")

    hs = r.get("homework_sim")
    if hs:
        ui('<div class="card"><h3>5. Homework mode: does it nudge instead of telling?</h3>'
           f'<p class="muted">Simulated students help the classmate solve two exercises ({escape(hs["model"])}). '
           'A stuck student should get nudges, then a step worked out; the answer must never be given away '
           'before the student reaches it.</p></div>')
        st.dataframe([{"student": x["profile"], "question": x["question"][:60], "solved": x["solved"],
                       "steps on own": x["steps_on_own"], "nudges": x["nudges"], "steps revealed": x["revealed"],
                       "drafts blocked by guard": x["drafts_blocked"], "leaks reaching student": x["leaks_reaching_student"],
                       "turns": x["turns"]} for x in hs["rows"]], hide_index=True, width="stretch")

    pub_path = HERE / "eval" / "public.json"
    seb = json.loads(pub_path.read_text(encoding="utf-8")).get("scientsbank") if pub_path.exists() else None
    if seb:
        ui('<div class="card"><h3>The grader on real students\' answers (SciEntsBank)</h3>'
           f'<p class="muted">{seb["items"]} answers by real school students, graded by experts (SemEval-2013 Task 7), '
           f'balanced over four labels ({escape(seb["model"])}). Not written by us.</p><div class="kpis">'
           f'<div class="kpi"><span class="label">Agree with experts</span><b>{pct(seb["accuracy_3way"])}</b></div>'
           f'<div class="kpi"><span class="label">Wrong answers given credit</span><b>{seb["wrong_answer_given_full_credit"]}</b></div>'
           f'<div class="kpi"><span class="label">Wrong ideas caught</span><b>{seb["per_label"].get("contradictory", "-")}</b></div>'
           '</div></div>')

    lang_path = HERE / "eval" / "language.json"
    if lang_path.exists():
        lg = json.loads(lang_path.read_text(encoding="utf-8"))
        ui('<div class="card"><h3>Does grading survive Hindi?</h3><p class="muted">The same '
           f'{lg["turns"]} labelled turns, typed as Hinglish (transliterate → translate) and spoken in Hindi '
           '(speak → transcribe → translate), graded by the same grader against the same labels.</p></div>')
        st.dataframe([{"input path": k, "concept credit": pct(x["concept_accuracy"]), "over-credits": x["over_credit"],
                       "planted mistakes": pct(x["plant_accuracy"]), "quality in range": pct(x["quality_in_range"])}
                      for k, x in lg["paths"].items()], hide_index=True, width="stretch")

    pages_path = HERE / "eval" / "pages.json"
    if pages_path.exists():
        pg = json.loads(pages_path.read_text(encoding="utf-8"))
        ui('<div class="card"><h3>6. Real and damaged pages</h3><p class="muted">Real photos (textbooks, a worksheet, '
           'hazy old books, handwriting) through indic-ocr, the lesson card and the exercise finder; and one book page '
           'damaged on purpose, with the PDF\'s own text as the answer key for OCR word recall.</p></div>')
        if pg.get("damage"):
            st.dataframe([{"damage": x["damage"], "indic-ocr word recall": x["word_recall"]}
                          for x in pg["damage"]["rows"]], hide_index=True, width="stretch")
        if pg.get("real"):
            st.dataframe([{"page": x["page"], "pixels": x.get("pixels"), "words read": x.get("ocr_words"),
                           "card ideas grounded": x.get("grounded", "-"), "questions found": x.get("questions_found"),
                           "what the app does": x["outcome"]} for x in pg["real"]], hide_index=True, width="stretch")

    if "session" in ss and getattr(ss.session, "card", None) and ss.session.checks:
        fc = tutor.fact_check_summary(ss.session)
        ui('<div class="card"><h3>7. This session, live</h3><div class="kpis">'
           f'<div class="kpi"><span class="label">Lines checked</span><b>{fc["lines"]}</b></div>'
           f'<div class="kpi"><span class="label">Claims verified true</span><b>{fc["claims_true"]}/{fc["claims"]}</b></div>'
           f'<div class="kpi"><span class="label">Wrong claims blocked</span><b>{fc["false_caught"]}</b></div>'
           f'<div class="kpi"><span class="label">Leaks blocked</span><b>{fc["leaks_caught"]}</b></div></div></div>')

    ui('<div class="card"><h3>Methods this builds on</h3>'
       '<div class="next"><span class="n">1</span><span><b>Feynman Bot</b> (Rajesh &amp; Khan, IEEE TALE 2024, '
       'arXiv 2506.09055): learning by explaining; learning gain = after score - before score.</span></div>'
       '<div class="next"><span class="n">2</span><span><b>AlgoBo / Reflect-Respond</b> (Jin et al., CHI 2024, '
       'arXiv 2309.14534): a teachable agent that only knows what it has been taught.</span></div>'
       '<div class="next"><span class="n">3</span><span><b>Chain-of-Verification</b> (Dhuliawala et al., Findings of '
       'ACL 2024, arXiv 2309.11495): plan checks, answer them independently, then revise.</span></div>'
       '<div class="next"><span class="n">4</span><span><b>LLM-as-a-judge</b> (Zheng et al., NeurIPS 2023, '
       'arXiv 2306.05685): validate an LLM grader against human labels.</span></div>'
       '<div class="next"><span class="n">5</span><span><b>Self-consistency</b> (Wang et al., ICLR 2023, '
       'arXiv 2203.11171): sample several solutions and keep the majority answer.</span></div></div>')


if view == "My progress":
    show_progress(student)
    st.stop()
if view == "Evaluation":
    show_evaluation()
    st.stop()

# ------------------------------------------------------------------ 1. the page, and what the student wants


def request_in_english(typed: str, heard) -> str:
    """What the student asked for, spoken (indic-transcribe) or typed (indic-transliterate), in English."""
    if heard is not None:
        said = bodhan.transcribe(heard.getvalue(), language=None if lang == "en" else lang, filename="request.wav")
    elif typed.strip():
        said = bodhan.transliterate(typed, language=lang) if roman else typed
    else:
        return ""
    return said if lang == "en" else bodhan.translate(said, target_language="en")


def reset_session() -> None:
    for key in ("session", "chat", "report", "quiz", "pre", "post", "pre_picks", "post_picks", "hw_questions",
                "hw_pick", "mode", "note", "finish_now"):
        ss.pop(key, None)


def start_teach(pages: list[tuple[bytes, str]], page_text: str | None = None) -> None:
    label = "the page" if len(pages) == 1 else f"{len(pages)} pages"
    with st.spinner(f"Reading {label} and preparing the lesson..."):
        try:
            ss.page_text, card = lesson.card_from_images(pages, page_text)
        except (bodhan.BodhanError, ValueError) as err:
            st.error(page_error(err))
            st.stop()
        ss.small_text = lesson.pixels_per_word(pages, ss.page_text) < lesson.MIN_PIXELS_PER_WORD
        ss.profile = memory.load(student)
        ss.session = tutor.Session(card=card, band=band, memory=memory.for_session(ss.profile, card["topic"]),
                                   page_text=ss.page_text, fact_check=fact_check_on, buddy=buddy)
        ss.mode, ss.chat = "teach", []
        add_buddy(tutor.start(ss.session), ss.session.opening_check)
    if quick_check:
        with st.spinner("Preparing a 3-question quick check (each question double-checked against the page)..."):
            ss.quiz = quiz.build(card, ss.page_text)
        if not ss.quiz["questions"]:
            ss.quiz = None


def start_homework(page_text: str, hint: str = "") -> bool:
    """False if the page has no exercise questions."""
    with st.spinner("Finding the exercise questions on the page..."):
        try:
            questions = exercise.find_questions(page_text)
        except ValueError:
            questions = []
    if not questions:
        return False
    ss.mode, ss.hw_questions, ss.hw_pick = "homework", questions, intent.pick_question(questions, hint)
    ss.profile = memory.load(student)
    return True


def mode_bar(current: str) -> None:
    """Which mode Samjhao picked and why, with a one-tap switch (before the conversation starts)."""
    it = ss.get("intent")
    if not it:
        return
    other = "homework" if current == "teach" else "teach"
    left, right = st.columns([5, 2], vertical_alignment="center")
    name_ = "Teach mode" if current == "teach" else "Homework mode"
    left.markdown(f'<div class="modepill">{chip(name_)}<span>{escape(it.get("reason", ""))}</span></div>',
                  unsafe_allow_html=True)
    if right.button("Switch to " + ("Homework" if other == "homework" else "Teach"), icon=":material/swap_horiz:",
                    width="stretch"):
        reset_session()
        if other == "homework" and not start_homework(ss.page_text):
            st.toast("No exercise questions on this page, so it stays in Teach mode.")
            start_teach(ss.pages, ss.page_text)
            other = "teach"
        elif other == "teach":
            start_teach(ss.pages, ss.page_text)
        ss.intent = {**it, "mode": other, "reason": "You switched modes."}
        st.rerun()


if "session" not in ss and "hw_questions" not in ss:
    ui('<div class="landing"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div>'
       '<div class="in"><span class="eyebrow">✦ Learn by teaching · 13 Indian languages</span>'
       '<h1 class="wordmark">Samjhao</h1><div class="deva">समझाओ · explain it, and you have learned it</div>'
       '<p>Snap a page from your book. Teach it to a classmate who missed the class, or solve your homework '
       'together, in your own language.</p>'
       '<div class="feats"><span class="feat">📷 <b>Photo or PDF</b></span><span class="feat">🎙️ <b>Type or speak</b>'
       '</span><span class="feat">🕵️ <b>Catch the trick</b></span><span class="feat">📈 <b>See your growth</b></span>'
       '</div></div></div>')

    with st.container(border=True, key="composer"):
        ui('<div class="steptitle"><span class="stepno">1</span>Add your page</div>')
        uploads = st.file_uploader("Photos of your textbook pages, or a PDF of the book",
                                   type=["png", "jpg", "jpeg", "webp", "pdf"], accept_multiple_files=True,
                                   help=f"Up to {lesson.MAX_PAGES} pages of one lesson, in reading order.")
        pdf = next((u for u in uploads or [] if u.name.lower().endswith(".pdf")), None)
        if pdf:  # a whole book: pick the pages of today's lesson
            try:
                n_pages = lesson.pdf_page_count(pdf.getvalue())
            except Exception:
                st.error("Could not open this PDF.")
                st.stop()
            a, b = st.columns(2)
            first = a.number_input(f"Start at page (of {n_pages})", 1, n_pages, 1)
            count = b.number_input("How many pages", 1, lesson.MAX_PAGES, 1)
        if uploads and not pdf and len(uploads) > lesson.MAX_PAGES:
            st.caption(f"Only the first {lesson.MAX_PAGES} pages will be used, to keep one lesson to one session.")
        s1, s2 = st.columns(2)
        sample_lesson = s1.button("Try a sample lesson page", icon=":material/auto_stories:", width="stretch")
        sample_hw = s2.button("Try a sample homework page", icon=":material/edit_note:", width="stretch")

        ui('<div class="steptitle" style="margin-top:14px"><span class="stepno">2</span>What do you want to do?'
           '&nbsp;<span class="muted">(optional)</span></div>')
        c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
        typed = c1.text_input("Type it", placeholder="“help me solve question 2” or “I have a test on this tomorrow”",
                              label_visibility="collapsed")
        heard = c2.audio_input("Or say it", label_visibility="collapsed")
        go = st.button("Let's go", type="primary", icon=":material/arrow_forward:", width="stretch", disabled=not uploads)
        ui('<div class="muted" style="text-align:center;margin-top:6px">Samjhao picks <b>Teach</b> or '
           '<b>Homework</b> from what you say and what is on the page. You can switch before you start.</div>')

    ui('<div class="tiles">'
       '<div class="tile"><div class="ic">📖</div><div class="t">Teach it</div><div class="d">Explain the page to a '
       'classmate who missed the class. They ask, and sometimes slip in a mistake for you to catch.</div></div>'
       '<div class="tile"><div class="ic">✏️</div><div class="t">Solve it</div><div class="d">Stuck on homework? Work '
       'through it step by step. You get nudges, never just the answer.</div></div>'
       '<div class="tile"><div class="ic">🛡️</div><div class="t">Always checked</div><div class="d">Every reply is '
       'fact-checked against your page before you see it, and every score is measured.</div></div></div>')

    def chosen_pages() -> list[tuple[bytes, str]]:
        if pdf:
            return lesson.pdf_pages(pdf.getvalue(), int(first), int(count))
        return [(u.getvalue(), u.name) for u in uploads[:lesson.MAX_PAGES]]

    if sample_lesson:
        ss.pages = [(SAMPLE.read_bytes(), SAMPLE.name)]
        ss.intent = {"mode": "teach", "reason": "A lesson page: teach it to your classmate.", "question": "", "by": "sample"}
        start_teach(ss.pages)
        st.rerun()
    if sample_hw:
        ss.pages = [(EXERCISE_SAMPLE.read_bytes(), EXERCISE_SAMPLE.name)]
        with st.spinner("Reading the page with indic-ocr..."):
            try:
                ss.page_text = lesson.read_pages(ss.pages)
            except bodhan.BodhanError as err:
                st.error(page_error(err))
                st.stop()
        ss.intent = {"mode": "homework", "reason": "An exercise page: solve it together.", "question": "", "by": "sample"}
        start_homework(ss.page_text)
        st.rerun()
    if go:
        pages = chosen_pages()
        with st.status("Getting your page ready...", expanded=True) as status:
            try:
                if heard is not None or typed.strip():
                    status.write("Understanding what you asked (Bodhan " +
                                 ("indic-transcribe" if heard is not None else "indic-transliterate") + ")...")
                request = request_in_english(typed, heard)
                status.write("Reading the page (indic-ocr)...")
                page_text = lesson.read_pages(pages)
                status.write("Choosing Teach or Homework...")
                choice = intent.classify(request, page_text)
            except bodhan.BodhanError as err:
                status.update(label="Could not read that", state="error")
                st.error(page_error(err))
                st.stop()
            status.update(label=("Homework mode" if choice["mode"] == "homework" else "Teach mode")
                          + f": {choice['reason']}", state="complete", expanded=False)
        ss.pages, ss.page_text, ss.intent = pages, page_text, {**choice, "request": request}
        if choice["mode"] == "homework" and not start_homework(page_text, choice.get("question", "")):
            ss.intent = {**ss.intent, "mode": "teach",
                         "reason": "No exercise questions on this page, so let's teach it instead."}
        if ss.intent["mode"] == "teach":
            start_teach(pages, page_text)
        st.rerun()
    st.stop()

# ------------------------------------------------------------------ homework mode

if ss.get("mode") == "homework":
    if ss.get("small_text"):
        st.warning(SMALL_TEXT, icon=":material/photo_camera:")
    if "session" not in ss:
        mode_bar("homework")
        ui('<div class="hero"><div class="tag">Homework mode</div><h1>Solve it together.</h1>'
           f'<p>{escape(buddy)} is stuck on a question from this page. Pick one and help solve it, '
           'step by step. If you get stuck too, they will nudge you, but they will not just tell you.</p></div>')
        with st.container(border=True):
            q = st.radio("Exercise questions found on the page", ss.hw_questions, index=ss.get("hw_pick", 0))
            if st.button("Solve this one", icon=":material/play_arrow:", type="primary"):
                with st.spinner("Working out the answer 3 times independently, to check it before we start..."):
                    ref = exercise.solve(q, ss.page_text, band)
                ss.session = exercise.Homework(question=q, ref=ref, band=band, page_text=ss.page_text, buddy=buddy)
                ss.chat = []
                add_buddy(*exercise.start(ss.session))
                st.rerun()
        st.stop()

    hw: exercise.Homework = ss.session
    name = hw.cfg["name"]
    buddy_face, student_face = avatar(name[0], PERSONA_COLOR[name]), avatar((student or "?")[0], STUDENT_COLOR)
    dots = "".join(f'<span class="dot {STEP[st_][2]}"></span>' for st_ in hw.steps)
    own = sum(st_ == "explained" for st_ in hw.steps)
    trust = chip(f'answer checked: {hw.ref["agreement"]} solutions agree', "green" if hw.ref["trusted"] else "amber")
    ui('<div class="card"><div class="head">'
       f'<div style="max-width:560px"><span class="label">Homework question</span>'
       f'<div class="topic" style="font-size:1.1rem">{escape(hw.question)}</div>'
       f'{chip(f"Helping {name}")}{chip(hw.cfg["label"], "gray")}{chip(LANGS[lang], "gray")}{trust}</div>'
       f'<div><span class="label">Steps solved</span><div class="dots">{dots}</div>'
       f'<div class="muted">{own} on your own · nudges: {sum(hw.nudges)}</div></div></div></div>')
    if not hw.ref["trusted"]:
        ui('<div class="banner"><b>Not graded.</b> The answer was worked out 3 times and the answers did not agree, '
           f'so {escape(name)} is not sure either. Solve it together, then check with your teacher.</div>')
    if teacher_view:
        with st.expander("Answer key (self-consistency: solved 3 times independently)", icon=":material/visibility:"):
            st.write("Steps: " + " → ".join(hw.ref["steps"]))
            st.write(f"Final answer: **{hw.ref['final_answer']}** · the three solutions said: "
                     + " | ".join(hw.ref["answers"]))
    with st.expander(f"{name}'s notebook: what {name} has learned from you ({len(hw.learned)})",
                     icon=":material/menu_book:"):
        if hw.learned:
            ui("".join(f'<div class="row"><div class="said">{escape(n["text"])}</div>{chip(n["how"], "gray")}</div>'
                       for n in hw.learned))
        else:
            st.write("Nothing yet. Start explaining!")

    render_chat(name, buddy_face, student_face)
    if ss.pop("finish_now", False):
        finish()
        st.rerun()
    if "report" not in ss:
        message = read_message(f"Help {name} solve it...")
        if message:
            take_turn(name, message, exercise)
        st.stop()

    rep = ss.report
    total = len(rep["steps"])
    share = round(100 * own / total) if total else 0
    verdict = ("not graded" if not rep["answer_trusted"] else "solved" if rep["solved"] else "not solved yet")
    ui('<div class="card"><h3>How it went</h3><div class="score">'
       f'<div class="ring" style="--p:{share}"><div>{own}/{total}<small>steps on your own</small></div></div>'
       '<div class="stats">'
       f'<div class="stat"><span class="label">Answer</span><b>{escape(verdict)}</b></div>'
       f'<div class="stat"><span class="label">Nudges</span><b>{rep["nudges"]}</b></div>'
       f'<div class="stat"><span class="label">Turns</span><b>{rep["turns"]}</b></div></div></div></div>')
    steps = "".join(f'<div class="row"><div class="name">{escape(x["step"])}</div>{chip(*STEP[x["status"]][:2])}</div>'
                    for x in rep["steps"])
    errors = "".join(f'<div class="row"><div class="name">You said "{escape(e)}"</div>{chip("check this", "red")}</div>'
                     for e in rep["errors"])
    answer = (f'<div class="quote">Answer: {escape(rep["reference_answer"])} ({rep["agreement"]} independent '
              'solutions agree)</div>' if rep["answer_trusted"] else
              '<div class="quote">The answer key was uncertain, so this question was not graded.</div>')
    ui(f'<div class="card"><h3>The solution, step by step</h3>{steps}{errors}{answer}</div>')
    if rep.get("doubts_for_teacher"):
        ui('<div class="card"><h3>Doubts to ask your teacher</h3><p class="muted">Write these in your doubt '
           'notebook: things the app could not check, or steps you needed help with.</p>'
           + "".join(f'<div class="next"><span class="n">{i}</span><span>{escape(d)}</span></div>'
                     for i, d in enumerate(rep["doubts_for_teacher"], 1)) + "</div>")
    ui(f'<div class="muted">Saved to {escape(student)}\'s homework history (see <b>My progress</b>). '
       f'The answer guard checked every line {escape(name)} said; {rep["rewritten"]} were rewritten so they would '
       'not give the answer away.</div>')
    st.stop()

if ss.get("small_text"):
    st.warning(SMALL_TEXT, icon=":material/photo_camera:")

s: tutor.Session = ss.session
name = s.cfg["name"]
buddy_face = avatar(name[0], PERSONA_COLOR[name])
student_face = avatar((student or "?")[0], STUDENT_COLOR)

statuses = [s.concepts[c["id"]]["status"] for c in s.card["concepts"]]
explained = statuses.count("explained")
caught = sum(p["status"] in ("caught", "caught_after_hint") for p in s.plants.values())
dots = "".join(
    f'<span class="dot {"on" if st_ == "explained" else "half" if st_ == "partial" else ""}"'
    + (f' title="{escape(c["name"])}"' if teacher_view else "") + "></span>"
    for st_, c in zip(statuses, s.card["concepts"], strict=True))
ui('<div class="card"><div class="head">'
   f'<div><span class="label">Today\'s page</span><div class="topic">{escape(s.card["topic"])}</div>'
   f'{chip(f"Teaching {name}")}{chip(s.cfg["label"], "gray")}{chip(LANGS[lang], "gray")}</div>'
   f'<div><span class="label">Ideas taught</span><div class="dots">{dots}</div>'
   f'<div class="muted">{explained} of {len(statuses)} · tricks caught: {caught}</div></div>'
   '</div></div>')
if s.turn == 0:  # switching is offered only before the conversation starts
    mode_bar("teach")
if s.memory:
    tricky = " and will check the parts that were tricky last time" if s.focus or s.retest else ""
    ui(f'<div class="banner"><b>Welcome back, {escape(student)}!</b> Last time you taught '
       f'<i>{escape(s.memory["last_topic"])}</i>. {escape(name)} starts at level {s.level}{tricky}.</div>')
if teacher_view:
    with st.expander("Lesson card (what the classmate secretly knows)", icon=":material/visibility:"):
        st.json(s.card, expanded=False)

if ss.get("quiz") and "pre" not in ss:
    quiz_form("pre")
    st.stop()

with st.expander(f"{name}'s notebook: what {name} has learned from you ({len(s.learned)})",
                 icon=":material/menu_book:"):
    st.caption(f"{name} missed the class, so {name} only knows what you teach. Every reply is written from this "
               "notebook alone (the Reflect-Respond idea from the AlgoBo study).")
    if s.learned:
        ui("".join(f'<div class="row"><div class="said">{escape(n["text"])}</div>{chip(n["how"], "gray")}</div>'
                   for n in s.learned))
    else:
        st.write("Nothing yet. Start teaching!")

# ------------------------------------------------------------------ 2. the conversation

render_chat(name, buddy_face, student_face)

if ss.pop("finish_now", False):
    finish()
    st.rerun()  # redraw the sidebar without the finish button

if "report" not in ss:
    message = read_message(f"Explain it to {name}...")
    if message:
        take_turn(name, message, tutor)

# ------------------------------------------------------------------ 3. the report

if "report" in ss:
    rep = ss.report
    prev = rep.get("progress", {}).get("previous_mastery")
    delta = "" if prev is None else chip(f"{rep['mastery'] - prev:+d} vs last time",
                                         "green" if rep["mastery"] >= prev else "red")
    answered = [p for p in rep["planted"] if p["outcome"] != "planted"]
    tricks = (f'{sum(p["outcome"] in ("caught", "caught_after_hint") for p in answered)}/{len(answered)}'
              if answered else "none yet")
    ui('<div class="card"><h3>How it went</h3><div class="score">'
       f'<div class="ring" style="--p:{rep["mastery"]}"><div>{rep["mastery"]}%<small>understanding</small></div></div>'
       '<div class="stats">'
       f'<div class="stat"><span class="label">Tricks caught</span><b>{escape(tricks)}</b></div>'
       f'<div class="stat"><span class="label">Answer quality</span><b>{rep["avg_quality"]}/3</b></div>'
       f'<div class="stat"><span class="label">Turns</span><b>{rep["turns"]}</b></div>'
       f'</div></div><div style="margin-top:10px">{delta}</div></div>')

    ideas = "".join(
        f'<div class="row"><div><div class="name">{escape(c["name"])}</div>'
        + (f'<div class="quote">"{escape(c["said"])}"</div>' if c["said"] else "")
        + f'</div>{chip(*STATUS[c["status"]])}</div>'
        for c in rep["concepts"])
    ui(f'<div class="card"><h3>The ideas on the page</h3>{ideas}</div>')

    checks = [
        f'<div class="row"><div><div class="name">{escape(name)} said "{escape(p["statement"])}"</div>'
        f'<div class="quote">{escape(p["correction"])}</div></div>{chip(*PLANT[p["outcome"]])}</div>'
        for p in rep["planted"]
    ] + [
        f'<div class="row"><div><div class="name">You said "{escape(e["claim"])}"</div>'
        f'<div class="quote">{escape(e["correction"])}</div></div>'
        f'{chip("fixed later", "green") if e["resolved"] else chip("still to fix", "red")}</div>'
        for e in rep["student_errors"]
    ]
    if checks:
        ui('<div class="card"><h3>Tricks and mix-ups</h3>' + "".join(checks) + "</div>")
    if ss.get("quiz"):
        if "post" not in ss:
            quiz_form("post")
        else:
            qz, pre, post = ss.quiz, ss.get("pre"), ss.post
            total = len(qz["questions"])
            gain = "" if pre is None else chip(f"learning gain {post - pre:+d}", "green" if post > pre else "gray")
            review = "".join(
                f'<div class="row"><div><div class="name">{escape(q["q"])}</div>'
                f'<div class="quote">Answer: {escape(q["options"][q["answer"]])} · page: "{escape(q["evidence"])}"</div>'
                f'</div>{chip("right", "green") if pick == q["answer"] else chip("not yet", "amber")}</div>'
                for q, pick in zip(qz["questions"], ss.post_picks, strict=True))
            ui('<div class="card"><h3>Quick check: before and after</h3><div class="stats" style="margin-bottom:10px">'
               f'<div class="stat"><span class="label">Before teaching</span><b>{"-" if pre is None else pre}/{total}</b></div>'
               f'<div class="stat"><span class="label">After teaching</span><b>{post}/{total}</b></div>'
               f'<div class="stat"><span class="label">Gain</span><b>{"-" if pre is None else f"{post - pre:+d}"}</b></div>'
               f'</div>{gain}{review}</div>')

    fc = rep["fact_check"]
    if fc["lines"]:
        ui('<div class="card"><h3>Behind the scenes: was the classmate always right?</h3>'
           f'<p class="muted">Every line {escape(name)} said was fact-checked against your page before you saw it '
           '(Chain-of-Verification), and checked for words you had not taught yet.</p><div class="kpis">'
           f'<div class="kpi"><span class="label">Lines checked</span><b>{fc["lines"]}</b></div>'
           f'<div class="kpi"><span class="label">Claims verified true</span><b>{fc["claims_true"]}/{fc["claims"]}</b></div>'
           f'<div class="kpi"><span class="label">Wrong claims blocked</span><b>{fc["false_caught"]}</b></div>'
           f'<div class="kpi"><span class="label">Leaked words blocked</span><b>{fc["leaks_caught"]}</b></div>'
           f'<div class="kpi"><span class="label">Replies rewritten</span><b>{fc["regenerated"]}</b></div>'
           f'<div class="kpi"><span class="label">Tricks confirmed planted</span><b>{fc["plants_verified"]}</b></div>'
           '</div></div>')
    ui(f'<div class="muted" style="margin-bottom:14px">Saved to {escape(student)}\'s memory: next time {escape(name)} '
       'will start from here. Open <b>My progress</b> in the sidebar to see the whole journey.</div>')

    with st.container(border=True):
        st.markdown("**A note for parents**")
        col1, col2 = st.columns([2, 1], vertical_alignment="bottom")
        plang = col1.selectbox("Parent's language", [c for c in LANGS if c in bodhan.VOICES or c == "en"],
                               index=list(LANGS).index(lang) if lang in bodhan.VOICES else 0, format_func=LANGS.get)
        if col2.button("Write the note", icon=":material/edit_note:", type="primary", width="stretch"):
            with st.spinner("Writing, translating (indic-translate) and recording (indic-speak)..."):
                ss.note = parent.parent_note(rep, plang)
        if "note" in ss:
            ui(f'<div class="said" style="margin-top:6px">{escape(ss.note["text"])}</div>')
            if ss.note["text"] != ss.note["english"]:
                ui(f'<div class="translation">{escape(ss.note["english"])}</div>')
            if ss.note["audio"]:
                st.audio(ss.note["audio"], format="audio/wav")
