"""Teach-Back Tutor app: photograph a textbook page, then teach it to a classmate who missed the class.

    streamlit run app.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

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
STATUS_LABEL = {"explained": "explained", "partial": "partly", "not_covered": "not yet"}
PLANT_LABEL = {"caught": "caught it", "caught_after_hint": "caught it after a hint", "revealed": "missed it",
               "planted": "no answer yet"}

st.set_page_config(page_title="Teach-Back Tutor", layout="centered")
ss = st.session_state

with st.sidebar:
    view = st.radio("View", ["Teach", "My progress"], horizontal=True)
    st.header("Settings")
    student = st.text_input("Student name", "Aarav", help="The tutor remembers each student between sessions.")
    band = st.selectbox("Class", list(tutor.BANDS), index=1, format_func=lambda b: tutor.BANDS[b]["label"])
    lang = st.selectbox("Language for talking", list(LANGS), index=1, format_func=LANGS.get)
    roman = lang != "en" and st.checkbox("I type my language in English letters (e.g. Hinglish)", value=True)
    voice_on = lang in bodhan.VOICES and st.checkbox("Classmate speaks out loud", value=True)
    teacher_view = st.checkbox("Teacher view (scores and moves)")
    if "session" in ss and "report" not in ss and ss.session.turn and st.button("Finish session now"):
        ss.finish_now = True
    if st.button("Start over"):
        for key in list(ss.keys()):
            del ss[key]
        st.rerun()


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


def show_progress(name: str) -> None:
    data = memory.skills(memory.load(name))
    st.title(f"{name}'s progress")
    if not data["sessions"]:
        st.info("No sessions yet. Teach a page to the classmate and your progress will show up here.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Sessions", data["sessions"])
    c2.metric("Level", data["level"])
    c3.metric("Ideas mastered", f"{data['mastered_total']}/{data['concepts_total']}")

    st.subheader("Thinking skills")
    for skill in data["skills"]:
        if skill["value"] is None:
            st.progress(0.0, text=f"{skill['name']}: not tested yet")
            continue
        trend = "" if skill["trend"] is None else f" ({skill['trend']:+d} vs before)"
        st.progress(skill["value"] / 100, text=f"{skill['name']}: {skill['value']}%{trend}")

    st.subheader("Ideas you have mastered")
    for t in data["topics"]:
        with st.expander(f"{t['topic']}: {t['mastery']}% last time", expanded=True):
            st.write("Mastered: " + (", ".join(t["mastered"]) or "none yet"))
            st.write("Still to practise: " + (", ".join(t["to_practice"]) or "nothing, well done!"))

    if len(data["history"]) > 1:
        st.subheader("Understanding over sessions")
        st.line_chart({"Understanding %": [h["mastery"] for h in data["history"]]})

    st.subheader("Practise next")
    for step in data["next_steps"]:
        st.write(f"- {step}")


if view == "My progress":
    show_progress(student)
    st.stop()

# ------------------------------------------------------------------ 1. the page

st.title("Teach-Back Tutor")
if "session" not in ss:
    st.write("Photograph a page from your textbook. Then teach it to your classmate, who missed the class. "
             "Your classmate will ask questions, and will sometimes say something wrong on purpose: catch it!")
    upload = st.file_uploader("Textbook page photo", type=["png", "jpg", "jpeg"])
    use_sample = st.button("Use the sample page (photosynthesis)")
    if upload or use_sample:
        image, name = (upload.getvalue(), upload.name) if upload else (SAMPLE.read_bytes(), SAMPLE.name)
        with st.spinner("Reading the page with indic-ocr and preparing the lesson..."):
            try:
                ss.page_text, card = lesson.card_from_image(image, name)
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
st.caption(f"Topic: **{s.card['topic']}** · classmate: **{name}** · {s.cfg['label']}")
if s.memory:
    st.info(f"Welcome back, {student}! Last time you taught *{s.memory['last_topic']}*. "
            f"{name} starts at level {s.level}" + (" and will check the parts that were tricky last time."
                                                   if s.focus or s.retest else "."))
if teacher_view:
    with st.expander("Lesson card (what the classmate secretly knows)"):
        st.json(s.card, expanded=False)

# ------------------------------------------------------------------ 2. the conversation

for i, m in enumerate(ss.chat):
    with st.chat_message("user" if m["role"] == "user" else "assistant"):
        st.write(m["text"])
        if m["english"] != m["text"]:
            st.caption(m["english"])
        if m.get("audio"):
            st.audio(m["audio"], format="audio/wav", autoplay=i == len(ss.chat) - 1)
        if teacher_view and m.get("info"):
            ev, info = m["info"]["eval"], m["info"]
            st.caption(f"quality {ev.get('quality')}/3 · next move: {info['move']} · level {info['level']}"
                       f" · {ev.get('short_feedback', '')}")

if ss.pop("finish_now", False):
    finish()

if "report" not in ss:
    explained = sum(c["status"] == "explained" for c in s.concepts.values())
    st.progress(explained / len(s.concepts), text=f"Ideas taught: {explained}/{len(s.concepts)}")
    heard = st.audio_input("Speak your answer")
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
    st.divider()
    st.subheader("How it went")
    col1, col2, col3 = st.columns(3)
    prev = rep.get("progress", {}).get("previous_mastery")
    col1.metric("Understanding", f"{rep['mastery']}%", None if prev is None else f"{rep['mastery'] - prev:+d} vs last time")
    col2.metric("Tricks caught", rep["mistakes_caught"].split(" on")[0] if rep["planted"] else "-")
    col3.metric("Answer quality", f"{rep['avg_quality']}/3")
    st.table([{"Idea": c["name"], "Taught?": STATUS_LABEL[c["status"]], "You said": c["said"]} for c in rep["concepts"]])
    for p in rep["planted"]:
        st.write(f"- {name} said *\"{p['statement']}\"*: you {PLANT_LABEL[p['outcome']]}. {p['correction']}")
    for e in rep["student_errors"]:
        st.write(f"- You said *\"{e['claim']}\"* ({'fixed later' if e['resolved'] else 'still to fix'}). {e['correction']}")
    st.caption(f"Saved to {student}'s memory: next time {name} will start from here.")

    st.subheader("Note for parents")
    plang = st.selectbox("Parent's language", [c for c in LANGS if c in bodhan.VOICES or c == "en"],
                         index=list(LANGS).index(lang) if lang in bodhan.VOICES else 0, format_func=LANGS.get)
    if st.button("Write the note"):
        with st.spinner("Writing, translating (indic-translate) and recording (indic-speak)..."):
            ss.note = parent.parent_note(rep, plang)
    if "note" in ss:
        st.write(ss.note["text"])
        if ss.note["text"] != ss.note["english"]:
            st.caption(ss.note["english"])
        if ss.note["audio"]:
            st.audio(ss.note["audio"], format="audio/wav")
