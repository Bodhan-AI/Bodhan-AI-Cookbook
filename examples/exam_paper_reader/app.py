"""Exam Paper Reader: a Streamlit screen on top of pipeline.py.

    streamlit run app.py

Streamlit reruns this whole script on every click. Paid results (OCR, translations,
audio) are therefore kept in st.session_state, so a click never repeats an API call.
"""

import hashlib
from pathlib import Path

import pipeline
import streamlit as st

SAMPLE = Path(__file__).parent / "sample_data" / "sample_paper.png"

st.set_page_config(page_title="Exam Paper Reader", page_icon="📝", layout="wide")
state = st.session_state
state.setdefault("translations", {})  # (image hash, language) -> list of translated lines
state.setdefault("audio", {})  # (image hash, language, line index or "all") -> WAV bytes
state.setdefault("answers", {})  # answer box key -> the answer text
state.setdefault("heard", {})  # answer box key -> hash of the last recording written down

# --- sidebar: choices -----------------------------------------------------------

with st.sidebar:
    st.header("Settings")
    lang = st.selectbox(
        "Read the paper in",
        options=list(pipeline.LANGUAGES),
        format_func=lambda code: pipeline.LANGUAGES[code][0],
    )
    voice_answers = st.toggle("Answer questions by voice", value=True)
    st.caption(
        "Uses Bodhan **indic-ocr** to read the page, **indic-translate** to translate it, "
        "**indic-speak** to read it aloud and **indic-transcribe** to write down spoken answers."
    )

st.title("📝 Exam Paper Reader")
st.write(
    "Upload a photo of a question paper. Each question is translated into your "
    "language and can be read aloud. Text already in your language is kept as it is."
)

# --- input: an uploaded photo, or the sample paper -------------------------------

uploaded = st.file_uploader("Photo or scan of one page", type=["png", "jpg", "jpeg"])
if uploaded is None and st.button("Try the sample paper"):
    state["use_sample"] = True
if uploaded is not None:
    state["use_sample"] = False

if uploaded is not None:
    raw = uploaded.getvalue()
elif state.get("use_sample"):
    raw = SAMPLE.read_bytes()
else:
    st.stop()  # nothing to show until there is an image

try:
    image, mime = pipeline.prepare_image(raw)
except Exception:
    st.error("This file could not be opened as an image. Please upload a PNG or JPEG.")
    st.stop()
key = hashlib.sha256(image).hexdigest()[:16]

# --- steps 1 and 2: read and translate (each paid call happens once) --------------

try:
    with st.spinner("Reading the paper..."):
        ocr = pipeline.read_paper(image, mime)  # cached on disk by image hash
    units = pipeline.paper_units(ocr)  # (kind, text): a title, a paragraph or a question

    if not units:
        st.warning("No text was found on this page. Try a sharper, well-lit photo.")
        st.stop()

    if (key, lang) not in state.translations:
        progress = st.progress(0.0, text=f"Translating into {pipeline.LANGUAGES[lang][0]}...")
        translated = []
        for i, (_, text) in enumerate(units, start=1):
            translated.append(pipeline.translate(text, lang))
            progress.progress(i / len(units), text=f"Translated {i} of {len(units)} parts")
        progress.empty()
        state.translations[(key, lang)] = translated
    translated = state.translations[(key, lang)]
except RuntimeError as err:  # readable Bodhan API errors from pipeline.bodhan
    st.error(f"The Bodhan API returned an error: {err}")
    st.stop()


def plain(text: str) -> str:
    """Make text from a photo safe to show as Markdown.

    The text comes from whatever was photographed, so it is shown as text only:
    "[" is escaped so it cannot form a link or load a remote image, and the first "."
    is escaped so that "1." stays a question number instead of starting a list.
    """
    return text.replace("[", "\\[").replace(".", "\\.", 1)


def play(audio_key, text: str, label: str) -> None:
    """A button that makes the audio on first press, then an audio player."""
    if audio_key not in state.audio and st.button(label, key=f"btn-{audio_key}"):
        with st.spinner("Generating audio..."):
            try:
                state.audio[audio_key] = pipeline.speak(text, lang)
            except RuntimeError as err:
                st.error(f"Could not generate audio: {err}")
    if audio_key in state.audio:
        st.audio(state.audio[audio_key], format="audio/wav")


def answer_box(box: str, label: str) -> None:
    """A recorder and a text box for one question's answer.

    Each new recording is turned into text once and added to the end of the answer, so
    a long answer can be spoken in parts. The student can correct the text by hand.

    Streamlit forgets a widget's value while the widget is not on screen (for example
    when voice answers are switched off), so the text is also kept in state.answers
    and put back when the box returns.
    """
    if box not in state:
        state[box] = state.answers.get(box, "")
    recording = st.audio_input("Record your answer", key=f"rec-{box}")
    if recording is not None:
        audio = recording.getvalue()
        heard = hashlib.sha256(audio).hexdigest()
        if state.heard.get(box) != heard:  # a new recording, not a rerun of an old one
            state.heard[box] = heard  # marked first: a recording that fails is not sent again
            with st.spinner("Writing down your answer..."):
                try:
                    said = pipeline.transcribe(audio, lang)
                except (RuntimeError, OSError) as err:  # API error, unreadable audio, no network
                    st.error(f"Could not write down the answer. Please record again. ({err})")
                else:
                    if said:
                        state[box] = f"{state[box]} {said}".strip()
                    else:
                        st.warning("No speech was heard. Please record again, closer to the microphone.")
    state.answers[box] = st.text_area(label, key=box, placeholder="Speak, or type here")


# --- results ----------------------------------------------------------------------

left, right = st.columns([2, 3], gap="large")

with left:
    st.subheader("The paper")
    st.image(image, width="stretch")

with right:
    st.subheader(f"In {pipeline.LANGUAGES[lang][0]}")
    play((key, lang, "all"), " ".join(translated), "🔊 Read the whole paper")
    st.divider()
    # Answer boxes go under questions; if none are recognised, under every paragraph.
    questions = [i for i, (_, text) in enumerate(units) if pipeline.is_question(text)]
    if not questions:
        questions = [i for i, (kind, _) in enumerate(units) if kind == "text"]
    for i, ((kind, original), new) in enumerate(zip(units, translated, strict=True)):
        if kind == "title":
            st.markdown(f"#### {plain(new)}")
        else:
            st.markdown(plain(new))
        if new != original:  # nothing to compare when the text was already in this language
            st.caption(plain(original))
        play((key, lang, i), new, "🔊 Read aloud")
        if voice_answers and i in questions:
            with st.expander("🎤 Answer by voice"):
                answer_box(f"answer-{key}-{i}", f"Your answer in {pipeline.LANGUAGES[lang][0]}")

    st.download_button(
        "Download the translation (.txt)",
        data="\n\n".join(translated).encode("utf-8"),
        file_name=f"paper_{lang}.txt",
        mime="text/plain",
    )
    answers = [(units[i][1], state.answers.get(f"answer-{key}-{i}", "")) for i in questions]
    if voice_answers and any(answer for _, answer in answers):
        sheet = "\n\n".join(f"{question}\nAnswer: {answer or '-'}" for question, answer in answers)
        st.download_button(
            "Download my answers (.txt)",
            data=sheet.encode("utf-8"),
            file_name="my_answers.txt",
            mime="text/plain",
        )

st.caption(
    "Translations are machine-generated. Check numbers, formulas and names against the "
    "original paper."
)
