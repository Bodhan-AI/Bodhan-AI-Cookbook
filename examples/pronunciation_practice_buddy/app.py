"""Pronunciation practice buddy: listen, repeat, get word-level feedback.

Run:  streamlit run app.py
"""

from __future__ import annotations

import html
import io
import wave

import bodhan_client as bodhan
import streamlit as st
from dotenv import load_dotenv
from phrases import LANGUAGES, PHRASES, VOICES
from scoring import feedback, score_attempt

load_dotenv()

st.set_page_config(page_title="Pronunciation Practice Buddy", page_icon="🗣️")

# "Careful" is the closest Indic-Speak style to slow, clear speech: the API has no speed knob.
CAREFUL_STYLE = "educational lecture"
COLORS = {"correct": "#1b8a3a", "close": "#c77700", "missed": "#c62828"}


@st.cache_data(show_spinner=False)
def cached_speech(text: str, voice: str, lang: str, style: str | None) -> bytes:
    return bodhan.speak(text, voice=voice, lang=lang, style=style)


@st.cache_data(show_spinner=False)
def cached_roman(text: str, lang: str) -> str:
    return bodhan.transliterate(text, lang, script="roman")


@st.cache_data(show_spinner=False)
def cached_translation(text: str, lang: str) -> str:
    return bodhan.translate(text, lang)


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def render_words(result) -> str:
    spans = [
        f'<span style="color:{COLORS[w.status]};font-size:1.6rem;margin-right:.5rem">'
        f"{html.escape(w.target)}</span>"
        for w in result.words
    ]
    return "".join(spans)


st.title("🗣️ Pronunciation Practice Buddy")
st.caption("Listen to a phrase, say it back, and see which words were recognised. "
           "Built with Bodhan indic-speak, indic-transcribe, indic-transliterate and indic-translate.")

with st.sidebar:
    lang = st.selectbox("Language to practise", list(LANGUAGES), format_func=LANGUAGES.get)
    gender = st.radio("Voice", list(VOICES[lang]), horizontal=True)
    show_roman = st.toggle("Show roman script", value=True)
    st.markdown("**Scoring**\n\n🟢 recognised  🟠 close  🔴 missed")

mode = st.radio("Phrase", ["Pick a phrase", "Type an English phrase"], horizontal=True)
if mode == "Pick a phrase":
    options = PHRASES[lang]
    idx = st.selectbox("Choose", range(len(options)), format_func=lambda i: options[i][1])
    target, meaning = options[idx]
else:
    english = st.text_input("English phrase", "Where is the market?", max_chars=120)
    if not english.strip():
        st.stop()
    try:
        with st.spinner("Translating..."):
            target = cached_translation(english.strip(), lang)
    except bodhan.BodhanError as exc:
        st.error(str(exc))
        st.stop()
    meaning = english.strip()

st.markdown(f"<div style='font-size:2rem'>{html.escape(target)}</div>", unsafe_allow_html=True)
st.caption(meaning)
if show_roman:
    try:
        st.markdown(f"*{cached_roman(target, lang)}*")
    except bodhan.BodhanError as exc:
        st.warning(f"Roman script unavailable: {exc}")

voice = VOICES[lang][gender]
col1, col2 = st.columns(2)
for col, label, style in ((col1, "🔊 Listen", None), (col2, "🐢 Listen carefully", CAREFUL_STYLE)):
    if col.button(label, use_container_width=True):
        try:
            with st.spinner("Generating speech..."):
                st.audio(cached_speech(target, voice, lang, style), format="audio/wav", autoplay=True)
        except bodhan.BodhanError as exc:
            st.error(str(exc))

st.divider()
st.subheader("Your turn")
recording = st.audio_input("Record yourself saying the phrase (up to 30 seconds)")

if recording is not None:
    data = recording.getvalue()
    try:
        if wav_seconds(data) > bodhan.MAX_AUDIO_SECONDS:
            st.error("That recording is longer than 30 seconds. Please record a shorter one.")
            st.stop()
        with st.spinner("Listening..."):
            heard = bodhan.transcribe(data, lang)
    except (bodhan.BodhanError, wave.Error) as exc:
        st.error(f"Could not process the recording: {exc}")
        st.stop()

    result = score_attempt(target, heard)
    st.metric("Score", f"{result.score} / 100")
    st.write(feedback(result))
    st.markdown(render_words(result), unsafe_allow_html=True)
    st.caption(f"We heard: {heard or '(nothing)'}")
    if result.extra_words:
        st.caption("Extra words heard: " + ", ".join(result.extra_words))

    best = st.session_state.setdefault("best", {})
    key = f"{lang}:{target}"
    best[key] = max(best.get(key, 0), result.score)
    st.caption(f"Best score for this phrase this session: {best[key]}")
