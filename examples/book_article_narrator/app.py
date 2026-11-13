"""The Streamlit page. It only collects input and shows results; the work is in narrator/."""

import os
import re
import sqlite3
from datetime import UTC, datetime

import streamlit as st
from narrator import config, storage
from narrator.bodhan_client import BodhanClient
from narrator.demo_client import DemoClient
from narrator.errors import BodhanAPIError, NarratorError
from narrator.extract import extract_pdf_text
from narrator.pipeline import NarrationResult, narrate
from narrator.text_utils import split_paragraphs

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title="Book/Article Narrator", layout="wide")
PALETTES = {
    "light": {
        "bg1": "#f8f6f1", "bg2": "#fff4e6", "card": "#ffffff", "line": "#e7e2d8",
        "text": "#0a0a0a", "muted": "#5c534a", "chip": "#ffffff",
    },
    "dark": {
        "bg1": "#0a0a0a", "bg2": "#171717", "card": "#171717", "line": "#2e2e2e",
        "text": "#f8f6f1", "muted": "#a1a1a1", "chip": "#171717",
    },
}


def current_theme():
    """'dark' when the user chose dark (or their computer is dark), else 'light'."""
    theme = getattr(st.context, "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Syne:wght@700;800&family=Noto+Sans+Devanagari:wght@400;600&family=Noto+Sans+Tamil:wght@400;600&family=Noto+Sans+Bengali:wght@400;600&family=Noto+Sans+Gujarati:wght@400;600&family=Noto+Sans+Gurmukhi:wght@400;600&family=Noto+Sans+Kannada:wght@400;600&family=Noto+Sans+Malayalam:wght@400;600&family=Noto+Sans+Oriya:wght@400;600&family=Noto+Sans+Telugu:wght@400;600&family=Noto+Sans+Meetei+Mayek:wght@400;600&family=Noto+Sans+Ol+Chiki:wght@400;600&family=Noto+Naskh+Arabic:wght@400;600&display=swap');
    :root {
        --orange: #ff6207;
        --line: color-mix(in srgb, currentColor 16%, transparent);
        --card: color-mix(in srgb, currentColor 5%, transparent);
    }

    .stApp {
        background-image: linear-gradient(180deg, transparent 0%, rgba(255, 98, 7, 0.07) 100%);
    }
    header[data-testid="stHeader"] { background: transparent; }
    .stAppDeployButton, [data-testid="stDecoration"], footer { display: none; }
    .block-container { max-width: 1050px; padding-top: 1.5rem; }

    .stApp, .stApp p, .stApp label, .stApp li, .stApp textarea,
    .stApp input, .stApp button, .stMarkdown {
        font-family: 'Manrope', 'Noto Sans Devanagari', 'Noto Sans Tamil',
                     'Noto Sans Bengali', 'Noto Sans Gujarati', 'Noto Sans Gurmukhi',
                     'Noto Sans Kannada', 'Noto Sans Malayalam', 'Noto Sans Oriya',
                     'Noto Sans Telugu', 'Noto Sans Meetei Mayek', 'Noto Sans Ol Chiki',
                     'Noto Naskh Arabic', sans-serif;
    }
    .stApp h1, .stApp h2, .stApp h3 {
        font-family: 'Syne', 'Manrope', sans-serif; letter-spacing: -0.02em;
    }
    .stApp h1 { font-size: 2.4rem; font-weight: 800; padding-top: 0.5rem; }

    .brand-bar {
        display: flex; justify-content: space-between; align-items: center;
        padding: 0.4rem 0 1rem; border-bottom: 1px solid var(--line);
        margin-bottom: 1.5rem;
    }
    .brand { font-family: 'Syne', sans-serif; font-weight: 800; font-size: 1.6rem; }
    .brand span { color: var(--orange); }
    .pill {
        background: var(--card); border: 1px solid var(--line); border-radius: 999px;
        padding: 0.3rem 0.9rem; font-size: 0.8rem; font-weight: 600;
    }
    .chips span {
        display: inline-block; background: var(--card); border: 1px solid var(--line);
        border-radius: 999px; padding: 0.15rem 0.7rem; margin: 0 0.4rem 0.4rem 0;
        font-size: 0.8rem; font-weight: 600;
    }

    .stButton > button[kind="primary"] {
        background: var(--orange); color: #fff; border: none; border-radius: 999px;
        padding: 0.7rem 2rem; font-weight: 700;
        box-shadow: 0 6px 16px rgba(255, 98, 7, 0.28);
    }
    .stButton > button[kind="primary"]:hover { background: #e55300; }
    .stDownloadButton > button { border-radius: 999px; font-weight: 600; }

    [data-testid="stFileUploader"] section {
        border: 2px dashed var(--line); border-radius: 18px;
    }
    [data-testid="stTextArea"] textarea {
        border-radius: 16px; border: 1px solid var(--line);
        font-size: 1rem; line-height: 1.6;
    }
    [data-baseweb="select"] > div { border-radius: 12px; border: 1px solid var(--line); }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 16px; border-color: var(--line);
    }
    [data-testid="stVerticalBlockBorderWrapper"] p {
    unicode-bidi: plaintext; text-align: start;
    }
    .st-key-input_bar [data-testid="stElementContainer"]:has(button),
    .st-key-input_bar [data-testid="stButton"] {
        display: flex; justify-content: flex-end; width: 100%;
    }
    .st-key-input_bar button { padding: 0.15rem 0.6rem; opacity: 0.75; }
    .st-key-input_bar button:hover { opacity: 1; color: var(--orange); }
    </style>
    """,
    unsafe_allow_html=True,
)

   

st.markdown(
    '<div class="brand-bar"><div class="brand">Bodhan<span>.AI</span></div>'
    '<div class="pill">Book/Article Narrator · Translate + Speak</div></div>',
    unsafe_allow_html=True,
)


DEMO_MODE = os.environ.get("NARRATOR_DEMO") == "1"


def get_client():
    """Demo client when NARRATOR_DEMO=1, otherwise the real Bodhan client."""
    if DEMO_MODE:
        return DemoClient()
    return BodhanClient()


def trim_to_limit(text):
    """Cut at the character limit, backing up to the end of a sentence if possible."""
    if len(text) <= config.MAX_INPUT_CHARS:
        return text
    cut = text[: config.MAX_INPUT_CHARS]
    end = max(cut.rfind(mark) for mark in ".?!।")
    return cut[: end + 1] if end > len(cut) // 2 else cut


INDIC_PATTERN = re.compile("[\u0600-\u06ff\u0900-\u0dff\u1c50-\u1c7f\uabc0-\uabff]")


def uploader_key():
    """The PDF box gets a new key when cleared, which empties it."""
    return f"pdf_file_{st.session_state.get('uploader_round', 0)}"


def load_pdf():
    """Runs when a PDF is uploaded: put its text in the text box."""
    st.session_state.pop("pdf_error", None)
    st.session_state.pop("pdf_notice", None)
    uploaded = st.session_state.get(uploader_key())
    if uploaded is None:
        return
    try:
        extracted = extract_pdf_text(uploaded.getvalue())
    except NarratorError as error:
        st.session_state["pdf_error"] = str(error)
        return
    trimmed = trim_to_limit(extracted)
    notices = []
    if len(trimmed) < len(extracted):
        notices.append(
            f"The PDF has {len(extracted)} characters. Only the first {len(trimmed)} "
            f"were loaded (limit {config.MAX_INPUT_CHARS}). Edit the box to choose "
            "a different part."
        )
    if INDIC_PATTERN.search(trimmed):
        notices.append(
            "This PDF contains Indian-language text. Text taken from PDFs can show "
            "vowel signs in the wrong place or stray spaces, so please check the box "
            "and correct it before narrating."
        )
    if notices:
        st.session_state["pdf_notice"] = " ".join(notices)
    st.session_state["text"] = trimmed


def clear_input():
    """Empty the text box and the PDF box."""
    st.session_state["text"] = ""
    st.session_state.pop("pdf_error", None)
    st.session_state.pop("pdf_notice", None)
    st.session_state["uploader_round"] = st.session_state.get("uploader_round", 0) + 1


def live_counter(current, limit):
    """Character count that updates while typing (plain Streamlit only updates on blur)."""
    st.html(
        f"""
        <div id="live-count" style="font-size:0.85rem;opacity:0.75">{current} of {limit} characters</div>
        <script>
        (function () {{
            if (window.__liveCountTimer) return;
            window.__liveCountTimer = setInterval(function () {{
                var label = document.getElementById("live-count");
                var box = document.querySelector('[data-testid="stTextArea"] textarea');
                if (!label || !box) return;
                var n = box.value.length;
                label.textContent = n + " of {limit} characters";
                label.style.color = n > {limit} ? "#e5484d" : "";
                label.style.fontWeight = n > {limit} ? "700" : "400";
            }}, 250);
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )

def open_from_history(narration_id):
    """Runs when the user clicks Open on a past narration."""
    row = storage.load_narration(narration_id)
    if row is None:
        return
    st.session_state["result"] = NarrationResult(
        original_text=row["original_text"],
        target_language=row["language"],
        voice=row["voice"],
        style=row["style"],
        paragraphs=split_paragraphs(row["original_text"]),
        translated_paragraphs=row["translated_text"].split("\n\n"),
        chunks=[],
        wav_bytes=row["audio"],
    )
    st.session_state.pop("partial", None)


def delete_from_history(narration_id):
    storage.delete_narration(narration_id)


def show_translation(original_paragraphs, translated_paragraphs):
    left, right = st.columns(2)
    left.subheader("Original")
    right.subheader("Translation")
    for original, translated in zip(original_paragraphs, translated_paragraphs, strict=False):
        with left.container(border=True):
            st.write(original)
        with right.container(border=True):
            st.write(translated)

st.title("Book/Article Narrator")
st.caption("Paste text, pick a language, and listen to it narrated.")
language_names = [label.split(" (")[0] for label in config.LANGUAGES.values()]
chips = "".join(f"<span>{name}</span>" for name in language_names[:6])
chips += f"<span>+{len(language_names) - 6} more</span>"
st.markdown(f'<div class="chips">{chips}</div>', unsafe_allow_html=True)

if DEMO_MODE:
    st.info("Demo mode: no real Bodhan calls are made, so the audio is a beep and the translation is a tag.")

st.file_uploader(
    "Or upload a text-based PDF (first 10 pages are read)",
    type=["pdf"],
    key=uploader_key(),
    on_change=load_pdf,
)
if "pdf_error" in st.session_state:
    st.error(st.session_state["pdf_error"])
if "pdf_notice" in st.session_state:
    st.warning(st.session_state["pdf_notice"])

text = st.text_area(
    "Text to narrate",
    height=200,
    placeholder="Paste a notice or an article here.",
    key="text",
)
with st.container(key="input_bar"):
    count_col, clear_col = st.columns([5, 1], vertical_alignment="center")
    with count_col:
        live_counter(len(text), config.MAX_INPUT_CHARS)
    clear_col.button(
        "Clear",
        icon=":material/close:",
        type="tertiary",
        on_click=clear_input,
        key="clear_button",
    )

col1, col2, col3 = st.columns(3)
language = col1.selectbox(
    "Language", list(config.LANGUAGES), format_func=lambda code: config.LANGUAGES[code]
)
voice = col2.selectbox("Voice", config.VOICES[language])
style_label = col3.selectbox("Style", list(config.STYLES))
save_history = st.checkbox(
    "Save this narration to history on this computer",
    value=False,
    help="Stored in a local file (narrator.db). Anyone using this copy of the app can see it.",
)

if st.button("Translate and narrate", type="primary"):
    st.session_state.pop("result", None)
    st.session_state.pop("partial", None)
    progress_bar = st.progress(0.0)
    status = st.empty()
    live_translation = st.container()

    def show_progress(stage, done, total):
        if stage == "translate":
            progress_bar.progress(0.2 * done / total)
            status.write(f"Translating: {done} of {total} paragraphs done")
        else:
            progress_bar.progress(0.2 + 0.8 * done / total)
            status.write(f"Reading aloud: {done} of {total} pieces done")

    def keep_translation(translated):
        st.session_state["partial"] = (split_paragraphs(text), translated)
        with live_translation:
            show_translation(split_paragraphs(text), translated)

    try:
        result = narrate(
            text,
            language,
            voice,
            config.STYLES[style_label],
            get_client(),
            progress=show_progress,
            on_translated=keep_translation,
        )
    except BodhanAPIError as error:
        st.error(str(error))
        with st.expander("Details for support"):
            st.write(f"Request id: {error.request_id or 'not provided by Bodhan for this call'}")
            st.write(f"Time (UTC): {datetime.now(UTC):%Y-%m-%d %H:%M:%S}")
            st.write(f"Status: {error.status}, code: {error.code}")
            if error.detail:
                st.write(f"Bodhan said: {error.detail}")
    except NarratorError as error:
        st.error(str(error))
    else:
        if save_history:
            try:
                storage.save_narration(result)
            except sqlite3.Error:
                st.session_state["history_warning"] = True
        st.session_state["result"] = result
        st.session_state.pop("partial", None)
        st.rerun()

if "result" in st.session_state:
    result = st.session_state["result"]
    show_translation(result.paragraphs, result.translated_paragraphs)
    st.audio(result.wav_bytes, format="audio/wav")
    st.download_button(
        "Download audio", result.wav_bytes, file_name="narration.wav", mime="audio/wav"
    )
elif "partial" in st.session_state:
    show_translation(*st.session_state["partial"])

if st.session_state.pop("history_warning", False):
    st.warning("The narration worked, but it could not be saved to history.")

st.divider()
try:
    history = storage.list_narrations()
except sqlite3.Error:
    history = []
    st.caption("History is not available right now.")

with st.expander(f"History ({len(history)})"):
    if not history:
        st.caption("Nothing saved yet. Finished narrations appear here.")
    for item in history:
        left, middle, right = st.columns([6, 1, 1])
        label = config.LANGUAGES.get(item["language"], item["language"])
        when = item["created_at"][:16].replace("T", " ")
        preview = item["original_text"][:60].replace("\n", " ")
        left.write(f"**{label} · {item['voice']}** · {when} UTC  \n{preview}")
        middle.button(
            "Open",
            key=f"open_{item['id']}",
            on_click=open_from_history,
            args=(item["id"],),
        )
        right.button(
            "Delete",
            key=f"del_{item['id']}",
            on_click=delete_from_history,
            args=(item["id"],),
        )
    if history:
        st.button("Clear all history", on_click=storage.clear_history)