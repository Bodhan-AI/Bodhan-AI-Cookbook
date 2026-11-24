"""
Settings & Preferences — System Configuration & API Credential Management
Minimalism & Swiss Style Architecture (Light Mode & Dynamic Localization)
"""

import streamlit as st
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from constants import (
    get_language_name, get_native_name, get_translation_languages,
    language_display_options, voice_display_options, VOICES,
    TTS_STYLES, SCRIPT_MODES, get_default_voice,
)
from task_manager import render_background_tasks_banner
from ui_theme import apply_custom_css, render_page_header
from i18n import t

st.set_page_config(page_title="Settings — Bodhan Workspace", layout="wide")
apply_custom_css()

# Render background task notification banner at top
render_background_tasks_banner()

# ── Auth Guard ───────────────────────────────────────────────

if not st.session_state.get("onboarding_complete"):
    st.warning("Setup required. Initialize target language to proceed.")
    st.page_link("streamlit_app.py", label="RETURN TO SETUP →")
    st.stop()

# ── Top Bar ──────────────────────────────────────────────────

render_page_header(
    title="System Configuration",
    subtitle=t("settings_header_sub", "Configure default target language, neural voice persona, reading style, and API credentials."),
    section_code=t("config", "CONFIG"),
)

st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# CARD 1: LANGUAGE & SCRIPT
# ══════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="ui-card">
    <div class="ui-card-header">
        <span>{t('cfg_lang_title', '01 / OUTPUT LANGUAGE & SCRIPT RENDERING')}</span>
        <span class="badge badge-primary">TRANSLATION ENGINE</span>
    </div>
    <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 16px;">
        {t('cfg_lang_desc', 'Specify primary target language and default script output format.')}
    </p>
""", unsafe_allow_html=True)

col_lang1, col_lang2 = st.columns(2, gap="medium")

with col_lang1:
    lang_options = language_display_options(get_translation_languages())
    current_display = None
    for display, code in lang_options.items():
        if code == st.session_state.preferred_language:
            current_display = display
            break

    current_index = list(lang_options.keys()).index(current_display) if current_display else 0

    new_lang_display = st.selectbox(
        t('pref_lang_label', "Target Language"),
        options=list(lang_options.keys()),
        index=current_index,
        key="settings_target_lang_select",
    )
    new_lang = lang_options[new_lang_display]

    if new_lang != st.session_state.preferred_language:
        st.session_state.preferred_language = new_lang
        st.session_state.voice_name = get_default_voice(new_lang)
        st.rerun()

with col_lang2:
    script_keys = list(SCRIPT_MODES.keys())
    current_script_index = list(SCRIPT_MODES.values()).index(st.session_state.script_mode) \
        if st.session_state.script_mode in SCRIPT_MODES.values() else 0

    new_script_display = st.selectbox(
        t('script_render_label', "Script Rendering Mode"),
        options=script_keys,
        index=current_script_index,
    )
    new_script = SCRIPT_MODES[new_script_display]

    if new_script != st.session_state.script_mode:
        st.session_state.script_mode = new_script
        st.rerun()

script_desc = {
    "native": "Authentic Indic script representation (e.g. ಕನ್ನಡ, తెలుగు, தமிழ், हिंदी).",
    "roman": "Latin phonetic alphabet transcription (e.g. 'Namaste' instead of 'नमस्ते').",
    "hinglish": "Code-mixed vernacular vocabulary with English loan words.",
}.get(st.session_state.script_mode, "")

st.markdown(f"""
<div style="margin-top: 12px; padding: 10px 14px; background: #F4F4F5; border: 1px solid #D4D4D8; font-size: 0.78rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D;">
    ACTIVE MODE: {script_desc}
</div>
""", unsafe_allow_html=True)

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# CARD 2: VOICE PERSONA & READING STYLE
# ══════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="ui-card">
    <div class="ui-card-header">
        <span>{t('cfg_voice_title', '02 / VOICE PERSONA & READING STYLE')}</span>
        <span class="badge badge-accent">TTS ENGINE</span>
    </div>
    <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 16px;">
        {t('cfg_voice_desc', 'Select from 45 neural voice models and 14 stylistic narration profiles.')}
    </p>
""", unsafe_allow_html=True)

col_voice1, col_voice2 = st.columns(2, gap="medium")

with col_voice1:
    voice_opts = voice_display_options()
    current_voice_display = None
    for display, name in voice_opts.items():
        if name == st.session_state.voice_name:
            current_voice_display = display
            break
    current_voice_index = list(voice_opts.keys()).index(current_voice_display) if current_voice_display else 0

    new_voice_display = st.selectbox(
        t('voice_model_label', "Voice Model"),
        options=list(voice_opts.keys()),
        index=current_voice_index,
    )
    new_voice = voice_opts[new_voice_display]

    if new_voice != st.session_state.voice_name:
        st.session_state.voice_name = new_voice
        st.rerun()

    voice_meta = VOICES.get(new_voice, {})
    st.markdown(f"""
    <div style="margin-top: 10px; display: flex; gap: 8px;">
        <span class="badge badge-primary">VOICE: {new_voice.upper()}</span>
        <span class="badge badge-neutral">NATIVE: {voice_meta.get('language', 'Universal').upper()}</span>
        <span class="badge badge-accent">{voice_meta.get('gender', 'Voice').upper()}</span>
    </div>
    """, unsafe_allow_html=True)

with col_voice2:
    style_keys = list(TTS_STYLES.keys())
    current_style = st.session_state.get("tts_style")
    current_style_index = list(TTS_STYLES.values()).index(current_style) if current_style in TTS_STYLES.values() else 0

    new_style_display = st.selectbox(
        t('reading_style_label', "Narration Style"),
        options=style_keys,
        index=current_style_index,
    )
    new_style = TTS_STYLES[new_style_display]

    if new_style != st.session_state.get("tts_style"):
        st.session_state.tts_style = new_style
        st.rerun()

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# CARD 3: BEHAVIOR & OCR PREFERENCES
# ══════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="ui-card">
    <div class="ui-card-header">
        <span>{t('cfg_beh_title', '03 / BEHAVIOR & OCR STRUCTURE')}</span>
        <span class="badge badge-neutral">AUTOMATION</span>
    </div>
    <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 16px;">
        {t('cfg_beh_desc', 'Configure automatic playback streaming and document table parsing structures.')}
    </p>
""", unsafe_allow_html=True)

col_beh1, col_beh2 = st.columns(2, gap="medium")

with col_beh1:
    auto_read = st.toggle(
        t('auto_read_label', "Automated speech generation after scan"),
        value=st.session_state.get("auto_read", True),
    )
    st.session_state.auto_read = auto_read
    st.caption("When enabled, audio streaming initializes immediately upon translation completion.")

with col_beh2:
    table_format = st.selectbox(
        t('ocr_table_label', "OCR Table Format"),
        options=["markdown", "html"],
        index=0 if st.session_state.get("table_format", "markdown") == "markdown" else 1,
    )
    st.session_state.table_format = table_format
    st.caption("HTML maintains merged cell hierarchies; Markdown generates compact tokens.")

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# CARD 4: API CREDENTIALS & STATUS
# ══════════════════════════════════════════════════════════════

def _status_pill(val, name):
    if val:
        return f'<span class="badge badge-primary">[ACTIVE] {name}</span>'
    return f'<span class="badge badge-neutral">[UNCONFIGURED] {name}</span>'

st.markdown(f"""
<div class="ui-card">
    <div class="ui-card-header">
        <span>{t('cfg_cred_title', '04 / API CREDENTIAL STATUS')}</span>
        <span class="badge badge-primary">SOURCE: .ENV</span>
    </div>
    <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 16px;">
        {t('cfg_cred_desc', 'Credentials are authenticated from your local .env file. No manual input required.')}
    </p>
""", unsafe_allow_html=True)

col_st1, col_st2 = st.columns(2, gap="medium")

with col_st1:
    st.markdown(f"""
    <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;">
        <div>{_status_pill(st.session_state.get('api_key_ocr'), 'DOCUMENT OCR (INDIC-OCR)')}</div>
        <div>{_status_pill(st.session_state.get('api_key_translate'), 'TRANSLATION (INDIC-TRANSLATE)')}</div>
        <div>{_status_pill(st.session_state.get('api_key_tts'), 'SPEECH SYNTHESIS (INDIC-SPEAK)')}</div>
    </div>
    """, unsafe_allow_html=True)

with col_st2:
    st.markdown(f"""
    <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px;">
        <div>{_status_pill(st.session_state.get('api_key_stt'), 'SPEECH TO TEXT (INDIC-TRANSCRIBE)')}</div>
        <div>{_status_pill(st.session_state.get('api_key_transliterate') or st.session_state.get('api_key_translate'), 'TRANSLITERATION')}</div>
        <div>{_status_pill(st.session_state.get('api_key'), 'GLOBAL FALLBACK KEY')}</div>
    </div>
    """, unsafe_allow_html=True)

with st.expander(t('manual_overrides', "MANUAL CREDENTIAL OVERRIDES (OPTIONAL)"), expanded=False):
    col_ov1, col_ov2 = st.columns(2, gap="medium")
    with col_ov1:
        new_k_ocr = st.text_input("Document OCR Key (indic-ocr)", value=st.session_state.get("api_key_ocr", ""), type="password")
        new_k_trans = st.text_input("Translation Key (indic-translate)", value=st.session_state.get("api_key_translate", ""), type="password")
        new_k_tts = st.text_input("Text to Speech Key (indic-speak)", value=st.session_state.get("api_key_tts", ""), type="password")

    with col_ov2:
        new_k_stt = st.text_input("Speech to Text Key (indic-transcribe)", value=st.session_state.get("api_key_stt", ""), type="password")
        new_k_translit = st.text_input("Transliteration Key", value=st.session_state.get("api_key_transliterate", ""), type="password")
        new_k_global = st.text_input("Fallback Global Key", value=st.session_state.get("api_key", ""), type="password")

    if st.button(t('save_overrides', "SAVE MANUAL OVERRIDES →"), type="primary", use_container_width=True):
        st.session_state.api_key_ocr = new_k_ocr.strip()
        st.session_state.api_key_translate = new_k_trans.strip()
        st.session_state.api_key_tts = new_k_tts.strip()
        st.session_state.api_key_stt = new_k_stt.strip()
        st.session_state.api_key_transliterate = new_k_translit.strip()
        st.session_state.api_key = new_k_global.strip()
        st.success("Session credentials updated.")
        st.rerun()

st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# CARD 5: DANGER ZONE & MAINTENANCE
# ══════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="ui-card" style="border-color: #E4E4E7;">
    <div class="ui-card-header">
        <span>{t('cfg_maint_title', '05 / SYSTEM MAINTENANCE & RESET')}</span>
        <span class="badge badge-neutral">MAINTENANCE</span>
    </div>
    <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 16px;">
        {t('cfg_maint_desc', 'Manage persistent session memory or restore defaults.')}
    </p>
""", unsafe_allow_html=True)

col_d1, col_d2 = st.columns(2, gap="medium")

with col_d1:
    if st.button(t('purge_history', "PURGE TRANSLATION HISTORY"), use_container_width=True):
        st.session_state.translation_history = []
        st.success("Translation history purged.")

with col_d2:
    if st.button(t('reset_defaults', "RESET SYSTEM DEFAULTS"), type="secondary", use_container_width=True):
        for key in ["onboarding_complete", "api_key", "preferred_language", "script_mode",
                     "voice_name", "tts_style", "auto_read", "table_format",
                     "books", "active_book_id", "translation_history"]:
            if key in st.session_state:
                del st.session_state[key]
        st.warning("System reset to defaults.")
        st.rerun()

st.markdown("</div>", unsafe_allow_html=True)
