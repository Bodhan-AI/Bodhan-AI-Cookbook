"""
Camera Reader — Document OCR Layout Extraction, Translation Overlay & Speech
Minimalism & Swiss Style Architecture (Light Mode & Dynamic Localization)
"""

import streamlit as st
import io
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from constants import (
    get_language_name, get_native_name, get_service_key,
)
from bodhan_api import (
    text_to_speech, text_to_speech_continuous, scan_and_translate, BodhanAPIError,
)
from task_manager import submit_background_task, get_task, render_background_tasks_banner
from ui_theme import apply_custom_css, render_page_header
from i18n import t

st.set_page_config(page_title="Camera Reader — Bodhan Workspace", layout="wide")
apply_custom_css()

# Render background task notification banner at top
render_background_tasks_banner()

# ── Auth Guard ───────────────────────────────────────────────

if not st.session_state.get("onboarding_complete"):
    st.warning("Setup required. Initialize target language to proceed.")
    st.page_link("streamlit_app.py", label="RETURN TO SETUP →")
    st.stop()

OCR_KEY = get_service_key(st.session_state, "ocr")
TRANSLATE_KEY = get_service_key(st.session_state, "translate")
TTS_KEY = get_service_key(st.session_state, "tts")
TARGET_LANG = st.session_state.preferred_language

if not OCR_KEY:
    st.error("Document OCR Key (indic-ocr) missing. Configure in Settings.")
    st.page_link("pages/4_Settings.py", label="OPEN SETTINGS →")
    st.stop()

if not TRANSLATE_KEY:
    st.error("Translation Key missing. Configure in Settings.")
    st.page_link("pages/4_Settings.py", label="OPEN SETTINGS →")
    st.stop()

# ── Top Bar ──────────────────────────────────────────────────

render_page_header(
    title=t("mod_cam_title", "01 / CAMERA READER"),
    subtitle=t("cam_header_sub", "Extract document layout blocks, translate text, and synthesize speech."),
    section_code="MODULE 01",
)

script_mode = st.session_state.get("script_mode", "native")
voice_name = st.session_state.get("voice_name", "Kavya")
tts_style = st.session_state.get("tts_style")

# ── Image Input Panel ────────────────────────────────────────

image_bytes = None
image_format = "png"

with st.container():
    st.markdown(f"""
    <div class="ui-card">
        <div class="ui-card-header">
            <span>{t('input_doc_source', 'INPUT DOCUMENT SOURCE')}</span>
            <span class="badge badge-primary">PNG / JPG</span>
        </div>
    """, unsafe_allow_html=True)

    tab_upload, tab_camera = st.tabs([t('tab_file_upload', 'FILE UPLOAD'), t('tab_cam_capture', 'CAMERA CAPTURE')])

    with tab_upload:
        uploaded = st.file_uploader(
            "Upload image (PNG, JPG, JPEG)",
            type=["png", "jpg", "jpeg"],
            key="camera_reader_uploader",
            label_visibility="collapsed",
        )
        if uploaded:
            image_bytes = uploaded.getvalue()
            image_format = "jpeg" if uploaded.name.lower().endswith((".jpg", ".jpeg")) else "png"

    with tab_camera:
        camera_photo = st.camera_input("Capture page image", key="camera_reader_cam")
        if camera_photo:
            image_bytes = camera_photo.getvalue()
            image_format = "png"

    st.markdown("</div>", unsafe_allow_html=True)

# Session state image cache
if image_bytes:
    st.session_state["camera_last_image"] = image_bytes
    st.session_state["camera_last_format"] = image_format
elif "camera_last_image" in st.session_state:
    image_bytes = st.session_state["camera_last_image"]
    image_format = st.session_state.get("camera_last_format", "png")

# ── Execution Controls ───────────────────────────────────────

if image_bytes:
    col_run1, col_run2 = st.columns([3, 1], gap="medium")

    with col_run1:
        start_btn = st.button(t('run_ocr_btn', "RUN OCR & TRANSLATION PIPELINE →"), type="primary", use_container_width=True)

    with col_run2:
        if st.button(t('refresh_btn', "REFRESH STATUS"), use_container_width=True):
            st.rerun()

    def _do_scan_translate(img_b, img_fmt, o_key, t_key, t_lang, s_mode, tbl_fmt):
        return scan_and_translate(
            ocr_key=o_key,
            translate_key=t_key,
            image_bytes=img_b,
            target_language=t_lang,
            image_format=img_fmt,
            script=s_mode if s_mode != "native" else None,
            table_format=tbl_fmt,
        )

    if start_btn:
        task_id = submit_background_task(
            name=f"Camera Reader ({get_language_name(TARGET_LANG)})",
            page_route="pages/1_Camera_Reader.py",
            target_fn=_do_scan_translate,
            img_b=image_bytes,
            img_fmt=image_format,
            o_key=OCR_KEY,
            t_key=TRANSLATE_KEY,
            t_lang=TARGET_LANG,
            s_mode=script_mode,
            tbl_fmt=st.session_state.get("table_format", "markdown"),
        )
        st.session_state["camera_task_id"] = task_id
        st.rerun()

    current_task_id = st.session_state.get("camera_task_id")
    task_info = get_task(current_task_id) if current_task_id else None

    if task_info:
        if task_info["status"] == "running":
            st.markdown(f"""
            <div class="ui-card" style="border-color: #B38B6D; padding: 24px;">
                <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase;">
                    ASYNC PIPELINE IN PROGRESS
                </div>
                <div style="font-size: 1.15rem; font-weight: 700; color: #111113; margin: 6px 0;">
                    {t('cam_header_sub', 'Extracting document layout and translating text...')}
                </div>
                <div style="font-size: 0.85rem; color: #52525B; line-height: 1.5;">
                    Bodhan indic-ocr is mapping bounding coordinates. Background execution active; tab navigation safe.
                </div>
            </div>
            """, unsafe_allow_html=True)
            time.sleep(1.5)
            st.rerun()

        elif task_info["status"] == "failed":
            st.error(f"Pipeline execution failed: {task_info.get('error')}")

        elif task_info["status"] == "completed":
            result = task_info["result"]
            translated_text = result["translated_text"]
            translated_blocks = result["translated_blocks"]

            # ── Audio Player Card ────────────────────────────
            st.markdown(f"""
            <div class="audio-player-card">
                <div class="audio-player-meta">
                    <div class="audio-player-title">
                        {t('doc_speech_title', 'FULL DOCUMENT SPEECH SYNTHESIS')}
                    </div>
                    <span class="badge badge-accent">CONTINUOUS WAV</span>
                </div>
            """, unsafe_allow_html=True)

            col_sp1, col_sp2 = st.columns([2, 1], gap="medium")
            with col_sp1:
                speak_btn = st.button(t('synthesize_page_speech', "SYNTHESIZE FULL PAGE SPEECH →"), type="primary", use_container_width=True)
                if speak_btn:
                    if not TTS_KEY:
                        st.error("Text to Speech Key missing in Settings.")
                    else:
                        if translated_text.strip():
                            with st.spinner("Synthesizing speech frames..."):
                                try:
                                    audio_bytes = text_to_speech_continuous(
                                        api_key=TTS_KEY,
                                        text=translated_text,
                                        voice=voice_name,
                                        language=TARGET_LANG,
                                        style=tts_style,
                                    )
                                    st.session_state["camera_audio_bytes"] = audio_bytes
                                except Exception as e:
                                    st.error(f"TTS synthesis error: {e}")
                        else:
                            st.warning("Empty text string; synthesis aborted.")

                if "camera_audio_bytes" in st.session_state:
                    st.audio(st.session_state["camera_audio_bytes"], format="audio/wav")

            with col_sp2:
                st.markdown(f"""
                <div style="font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: #71717A; line-height: 1.6;">
                    {t('voice', 'VOICE')}: {voice_name.upper()}<br>
                    {t('target_label', 'TARGET')}: {get_language_name(TARGET_LANG).upper()}<br>
                    CHUNK SIZE: SENTENCE DELIMITED
                </div>
                """, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

            # ── Side-by-Side Presentation ────────────────────
            col_orig, col_trans = st.columns(2, gap="medium")
            with col_orig:
                st.markdown(f"#### {t('orig_source', 'ORIGINAL SOURCE')}")
                st.image(image_bytes, use_container_width=True)
            with col_trans:
                st.markdown(f"#### {t('translated', 'TRANSLATED')} ({get_native_name(TARGET_LANG)})")
                for tb in translated_blocks:
                    block = tb["block"]
                    is_heading = getattr(block, "label", "") in ("Chapter-title", "Section-title", "Title")
                    if is_heading:
                        st.markdown(f"**{tb['translation']}**")
                    else:
                        st.markdown(tb["translation"])
                    st.divider()

            # ── Block Breakdown ──────────────────────────────
            with st.expander(t('block_breakdown', "BLOCK BREAKDOWN AND SEGMENT AUDIO"), expanded=False):
                for idx, tb in enumerate(translated_blocks):
                    b = tb["block"]
                    c1, c2, c3 = st.columns([2, 3, 1])
                    with c1:
                        st.caption(f"BLOCK {idx+1:02d}: {getattr(b, 'label', 'TEXT')}")
                        st.text(getattr(b, "text", "")[:100])
                    with c2:
                        st.caption("TRANSLATION:")
                        st.markdown(tb["translation"][:100])
                    with c3:
                        if st.button(t('play', "PLAY"), key=f"speak_blk_{idx}"):
                            if TTS_KEY:
                                try:
                                    blk_audio = text_to_speech(
                                        api_key=TTS_KEY,
                                        text=tb["translation"][:350],
                                        voice=voice_name,
                                        language=TARGET_LANG,
                                        style=tts_style,
                                    )
                                    st.audio(blk_audio, format="audio/wav")
                                except Exception as e:
                                    st.error(f"Audio generation error: {e}")

else:
    st.markdown(f"""
    <div class="ui-card" style="text-align: center; padding: 48px 24px;">
        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; margin-bottom: 8px;">
            {t('awaiting_input', 'AWAITING INPUT')}
        </div>
        <div style="font-size: 1.25rem; font-weight: 700; color: #111113; margin-bottom: 8px;">
            {t('no_doc_loaded', 'No document loaded')}
        </div>
        <div style="font-size: 0.9rem; color: #52525B; max-width: 480px; margin: 0 auto; line-height: 1.6;">
            {t('cam_empty_desc', 'Upload an image file or capture a photo using the controls above. The pipeline will extract block geometry, translate text, and synthesize speech.')}
        </div>
    </div>
    """, unsafe_allow_html=True)
