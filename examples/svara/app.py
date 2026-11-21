"""Streamlit web application for Svara AAC Assistant."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import find_dotenv, load_dotenv

from svara import SvaraClient

# Load environment configuration from nearest .env
load_dotenv(find_dotenv())

st.set_page_config(
    page_title="Svara — Indic AAC Speech Assistant",
    page_icon="🗣️",
    layout="wide",
)

# Custom styling for AAC accessibility
st.markdown(
    """
    <style>
    .aac-card {
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #e0e0e0;
        margin-bottom: 12px;
        background-color: #fafafa;
    }
    .status-badge-cached {
        background-color: #e8f5e9;
        color: #2e7d32;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .status-badge-live {
        background-color: #e3f2fd;
        color: #1565c0;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_client() -> SvaraClient | None:
    api_key = os.environ.get("BODHAN_API_KEY") or os.environ.get("BODHAN_SPEAK_KEY")
    if not api_key:
        return None
    return SvaraClient(api_key=api_key)


st.title("🗣️ Svara (स्वर)")
st.subheader("Multilingual, Voice-Personalised AAC Speech Assistant")
st.markdown(
    "Svara helps non-verbal individuals communicate through **natural Indian-language speech** "
    "powered by Bodhan's `indic-speak` model. It features **code-mixed Hindi-English** phrases, "
    "**personalized voices**, and **zero-latency offline caching** for essential daily phrases."
)

# Sidebar setup
st.sidebar.header("⚙️ Voice & Settings")

lang_code = st.sidebar.selectbox(
    "Language",
    options=["hi", "mr", "bn", "ta", "te", "kn", "gu"],
    format_func=lambda x: {
        "hi": "Hindi (हिन्दी)",
        "mr": "Marathi (मराठी)",
        "bn": "Bengali (বাংলা)",
        "ta": "Tamil (தமிழ்)",
        "te": "Telugu (తెలుగు)",
        "kn": "Kannada (ಕನ್ನಡ)",
        "gu": "Gujarati (ગુજરાતી)",
    }.get(x, x),
    index=0,
)

available_voices = SvaraClient.get_voices(lang_code)
voice = st.sidebar.selectbox("Voice Persona", options=available_voices, index=0)

available_styles = SvaraClient.get_styles()
style = st.sidebar.selectbox("Default Speaking Tone", options=available_styles, index=0)

use_cache = st.sidebar.checkbox("Enable Local Audio Caching", value=True)

client = get_client()

if not client:
    st.error("⚠️ Bodhan API key not detected. Set `BODHAN_API_KEY` in your `.env` file.")
    st.stop()

# Cache pre-warming button in sidebar
if st.sidebar.button("⚡ Pre-cache All Board Phrases"):
    categories = SvaraClient.load_default_phrases(lang=lang_code)
    all_phrases = [p for cat in categories for p in cat.get("phrases", [])]
    prog = st.sidebar.progress(0.0)
    status_text = st.sidebar.empty()

    def update_prog(idx: int, total: int, text: str, cached: bool):
        prog.progress(idx / total)
        state = "Cached" if cached else "Fetched"
        status_text.caption(f"{state}: {text[:20]}…")

    result = client.prewarm_cache(all_phrases, voice=voice, lang=lang_code, on_progress=update_prog)
    st.sidebar.success(f"Ready: {result['cached']} cached, {result['generated']} generated.")


# Session state for playback feedback
if "last_audio" not in st.session_state:
    st.session_state.last_audio = None
if "last_meta" not in st.session_state:
    st.session_state.last_meta = None

# Section 1: Quick Phrase Board
st.markdown("### 📋 Communication Board")
categories = SvaraClient.load_default_phrases(lang=lang_code)
tab_names = [cat["name"] for cat in categories]

if categories:
    tabs = st.tabs(tab_names)
    for tab, cat in zip(tabs, categories):
        with tab:
            phrases = cat.get("phrases", [])
            cols = st.columns(2)
            for i, p in enumerate(phrases):
                col = cols[i % 2]
                with col:
                    btn_label = f"**{p['label']}**\n\n_{p['text']}_"
                    if st.button(btn_label, key=f"phrase_{cat['id']}_{i}", use_container_width=True):
                        phrase_style = p.get("style", style)
                        with st.spinner("Speaking…"):
                            audio, is_cached, latency = client.synthesize(
                                text=p["text"],
                                voice=voice,
                                lang=lang_code,
                                style=phrase_style,
                                use_cache=use_cache,
                            )
                            st.session_state.last_audio = audio
                            st.session_state.last_meta = {
                                "text": p["text"],
                                "voice": voice,
                                "style": phrase_style,
                                "is_cached": is_cached,
                                "latency": latency,
                            }

# Section 2: Custom Sentence Composer
st.markdown("---")
st.markdown("### ✍️ Custom Sentence Composer")
col_input, col_action = st.columns([4, 1])

placeholders = {
    "hi": "e.g. Mujhe thoda rest karna hai, light off kar do.",
    "bn": "e.g. আমি একটু rest নেব, light off করে দিন।",
    "mr": "e.g. मला थोडी rest करायची आहे, light बंद करा.",
    "ta": "e.g. நான் கொஞ்சம் rest எடுக்கணும், light off பண்ணுங்க.",
    "te": "e.g. నేను కొంచెం rest తీసుకోవాలి, light off చేయండి.",
    "kn": "e.g. ನಾನು ಸ್ವಲ್ಪ rest ಮಾಡಬೇಕು, light off ಮಾಡಿ.",
    "gu": "e.g. મારે થોડો rest કરવો છે, light બંધ કરો.",
}

with col_input:
    custom_text = st.text_input(
        "Type any sentence (Native script, English, or Code-mixed Roman script):",
        placeholder=placeholders.get(lang_code, placeholders["hi"]),
    )

with col_action:
    st.write("")
    st.write("")
    speak_custom = st.button("🔊 Speak", use_container_width=True, type="primary")

if speak_custom and custom_text.strip():
    with st.spinner("Generating speech…"):
        audio, is_cached, latency = client.synthesize(
            text=custom_text.strip(),
            voice=voice,
            lang=lang_code,
            style=style,
            use_cache=use_cache,
        )
        st.session_state.last_audio = audio
        st.session_state.last_meta = {
            "text": custom_text.strip(),
            "voice": voice,
            "style": style,
            "is_cached": is_cached,
            "latency": latency,
        }

# Playback area
if st.session_state.last_audio:
    st.markdown("---")
    st.markdown("### 🔊 Live Vocalization")
    meta = st.session_state.last_meta
    st.markdown(f"**Speaking:** *\"{meta['text']}\"*")

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        if meta["is_cached"]:
            st.markdown('<span class="status-badge-cached">⚡ Instant Playback (Local Cache: 0 ms)</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="status-badge-live">🌐 Bodhan API ({meta["latency"]:.2f} s)</span>', unsafe_allow_html=True)
    with col_meta2:
        st.caption(f"Voice: **{meta['voice']}** | Style: **{meta['style']}**")

    st.audio(st.session_state.last_audio, format="audio/wav", autoplay=True)
