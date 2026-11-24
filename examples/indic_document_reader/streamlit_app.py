"""
Bodhan Reader — Main Application Workspace
Minimalism & Swiss Style Architecture (Light Mode & Dynamic Localization)
"""

import streamlit as st
import os
from constants import (
    LANGUAGES, get_language_name, get_native_name,
    get_translation_languages, get_default_voice,
    language_display_options, load_env_file,
)
from task_manager import render_background_tasks_banner
from ui_theme import apply_custom_css, render_page_header, render_sidebar
from i18n import t

# Load .env into os.environ
load_env_file()

# ── Page Configuration ───────────────────────────────────────

st.set_page_config(
    page_title="Bodhan Reader — Swiss Workspace",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_custom_css()

# ── Session State Initialization ─────────────────────────────

def init_session_state():
    defaults = {
        "onboarding_complete": False,
        "api_key": os.getenv("BODHAN_API_KEY", ""),
        "api_key_ocr": os.getenv("BODHAN_API_KEY_OCR", ""),
        "api_key_translate": os.getenv("BODHAN_API_KEY_TRANSLATE", ""),
        "api_key_tts": os.getenv("BODHAN_API_KEY_TTS", ""),
        "api_key_stt": os.getenv("BODHAN_API_KEY_STT", ""),
        "api_key_transliterate": os.getenv("BODHAN_API_KEY_TRANSLITERATE", ""),
        "preferred_language": "",
        "script_mode": "native",
        "voice_name": "Kavya",
        "tts_style": None,
        "auto_read": True,
        "table_format": "markdown",
        "books": [],
        "active_book_id": None,
        "translation_history": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    for k, env_v in [
        ("api_key_ocr", "BODHAN_API_KEY_OCR"),
        ("api_key_translate", "BODHAN_API_KEY_TRANSLATE"),
        ("api_key_tts", "BODHAN_API_KEY_TTS"),
        ("api_key_stt", "BODHAN_API_KEY_STT"),
        ("api_key_transliterate", "BODHAN_API_KEY_TRANSLITERATE"),
        ("api_key", "BODHAN_API_KEY"),
    ]:
        if not st.session_state.get(k) and os.getenv(env_v):
            st.session_state[k] = os.getenv(env_v)


init_session_state()


# ── Onboarding View (Swiss Split-Screen Layout) ──────────────

def render_onboarding():
    render_sidebar()
    st.markdown("<div style='height: 24px;'></div>", unsafe_allow_html=True)

    lang_options = language_display_options(get_translation_languages())

    def _on_onboarding_lang_select():
        chosen_display = st.session_state.get("onboarding_lang_select")
        chosen_code = lang_options.get(chosen_display, "")
        if chosen_code:
            st.session_state.preferred_language = chosen_code
            st.session_state.voice_name = get_default_voice(chosen_code)

    current_pref = st.session_state.get("preferred_language", "")
    current_display = None
    if current_pref:
        for display, code in lang_options.items():
            if code == current_pref:
                current_display = display
                break

    current_index = list(lang_options.keys()).index(current_display) if current_display else None

    col_hero_text, col_hero_form = st.columns([1, 1], gap="large")

    with col_hero_text:
        st.markdown(f"""
        <div style="padding-right: 20px;">
            <div class="badge badge-accent" style="margin-bottom: 12px;">{t('init_badge', 'SYSTEM INITIALIZATION')}</div>
            <h1 style="font-size: 2.75rem; font-weight: 700; letter-spacing: -0.04em; line-height: 1.1; margin-bottom: 16px; color: #111113;">
                {t('init_headline', 'Objective Indic language synthesis.')}
            </h1>
            <p style="font-size: 1.05rem; color: #52525B; line-height: 1.6; max-width: 480px; margin-bottom: 24px;">
                {t('init_sub', 'Direct OCR document parsing, contextual neural translation, and high-fidelity speech generation across Indian languages.')}
            </p>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                <span class="badge badge-neutral">{t('badge_10_langs', '22+ TARGET LANGUAGES')}</span>
                <span class="badge badge-neutral">{t('badge_45_voices', '45 NEURAL VOICES')}</span>
                <span class="badge badge-neutral">{t('badge_wav', 'CONTINUOUS WAV SYNTHESIS')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col_hero_form:
        with st.container(border=True):
            st.markdown(f"""
            <div class="ui-card-header" style="margin-top: -4px;">
                <span>{t('lang_selection', 'LANGUAGE SELECTION')}</span>
                <span class="badge badge-primary">{t('step_01', 'STEP 01')}</span>
            </div>
            <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 18px; line-height: 1.5;">
                {t('lang_desc', 'Select your primary target language. All documents, camera inputs, and text will translate into this language.')}
            </p>
            """, unsafe_allow_html=True)

            selected_display = st.selectbox(
                "Target Language",
                options=list(lang_options.keys()),
                index=current_index,
                placeholder=t('select_lang_placeholder', 'Select language...'),
                label_visibility="collapsed",
                key="onboarding_lang_select",
                on_change=_on_onboarding_lang_select,
            )

            # Determine selected language code
            selected_lang = st.session_state.get("preferred_language", "")
            if not selected_lang and selected_display:
                selected_lang = lang_options.get(selected_display, "")
                if selected_lang:
                    st.session_state.preferred_language = selected_lang
                    st.session_state.voice_name = get_default_voice(selected_lang)

            if selected_lang:
                st.markdown(f"""
                <div style="margin: 14px 0; padding: 12px 14px; background: #F4F4F5; border: 1px solid #D4D4D8; display: flex; align-items: center; justify-content: space-between;">
                    <div>
                        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase;">{t('selected_target', 'SELECTED TARGET')}</div>
                        <div style="font-size: 1.1rem; font-weight: 700; color: #111113;">{get_native_name(selected_lang)} ({get_language_name(selected_lang)})</div>
                    </div>
                    <span class="badge badge-primary">{t('ready', 'READY')}</span>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

            if st.button(
                t('init_btn', "INITIALIZE WORKSPACE →"),
                type="primary",
                use_container_width=True,
                disabled=not selected_lang,
                key="btn_init_workspace",
            ):
                st.session_state.preferred_language = selected_lang
                st.session_state.voice_name = get_default_voice(selected_lang)
                st.session_state.onboarding_complete = True
                st.rerun()


# ── Home Dashboard (Swiss Asymmetric Grid & Zig-Zag Layout) ──

def render_home():
    render_background_tasks_banner()
    render_page_header(
        title=t('home_title', "Bodhan Reader Workspace"),
        subtitle=t('home_sub', "Multimodal translation and speech synthesis system for Indian languages."),
        section_code=t('workspace', "WORKSPACE"),
    )

    lang = st.session_state.preferred_language

    # Metric Row (Swiss High Contrast)
    col_m1, col_m2, col_m3 = st.columns([1, 1, 2], gap="medium")
    with col_m1:
        st.markdown(f"""
        <div class="ui-card" style="padding: 16px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase;">
                {t('trans_logged', 'TRANSLATIONS LOGGED')}
            </div>
            <div style="font-size: 2.2rem; font-weight: 700; color: #111113; margin: 4px 0;">
                {len(st.session_state.get('translation_history', [])):02d}
            </div>
            <div style="font-size: 0.78rem; color: #52525B;">{t('session_records', 'Session records')}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_m2:
        st.markdown(f"""
        <div class="ui-card" style="padding: 16px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase;">
                {t('books_in_lib', 'BOOKS IN LIBRARY')}
            </div>
            <div style="font-size: 2.2rem; font-weight: 700; color: #111113; margin: 4px 0;">
                {len(st.session_state.get('books', [])):02d}
            </div>
            <div style="font-size: 0.78rem; color: #52525B;">{t('audiobook_proj', 'Audiobook projects')}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_m3:
        st.markdown(f"""
        <div class="ui-card" style="padding: 16px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase;">
                {t('active_cfg', 'ACTIVE CONFIGURATION')}
            </div>
            <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
                <span class="badge badge-primary">{get_language_name(lang).upper()} ({get_native_name(lang)})</span>
                <span class="badge badge-accent">{t('voice', 'VOICE')}: {st.session_state.voice_name.upper()}</span>
                <span class="badge badge-neutral">{t('script', 'SCRIPT')}: {st.session_state.script_mode.upper()}</span>
            </div>
            <div style="font-size: 0.78rem; color: #71717A; margin-top: 8px;">
                {t('modify_in_settings', 'Modify parameters in Settings')}
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

    # ── Feature Sections: Asymmetric Zig-Zag Layout (No 3 equal columns) ──
    st.markdown(f"""
    <div style="font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px;">
        {t('module_dir', 'MODULE DIRECTORY')}
    </div>
    """, unsafe_allow_html=True)

    # Row 1: Camera Reader (Asymmetric 7:5)
    col_f1_info, col_f1_action = st.columns([7, 5], gap="large")
    with col_f1_info:
        st.markdown(f"""
        <div class="ui-card" style="height: 100%;">
            <div class="ui-card-header">
                <span>{t('mod_cam_title', '01 / CAMERA READER')}</span>
                <span class="badge badge-primary">{t('mod_cam_badge', 'OCR + TRANSLATE + TTS')}</span>
            </div>
            <p style="font-size: 0.92rem; color: #52525B; line-height: 1.6; margin-bottom: 14px;">
                {t('mod_cam_desc', 'Extract printed text from images, render side-by-side translated document blocks, and stream multi-sentence speech.')}
            </p>
            <div style="display: flex; gap: 6px;">
                <span class="badge badge-neutral">{t('mod_cam_tag1', 'LAYOUT PARSER')}</span>
                <span class="badge badge-neutral">{t('mod_cam_tag2', 'SIDE-BY-SIDE TRANSLATION')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col_f1_action:
        st.markdown("<div style='height: 48px;'></div>", unsafe_allow_html=True)
        st.page_link("pages/1_Camera_Reader.py", label=t('mod_cam_btn', "OPEN CAMERA READER →"), use_container_width=True)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # Row 2: Quick Translate (Asymmetric 5:7 - Inverted Zig-Zag)
    col_f2_action, col_f2_info = st.columns([5, 7], gap="large")
    with col_f2_action:
        st.markdown("<div style='height: 48px;'></div>", unsafe_allow_html=True)
        st.page_link("pages/2_Quick_Translate.py", label=t('mod_qt_btn', "OPEN QUICK TRANSLATE →"), use_container_width=True)
    with col_f2_info:
        st.markdown(f"""
        <div class="ui-card" style="height: 100%;">
            <div class="ui-card-header">
                <span>{t('mod_qt_title', '02 / QUICK TRANSLATE')}</span>
                <span class="badge badge-accent">{t('mod_qt_badge', 'TEXT TRANSLATION')}</span>
            </div>
            <p style="font-size: 0.92rem; color: #52525B; line-height: 1.6; margin-bottom: 14px;">
                {t('mod_qt_desc', 'Direct textual translation across 10 Indian languages. Generates authentic native script, Romanized transliteration, and audio narration.')}
            </p>
            <div style="display: flex; gap: 6px;">
                <span class="badge badge-neutral">{t('mod_qt_tag1', 'ROMAN SCRIPT')}</span>
                <span class="badge badge-neutral">{t('mod_qt_tag2', 'AUDIO PLAYBACK')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # Row 3: Book Mode (Asymmetric 7:5)
    col_f3_info, col_f3_action = st.columns([7, 5], gap="large")
    with col_f3_info:
        st.markdown(f"""
        <div class="ui-card" style="height: 100%;">
            <div class="ui-card-header">
                <span>{t('mod_book_title', '03 / BOOK MODE')}</span>
                <span class="badge badge-primary">{t('mod_book_badge', 'AUDIOBOOK SYNTHESIZER')}</span>
            </div>
            <p style="font-size: 0.92rem; color: #52525B; line-height: 1.6; margin-bottom: 14px;">
                {t('mod_book_desc', 'Process multi-page PDF documents or page photography. Automatically routes digital text or scanned OCR, stitches chapters, and renders continuous audiobooks.')}
            </p>
            <div style="display: flex; gap: 6px;">
                <span class="badge badge-neutral">{t('mod_book_tag1', 'PDF INGESTION')}</span>
                <span class="badge badge-neutral">{t('mod_book_tag2', 'CONTINUOUS SPEECH')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col_f3_action:
        st.markdown("<div style='height: 48px;'></div>", unsafe_allow_html=True)
        st.page_link("pages/3_Book_Mode.py", label=t('mod_book_btn', "OPEN BOOK MODE →"), use_container_width=True)


# ── Main Entrypoint ──────────────────────────────────────────

if st.session_state.onboarding_complete:
    render_home()
else:
    render_onboarding()
