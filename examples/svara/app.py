"""Streamlit web application for Svara: Two-Way Intelligent AAC Assistant."""

from __future__ import annotations

import io
import os
from pathlib import Path

import streamlit as st
from dotenv import find_dotenv, load_dotenv

from svara import SvaraClient

# Load environment configuration from nearest .env
load_dotenv(find_dotenv())

st.set_page_config(
    page_title="Svara — Two-Way Indic AAC Assistant",
    page_icon="🗣️",
    layout="wide",
)

# Custom styling for AAC accessibility
st.markdown(
    """
    <style>
    .aac-card {
        padding: 14px;
        border-radius: 10px;
        border: 1px solid #e0e0e0;
        margin-bottom: 10px;
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
    .status-badge-llm {
        background-color: #f3e5f5;
        color: #7b1fa2;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .token-chip {
        display: inline-block;
        background-color: #e0f2fe;
        color: #0369a1;
        padding: 6px 12px;
        border-radius: 16px;
        margin: 4px;
        font-weight: 600;
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
st.subheader("Two-Way Intelligent AAC Assistant with Bodhan Speech AI")
st.markdown(
    "Svara bridges communication for non-verbal individuals using Bodhan's **`indic-transcribe`** (ASR) "
    "and **`indic-speak`** (TTS). It features a **Two-Way Conversational Loop**, a **Telegraphic Concept Builder**, "
    "and **zero-latency offline caching**."
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

# System architecture badges in sidebar
st.sidebar.markdown("---")
st.sidebar.caption("**Active AI Pipeline:**")
st.sidebar.caption("• **ASR:** Bodhan `indic-transcribe`")
if client.groq_key:
    st.sidebar.caption("• **Reasoning:** Groq Llama 3 (`qwen/qwen3.8-27b`)")
else:
    st.sidebar.caption("• **Reasoning:** Semantic Intent Matcher (Local)")
st.sidebar.caption("• **TTS:** Bodhan `indic-speak`")

# Session state initialization
if "current_lang" not in st.session_state:
    st.session_state.current_lang = lang_code
elif st.session_state.current_lang != lang_code:
    st.session_state.selected_tokens = []
    st.session_state.expanded_sentence = ""
    st.session_state.current_lang = lang_code

if "last_audio" not in st.session_state:
    st.session_state.last_audio = None
if "last_meta" not in st.session_state:
    st.session_state.last_meta = None
if "selected_tokens" not in st.session_state:
    st.session_state.selected_tokens = []
if "expanded_sentence" not in st.session_state:
    st.session_state.expanded_sentence = ""
if "two_way_transcript" not in st.session_state:
    st.session_state.two_way_transcript = ""
if "two_way_replies" not in st.session_state:
    st.session_state.two_way_replies = []
if "perf_stats" not in st.session_state:
    st.session_state.perf_stats = {"asr_ms": 0, "llm_ms": 0, "tts_ms": 0, "cached": False}

# Top metrics status bar
stat1, stat2, stat3, stat4 = st.columns(4)
stat1.metric("ASR Latency", f"{st.session_state.perf_stats['asr_ms']} ms" if st.session_state.perf_stats['asr_ms'] else "—")
stat2.metric("AI Reasoning", f"{st.session_state.perf_stats['llm_ms']} ms" if st.session_state.perf_stats['llm_ms'] else "—")
stat3.metric("TTS Response", f"{st.session_state.perf_stats['tts_ms']} ms" if st.session_state.perf_stats['tts_ms'] else "—")
stat4.metric("Cache Hit", "⚡ 0 ms" if st.session_state.perf_stats["cached"] else "🌐 Live API")

st.markdown("---")

# Main interface tabs
tab_twoway, tab_telegraphic, tab_board = st.tabs([
    "🎙️ Two-Way Conversation (Hearing & Speaking)",
    "🧩 Telegraphic Concept Builder",
    "📋 Quick Phrase Board",
])

# ==============================================================================
# TAB 1: TWO-WAY CONVERSATION LOOP
# ==============================================================================
with tab_twoway:
    st.markdown("### 🎙️ Two-Way Conversational Assistant")
    st.markdown(
        "A caregiver or doctor speaks into the microphone. Bodhan's **`indic-transcribe`** transcribes the speech, "
        "the AI reasoning engine extracts the context to suggest **1-tap reply chips**, and the user taps to respond."
    )

    col_mic, col_sample = st.columns([3, 2])
    with col_mic:
        recorded_audio = st.audio_input("Record Doctor / Caregiver Question:")
    with col_sample:
        st.write("")
        st.write("")
        use_sample = st.button("🧪 Test with Sample Audio (sample_hindi.wav)", use_container_width=True)

    audio_bytes_to_process = None
    if recorded_audio is not None:
        audio_bytes_to_process = recorded_audio.read()
    elif use_sample:
        sample_path = Path(__file__).resolve().parent.parent.parent / "sample_data" / "sample_hindi.wav"
        if sample_path.exists():
            audio_bytes_to_process = sample_path.read_bytes()
        else:
            st.warning("Sample clip not found at `sample_data/sample_hindi.wav`")

    if audio_bytes_to_process:
        with st.spinner("Transcribing caregiver speech with Bodhan indic-transcribe…"):
            try:
                transcript, dt_asr = client.transcribe(audio_bytes_to_process, lang=lang_code)
                st.session_state.two_way_transcript = transcript
                st.session_state.perf_stats["asr_ms"] = round(dt_asr * 1000)

                with st.spinner("Reasoning over context for 1-tap replies…"):
                    replies, dt_llm = client.suggest_contextual_replies(transcript, lang=lang_code)
                    st.session_state.two_way_replies = replies
                    st.session_state.perf_stats["llm_ms"] = round(dt_llm * 1000)

            except Exception as e:
                st.error(f"Two-Way processing failed: {e}")

    # Display Conversation Turns
    if st.session_state.two_way_transcript:
        st.markdown("#### 👂 What the Doctor/Caregiver Asked:")
        st.info(f"🗣️ **Caregiver:** \"{st.session_state.two_way_transcript}\"")

        st.markdown("#### 💡 Suggested 1-Tap Replies (Tap to Speak):")
        cols = st.columns(len(st.session_state.two_way_replies) if st.session_state.two_way_replies else 1)
        for i, reply in enumerate(st.session_state.two_way_replies):
            with cols[i]:
                if st.button(f"👉 **{reply}**", key=f"twoway_btn_{i}", use_container_width=True):
                    with st.spinner("Vocalizing response…"):
                        audio, is_cached, dt_tts = client.synthesize(
                            text=reply,
                            voice=voice,
                            lang=lang_code,
                            style=style,
                            use_cache=use_cache,
                        )
                        st.session_state.last_audio = audio
                        st.session_state.last_meta = {
                            "text": reply,
                            "voice": voice,
                            "style": style,
                            "is_cached": is_cached,
                            "latency": dt_tts,
                        }
                        st.session_state.perf_stats["tts_ms"] = round(dt_tts * 1000)
                        st.session_state.perf_stats["cached"] = is_cached

# ==============================================================================
# TAB 2: TELEGRAPHIC CONCEPT BUILDER
# ==============================================================================
with tab_telegraphic:
    st.markdown("### 🧩 Telegraphic Concept Builder")
    st.markdown(
        "Non-verbal communicators with motor disabilities often cannot type full sentences. "
        "Tap **2 or 3 concept tokens** below (e.g. `[Paani]` + `[Thanda]` + `[Chahiye]`), and Svara "
        "will dynamically expand them into a complete, fluent sentence."
    )

    concept_dict = SvaraClient.get_concept_tokens(lang=lang_code)
    cols_tok = st.columns(len(concept_dict))

    for col, (category, tokens) in zip(cols_tok, concept_dict.items()):
        with col:
            st.markdown(f"**{category}**")
            for tok in tokens:
                if st.button(f"+ {tok}", key=f"tok_{tok}", use_container_width=True):
                    if tok not in st.session_state.selected_tokens:
                        st.session_state.selected_tokens.append(tok)

    st.markdown("#### 🔲 Selected Concepts:")
    if st.session_state.selected_tokens:
        tok_html = "".join([f'<span class="token-chip">{t}</span>' for t in st.session_state.selected_tokens])
        st.markdown(tok_html, unsafe_allow_html=True)

        col_b1, col_b2 = st.columns([1, 4])
        with col_b1:
            if st.button("🗑️ Clear Tokens", use_container_width=True):
                st.session_state.selected_tokens = []
                st.session_state.expanded_sentence = ""
                st.rerun()
        with col_b2:
            if st.button("✨ Expand to Natural Sentence", use_container_width=True, type="primary"):
                with st.spinner("Assembling natural sentence…"):
                    sent, dt_exp = client.expand_telegraphic_tokens(st.session_state.selected_tokens, lang=lang_code)
                    st.session_state.expanded_sentence = sent
                    st.session_state.perf_stats["llm_ms"] = round(dt_exp * 1000)

        if st.session_state.expanded_sentence:
            st.success(f"**Expanded Sentence:** \"{st.session_state.expanded_sentence}\"")
            if st.button("🔊 Speak Expanded Sentence", use_container_width=True):
                with st.spinner("Synthesizing speech…"):
                    audio, is_cached, dt_tts = client.synthesize(
                        text=st.session_state.expanded_sentence,
                        voice=voice,
                        lang=lang_code,
                        style=style,
                        use_cache=use_cache,
                    )
                    st.session_state.last_audio = audio
                    st.session_state.last_meta = {
                        "text": st.session_state.expanded_sentence,
                        "voice": voice,
                        "style": style,
                        "is_cached": is_cached,
                        "latency": dt_tts,
                    }
                    st.session_state.perf_stats["tts_ms"] = round(dt_tts * 1000)
                    st.session_state.perf_stats["cached"] = is_cached
    else:
        st.caption("No tokens selected yet. Tap concepts above to begin assembling a message.")

# ==============================================================================
# TAB 3: QUICK PHRASE BOARD & CUSTOM COMPOSER
# ==============================================================================
with tab_board:
    st.markdown("### 📋 Quick Communication Board")
    categories = SvaraClient.load_default_phrases(lang=lang_code)
    tab_names = [cat["name"] for cat in categories]

    if categories:
        sub_tabs = st.tabs(tab_names)
        for tab, cat in zip(sub_tabs, categories):
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
                                st.session_state.perf_stats["tts_ms"] = round(latency * 1000)
                                st.session_state.perf_stats["cached"] = is_cached

    # Custom Sentence Composer
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
            key="custom_input_field",
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
            st.session_state.perf_stats["tts_ms"] = round(latency * 1000)
            st.session_state.perf_stats["cached"] = is_cached

# ==============================================================================
# PLAYBACK & FEEDBACK COMPONENT
# ==============================================================================
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
            st.markdown(f'<span class="status-badge-live">🌐 Bodhan Indic-Speak ({meta["latency"]:.2f} s)</span>', unsafe_allow_html=True)
    with col_meta2:
        st.caption(f"Voice: **{meta['voice']}** | Style: **{meta['style']}**")

    st.audio(st.session_state.last_audio, format="audio/wav", autoplay=True)
