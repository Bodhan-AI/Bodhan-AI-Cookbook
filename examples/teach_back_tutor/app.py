"""Teach-Back Tutor app: photograph a textbook page, then teach it to a classmate who missed the class.

    streamlit run app.py
"""

from __future__ import annotations

import hashlib
import io
from html import escape
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import bodhan  # noqa: E402
import lesson  # noqa: E402
import memory  # noqa: E402
import parent  # noqa: E402
import tutor  # noqa: E402

LANGS = {"en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
         "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese",
         "ur": "Urdu"}
SAMPLE = HERE / "sample_data" / "photosynthesis_page.png"
STATUS = {"explained": ("explained", "green"), "partial": ("partly", "amber"), "not_covered": ("not yet", "gray")}
PLANT = {"caught": ("caught it", "green"), "caught_after_hint": ("caught after a hint", "amber"),
         "revealed": ("missed it", "red"), "planted": ("no answer yet", "gray")}
PERSONA_COLOR = {"primary": "#F59E0B", "middle": "#8B5CF6", "secondary": "#0EA5E9"}
STUDENT_COLOR = "#4F46E5"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap');
.stApp, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp p, .stApp li, .stApp label, .stApp button,
.stApp input, .stApp textarea { font-family: 'Nunito', sans-serif; }
.block-container { padding-top: 3.5rem; max-width: 860px; }
.stAppDeployButton { display: none; }
section[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #E6E8F2; }
.stButton > button { border-radius: 12px; font-weight: 700; }

.hero { background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 55%, #DB2777 100%); color: #fff;
  border-radius: 24px; padding: 30px 32px 28px; margin-bottom: 18px;
  box-shadow: 0 18px 40px -20px rgba(79, 70, 229, .65); }
.hero .tag { display: inline-block; background: rgba(255,255,255,.18); padding: 4px 12px; border-radius: 999px;
  font-size: .78rem; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; margin-bottom: 12px; }
.hero h1 { color: #fff; font-weight: 800; font-size: 2.5rem; line-height: 1.1; margin: 0 0 8px; padding: 0; }
.hero p { color: rgba(255,255,255,.93); font-size: 1.08rem; margin: 0; max-width: 620px; }

.steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
@media (max-width: 680px) { .steps { grid-template-columns: repeat(2, 1fr); } }
.step { background: #fff; border: 1px solid #E6E8F2; border-radius: 16px; padding: 14px 14px 12px; }
.step .n { display: inline-flex; width: 28px; height: 28px; border-radius: 50%; background: #EEF0FF; color: #4F46E5;
  align-items: center; justify-content: center; font-weight: 800; margin-bottom: 8px; }
.step .t { display: block; font-weight: 800; margin-bottom: 2px; color: #1E2235; }
.step .d { color: #5B6075; font-size: .87rem; line-height: 1.35; }

.card { background: #fff; border: 1px solid #E6E8F2; border-radius: 18px; padding: 18px 20px; margin-bottom: 14px;
  box-shadow: 0 1px 2px rgba(16,24,40,.04); }
.card h3 { margin: 0 0 10px; font-size: 1.1rem; font-weight: 800; }
.muted { color: #5B6075; font-size: .88rem; }
.label { color: #8A8FA3; font-size: .74rem; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 700;
  background: #EEF0FF; color: #4338CA; margin: 3px 4px 3px 0; white-space: nowrap; }
.chip.green { background: #DCFCE7; color: #166534; } .chip.amber { background: #FEF3C7; color: #92400E; }
.chip.gray { background: #F1F2F6; color: #5B6075; } .chip.red { background: #FEE2E2; color: #991B1B; }

.head { display: flex; justify-content: space-between; gap: 18px; flex-wrap: wrap; align-items: flex-end; }
.head .topic { font-size: 1.45rem; font-weight: 800; margin: 2px 0 6px; color: #1E2235; }
.dots { display: flex; gap: 6px; margin: 6px 0 4px; }
.dot { width: 28px; height: 10px; border-radius: 999px; background: #E6E8F2; }
.dot.on { background: #22C55E; } .dot.half { background: #FBBF24; }
.banner { border-left: 4px solid #8B5CF6; background: #F5F3FF; border-radius: 12px; padding: 12px 16px;
  margin-bottom: 14px; color: #3B2F6B; }

[data-testid="stChatMessage"] { background: #fff; border: 1px solid #E6E8F2; border-radius: 18px;
  padding: 14px 16px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(16,24,40,.04); }
[data-testid="stChatMessage"]:has(.from-student) { background: #EEF0FF; border-color: #DCDFFB; }
.who { font-weight: 800; font-size: .82rem; color: #5B6075; margin-bottom: 2px; }
.said { font-size: 1.04rem; line-height: 1.5; color: #1E2235; }
.translation { color: #6B7085; font-size: .86rem; font-style: italic; border-left: 3px solid #E0E3F5;
  padding-left: 8px; margin-top: 6px; }

.score { display: flex; gap: 22px; align-items: center; flex-wrap: wrap; }
.ring { width: 124px; height: 124px; border-radius: 50%; flex: none; display: flex; align-items: center;
  justify-content: center; background: conic-gradient(#4F46E5 calc(var(--p) * 1%), #E6E8F2 0); }
.ring > div { width: 96px; height: 96px; border-radius: 50%; background: #fff; display: flex; flex-direction: column;
  align-items: center; justify-content: center; font-size: 1.7rem; font-weight: 800; color: #1E2235; line-height: 1; }
.ring small { font-size: .68rem; font-weight: 700; color: #8A8FA3; margin-top: 4px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; flex: 1; min-width: 260px; }
.stat { background: #F7F7FB; border-radius: 14px; padding: 12px 14px; }
.stat b { display: block; font-size: 1.35rem; font-weight: 800; color: #1E2235; }
.row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 10px 0;
  border-bottom: 1px solid #F0F1F6; }
.row:last-child { border-bottom: none; padding-bottom: 0; }
.row .name { font-weight: 700; color: #1E2235; }
.quote { color: #5B6075; font-size: .87rem; font-style: italic; margin-top: 2px; }

.skill { margin-bottom: 14px; }
.skill .top { display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 6px; color: #1E2235; }
.bar { height: 10px; background: #E6E8F2; border-radius: 999px; overflow: hidden; }
.bar > span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #4F46E5, #8B5CF6); }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 14px; }
.kpi { background: #fff; border: 1px solid #E6E8F2; border-radius: 16px; padding: 14px 16px; }
.kpi b { display: block; font-size: 1.6rem; font-weight: 800; color: #1E2235; }
.next { display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; }
.next .n { flex: none; width: 24px; height: 24px; border-radius: 50%; background: #EEF0FF; color: #4F46E5;
  display: inline-flex; align-items: center; justify-content: center; font-weight: 800; font-size: .8rem; }
.brand { font-weight: 800; font-size: 1.25rem; color: #1E2235; }
</style>
"""

st.set_page_config(page_title="Teach-Back Tutor", page_icon=":material/school:", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)
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
    ui('<div class="brand">Teach-Back Tutor</div><div class="muted">Learn it by teaching it</div>')
    st.write("")
    view = st.radio("View", ["Teach", "My progress"], horizontal=True, label_visibility="collapsed")
    st.divider()
    student = st.text_input("Student name", "Aarav", help="The tutor remembers each student between sessions.")
    band = st.selectbox("Class", list(tutor.BANDS), index=1, format_func=lambda b: tutor.BANDS[b]["label"])
    lang = st.selectbox("Language for talking", list(LANGS), index=1, format_func=LANGS.get)
    roman = lang != "en" and st.checkbox("I type my language in English letters (e.g. Hinglish)", value=True)
    voice_on = lang in bodhan.VOICES and st.checkbox("Classmate speaks out loud", value=True)
    teacher_view = st.toggle("Teacher view", help="Show the score, the next move and the level on every turn.")
    st.divider()
    if "session" in ss and "report" not in ss and ss.session.turn and st.button(
            "Finish session now", icon=":material/flag:", width="stretch"):
        ss.finish_now = True
    if st.button("Start over", icon=":material/restart_alt:", width="stretch"):
        for key in list(ss.keys()):
            del ss[key]
        st.rerun()
    ui('<div class="muted" style="margin-top:12px">Powered by Bodhan: indic-ocr, indic-transcribe, indic-translate, '
       'indic-transliterate, indic-speak.</div>')


def localize(text: str) -> str:
    return text if lang == "en" else bodhan.translate(text, target_language=lang)


def add_buddy(english: str) -> None:
    shown = localize(english)
    audio = None
    if voice_on:
        try:
            audio = bodhan.speak_long(shown, lang, style=tutor.BANDS[ss.session.band]["voice_style"])
        except bodhan.BodhanError as err:
            st.toast(f"Could not speak the reply: {err}")
    ss.chat.append({"role": "assistant", "text": shown, "english": english, "audio": audio})


def finish() -> None:
    ss.report = tutor.report(ss.session)
    memory.save(memory.record(ss.profile, ss.session, ss.report))


# ------------------------------------------------------------------ progress dashboard

def show_progress(name: str) -> None:
    data = memory.skills(memory.load(name))
    ui(f'<div class="hero"><div class="tag">My progress</div><h1>{escape(name)}\'s learning journey</h1>'
       '<p>Every session you teach is remembered. Here is what you have mastered and what to practise next.</p></div>')
    if not data["sessions"]:
        ui('<div class="card"><h3>No sessions yet</h3><p class="muted">Teach a page to your classmate and your '
           'progress will show up here.</p></div>')
        return
    ui('<div class="kpis">'
       f'<div class="kpi"><span class="label">Sessions</span><b>{data["sessions"]}</b></div>'
       f'<div class="kpi"><span class="label">Level</span><b>{data["level"]} / 3</b></div>'
       f'<div class="kpi"><span class="label">Ideas mastered</span><b>{data["mastered_total"]} / '
       f'{data["concepts_total"]}</b></div></div>')

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


if view == "My progress":
    show_progress(student)
    st.stop()

# ------------------------------------------------------------------ 1. the page

if "session" not in ss:
    ui('<div class="hero"><div class="tag">Feynman technique · 13 languages</div>'
       '<h1>Teach it to learn it.</h1>'
       '<p>Photograph a page from your textbook and teach it to a classmate who missed the class. '
       'They will ask questions, and sometimes say something wrong on purpose. Can you catch it?</p></div>')
    ui('<div class="steps">'
       '<div class="step"><span class="n">1</span><span class="t">Snap a page</span>'
       '<span class="d">Bodhan reads it, in any Indian language.</span></div>'
       '<div class="step"><span class="n">2</span><span class="t">Teach your friend</span>'
       '<span class="d">Type or speak, in English or your own language.</span></div>'
       '<div class="step"><span class="n">3</span><span class="t">Catch the trick</span>'
       '<span class="d">Your friend will slip in a mistake. Spot it!</span></div>'
       '<div class="step"><span class="n">4</span><span class="t">See your progress</span>'
       '<span class="d">Skills, weak spots, and a note for your parents.</span></div></div>')
    with st.container(border=True):
        left, right = st.columns([3, 2], gap="large")
        with left:
            uploads = st.file_uploader(f"Upload photos of your textbook pages (up to {lesson.MAX_PAGES})",
                                       type=["png", "jpg", "jpeg"], accept_multiple_files=True)
            ui('<div class="muted"><b>Tips for good photos</b><br>Hold the phone flat above the page, in good '
               'light. Pages from one lesson work best; add them in reading order.</div>')
            start = st.button("Start teaching", icon=":material/play_arrow:", type="primary", width="stretch",
                              disabled=not uploads)
        with right:
            st.image(str(SAMPLE), width="stretch")
            use_sample = st.button("Try the sample page", icon=":material/auto_stories:", width="stretch")
    if uploads and len(uploads) > lesson.MAX_PAGES:
        st.warning(f"Only the first {lesson.MAX_PAGES} pages will be used, to keep one lesson to one session.")
    if (uploads and start) or use_sample:
        pages = ([(u.getvalue(), u.name) for u in uploads[:lesson.MAX_PAGES]] if uploads and start
                 else [(SAMPLE.read_bytes(), SAMPLE.name)])
        label = "the page" if len(pages) == 1 else f"{len(pages)} pages"
        with st.spinner(f"Reading {label} with indic-ocr and preparing the lesson..."):
            try:
                ss.page_text, card = lesson.card_from_images(pages)
            except (bodhan.BodhanError, ValueError) as err:
                st.error(str(err))
                st.stop()
            ss.profile = memory.load(student)
            ss.session = tutor.Session(card=card, band=band, memory=memory.for_session(ss.profile, card["topic"]))
            ss.chat = []
            add_buddy(tutor.start(ss.session))
        st.rerun()
    st.stop()

s: tutor.Session = ss.session
name = s.cfg["name"]
buddy_face = avatar(name[0], PERSONA_COLOR[s.band])
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
if s.memory:
    tricky = " and will check the parts that were tricky last time" if s.focus or s.retest else ""
    ui(f'<div class="banner"><b>Welcome back, {escape(student)}!</b> Last time you taught '
       f'<i>{escape(s.memory["last_topic"])}</i>. {escape(name)} starts at level {s.level}{tricky}.</div>')
if teacher_view:
    with st.expander("Lesson card (what the classmate secretly knows)", icon=":material/visibility:"):
        st.json(s.card, expanded=False)

# ------------------------------------------------------------------ 2. the conversation

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
        ui(body + "</div>")
        if m.get("audio"):
            st.audio(m["audio"], format="audio/wav", autoplay=i == len(ss.chat) - 1)

if ss.pop("finish_now", False):
    finish()
    st.rerun()  # redraw the sidebar without the finish button

if "report" not in ss:
    heard = st.audio_input("Or answer out loud", help="Speak in the language chosen in the sidebar.")
    typed = st.chat_input(f"Explain it to {name}...")
    message = None  # (what the student said, in English for grading)
    try:
        if heard is not None and ss.get("last_audio") != hashlib.sha1(heard.getvalue()).hexdigest():
            ss.last_audio = hashlib.sha1(heard.getvalue()).hexdigest()
            with st.spinner("Listening with indic-transcribe..."):
                said = bodhan.transcribe(heard.getvalue(), language=None if lang == "en" else lang,
                                         filename="speech.wav")
                message = (said, said if lang == "en" else bodhan.translate(said, target_language="en"))
        elif typed:
            said = bodhan.transliterate(typed, language=lang) if roman else typed
            message = (said, said if lang == "en" else bodhan.translate(said, target_language="en"))
    except bodhan.BodhanError as err:
        st.error(f"Bodhan API error: {err}")

    if message:
        said, english = message
        ss.chat.append({"role": "user", "text": said, "english": english})
        with st.spinner(f"{name} is thinking..."):
            reply, info = tutor.step(s, english)
            ss.chat[-1]["info"] = info
            add_buddy(reply)
        if s.done:
            finish()
        st.rerun()

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
