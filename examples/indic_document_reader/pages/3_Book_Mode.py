"""
Book Mode — Document Ingestion (PDF / Image OCR) & Continuous Audiobook Synthesis
Minimalism & Swiss Style Architecture (Light Mode & Dynamic Localization)
"""

import streamlit as st
import fitz  # PyMuPDF
from datetime import datetime
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from constants import (
    get_language_name, get_native_name, get_service_key,
)
from bodhan_api import (
    scan_and_translate, text_to_speech_continuous, BodhanAPIError,
)
from pdf_processor import process_pdf_page
from task_manager import submit_background_task, get_task, render_background_tasks_banner
from ui_theme import apply_custom_css, render_page_header
from i18n import t

st.set_page_config(page_title="Book Mode — Bodhan Workspace", layout="wide")
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
voice_name = st.session_state.get("voice_name", "Kavya")
tts_style = st.session_state.get("tts_style") or "single person narration audiobook"
script_mode = st.session_state.get("script_mode", "native")

# ── Session State Books ──────────────────────────────────────

if "books" not in st.session_state:
    st.session_state.books = []
if "active_book_id" not in st.session_state:
    st.session_state.active_book_id = None


def get_active_book():
    for book in st.session_state.books:
        if book["id"] == st.session_state.active_book_id:
            return book
    return None


active_book = get_active_book()

# ── Top Bar ──────────────────────────────────────────────────

render_page_header(
    title=t("mod_book_title", "03 / BOOK MODE") if not active_book else f"Project: {active_book['name']}",
    subtitle=t("book_header_sub", "Ingest PDF documents or scanned page images, translate into target language, and synthesize audiobooks."),
    section_code="MODULE 03",
)

# ══════════════════════════════════════════════════════════════
# VIEW 1: LIBRARY DIRECTORY (When no active book)
# ══════════════════════════════════════════════════════════════

if active_book is None:
    st.markdown(f"""
    <div class="ui-card">
        <div class="ui-card-header">
            <span>{t('new_project_creation', 'NEW PROJECT CREATION')}</span>
            <span class="badge badge-primary">{t('project_initializer', 'PROJECT INITIALIZER')}</span>
        </div>
        <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 14px;">
            {t('new_project_desc', 'Initialize a book or multi-page document project. Supports digital PDFs and photographic scans.')}
        </p>
    """, unsafe_allow_html=True)

    col_new1, col_new2 = st.columns([3, 1], gap="medium")
    with col_new1:
        new_book_name = st.text_input(
            "Project Title",
            placeholder=t('project_title_placeholder', 'Enter title (e.g. Physics Textbook, Malgudi Days, Legal Agreement)...'),
            label_visibility="collapsed",
        )
    with col_new2:
        if st.button(t('create_project_btn', "CREATE PROJECT →"), type="primary", use_container_width=True, disabled=not (new_book_name and new_book_name.strip())):
            book = {
                "id": int(datetime.now().timestamp() * 1000),
                "name": new_book_name.strip(),
                "pages": [],
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            st.session_state.books.append(book)
            st.session_state.active_book_id = book["id"]
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # Existing Projects Directory
    if st.session_state.books:
        st.markdown(f"""
        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; margin-bottom: 12px;">
            {t('project_directory', 'PROJECT DIRECTORY')} ({len(st.session_state.books):02d} TOTAL)
        </div>
        """, unsafe_allow_html=True)

        for book in st.session_state.books:
            page_count = len(book["pages"])
            status_badge = f'<span class="badge badge-primary">{page_count:02d} PAGES</span>' if page_count > 0 else '<span class="badge badge-neutral">00 PAGES</span>'

            st.markdown(f"""
            <div class="ui-card" style="margin-bottom: 12px; padding: 18px 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="font-size: 1.1rem; font-weight: 700; color: #111113; letter-spacing: -0.01em;">
                        {book['name'].upper()}
                    </div>
                    <div>
                        {status_badge}
                    </div>
                </div>
                <div style="font-size: 0.78rem; font-family: 'JetBrains Mono', monospace; color: #71717A;">
                    CREATED: {book['created_at']}  |  TARGET: {get_language_name(TARGET_LANG).upper()}
                </div>
            </div>
            """, unsafe_allow_html=True)

            col_act1, col_act2, col_space = st.columns([2, 1, 4], gap="small")
            with col_act1:
                if st.button(t('open_project', "OPEN PROJECT →"), key=f"open_{book['id']}", type="primary", use_container_width=True):
                    st.session_state.active_book_id = book["id"]
                    st.rerun()
            with col_act2:
                if st.button(t('delete', "DELETE"), key=f"del_{book['id']}", use_container_width=True):
                    st.session_state.books = [b for b in st.session_state.books if b["id"] != book["id"]]
                    st.rerun()

            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    else:
        st.markdown(f"""
        <div class="ui-card" style="text-align: center; padding: 48px 24px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; margin-bottom: 8px;">
                {t('empty_directory', 'EMPTY DIRECTORY')}
            </div>
            <div style="font-size: 1.25rem; font-weight: 700; color: #111113; margin-bottom: 8px;">
                {t('no_active_projects', 'No active projects')}
            </div>
            <div style="font-size: 0.88rem; color: #52525B; max-width: 480px; margin: 0 auto; line-height: 1.6;">
                {t('book_empty_desc', 'Create a project above to ingest PDF documents or page images into an automated translation audiobook.')}
            </div>
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# VIEW 2: ACTIVE PROJECT WORKSPACE
# ══════════════════════════════════════════════════════════════

else:
    col_nav1, col_nav2 = st.columns([2, 5], gap="medium")
    with col_nav1:
        if st.button(t('return_to_dir', "← RETURN TO DIRECTORY"), use_container_width=True):
            st.session_state.active_book_id = None
            st.rerun()
    with col_nav2:
        st.markdown(f"""
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px; height: 100%;">
            <span class="badge badge-primary">{len(active_book['pages']):02d} PAGES</span>
            <span class="badge badge-accent">{t('voice', 'VOICE')}: {voice_name.upper()}</span>
            <span class="badge badge-neutral">{t('target_label', 'TARGET')}: {get_language_name(TARGET_LANG).upper()}</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # ── Content Ingestion Panel ──────────────────────────────
    st.markdown(f"""
    <div class="ui-card">
        <div class="ui-card-header">
            <span>{t('ingest_content', 'INGEST DOCUMENT CONTENT')}</span>
            <span class="badge badge-primary">PDF / IMAGES</span>
        </div>
        <p style="font-size: 0.88rem; color: #52525B; margin-bottom: 12px;">
            Upload PDF documents or page photography. Digital PDF text translates directly; scanned documents route through OCR layout extraction.
        </p>
    """, unsafe_allow_html=True)

    tab_file_upload, tab_camera_capture = st.tabs([t('tab_file_ingest', 'FILE INGESTION (PDF / IMAGE)'), t('tab_camera_scan', 'CAMERA SCAN')])

    with tab_file_upload:
        uploaded_file = st.file_uploader(
            "Upload Document File",
            type=["pdf", "png", "jpg", "jpeg"],
            key=f"unified_upload_{active_book['id']}",
            label_visibility="collapsed",
        )

        if uploaded_file:
            file_name_lower = uploaded_file.name.lower()
            file_bytes = uploaded_file.getvalue()

            # PATH A: PDF
            if file_name_lower.endswith(".pdf"):
                try:
                    doc = fitz.open(stream=file_bytes, filetype="pdf")
                    total_pdf_pages = len(doc)

                    st.markdown(f"""
                    <div style="margin: 14px 0; padding: 12px 16px; background: #F4F4F5; border: 1px solid #D4D4D8; display: flex; align-items: center; justify-content: space-between;">
                        <div>
                            <div style="font-size: 0.95rem; font-weight: 700; color: #111113;">{uploaded_file.name}</div>
                            <div style="font-size: 0.78rem; font-family: 'JetBrains Mono', monospace; color: #52525B;">PDF DOCUMENT | {total_pdf_pages:02d} TOTAL PAGES</div>
                        </div>
                        <span class="badge badge-accent">FORMAT: PDF</span>
                    </div>
                    """, unsafe_allow_html=True)

                    col_r1, col_r2, col_info = st.columns([1, 1, 2], gap="medium")
                    with col_r1:
                        start_page = st.number_input("Start Page", min_value=1, max_value=total_pdf_pages, value=1)
                    with col_r2:
                        default_end = min(total_pdf_pages, start_page + 3)
                        end_page = st.number_input("End Page", min_value=start_page, max_value=total_pdf_pages, value=default_end)

                    pages_to_process = end_page - start_page + 1

                    with col_info:
                        st.markdown("<div style='height: 26px;'></div>", unsafe_allow_html=True)
                        st.caption(f"Batch target: {pages_to_process} pages (Pages {start_page} to {end_page}).")

                    if st.button(f"{t('batch_translate_btn', 'TRANSLATE & INGEST PAGES →')} ({pages_to_process})", type="primary", use_container_width=True):
                        if not TRANSLATE_KEY:
                            st.error("Translation Key missing. Configure in Settings.")
                            st.stop()

                        prog_bar = st.progress(0)
                        stat_text = st.empty()

                        for idx, p_num in enumerate(range(start_page - 1, end_page)):
                            stat_text.markdown(f"Translating page {p_num + 1} of {total_pdf_pages}...")

                            try:
                                page_data = process_pdf_page(
                                    doc=doc,
                                    page_num=p_num,
                                    target_language=TARGET_LANG,
                                    ocr_key=OCR_KEY,
                                    translate_key=TRANSLATE_KEY,
                                    script=script_mode,
                                )

                                book_page = {
                                    "number": len(active_book["pages"]) + 1,
                                    "original_text": page_data["original_text"],
                                    "translated_text": page_data["translated_text"],
                                    "chapter": page_data["chapter"],
                                    "method": page_data["method"],
                                    "image_bytes": page_data["image_bytes"],
                                }

                                active_book["pages"].append(book_page)

                            except Exception as e:
                                st.error(f"Error on page {p_num + 1}: {e}")

                            prog_bar.progress((idx + 1) / pages_to_process)

                        stat_text.markdown("Finished batch processing.")
                        st.success(f"Added {pages_to_process} pages to '{active_book['name']}'.")
                        time.sleep(0.5)
                        st.rerun()

                except Exception as e:
                    st.error(f"PDF read error: {e}")

            # PATH B: IMAGE
            else:
                st.markdown(f"""
                <div style="margin: 14px 0; padding: 12px 16px; background: #F4F4F5; border: 1px solid #D4D4D8; display: flex; align-items: center; justify-content: space-between;">
                    <div>
                        <div style="font-size: 0.95rem; font-weight: 700; color: #111113;">{uploaded_file.name}</div>
                        <div style="font-size: 0.78rem; font-family: 'JetBrains Mono', monospace; color: #52525B;">IMAGE SOURCE | OCR LAYOUT PIPELINE</div>
                    </div>
                    <span class="badge badge-accent">FORMAT: IMAGE</span>
                </div>
                """, unsafe_allow_html=True)

                col_img_prev, col_img_act = st.columns([1, 2], gap="medium")
                with col_img_prev:
                    st.image(file_bytes, width=220)

                with col_img_act:
                    img_fmt = "jpeg" if file_name_lower.endswith((".jpg", ".jpeg")) else "png"
                    if st.button(t('ingest_img_btn', "PROCESS & INGEST IMAGE PAGE →"), type="primary", use_container_width=True):
                        if not OCR_KEY or not TRANSLATE_KEY:
                            st.error("OCR and Translation keys required.")
                            st.stop()

                        with st.spinner("Extracting layout blocks..."):
                            try:
                                res = scan_and_translate(
                                    ocr_key=OCR_KEY,
                                    translate_key=TRANSLATE_KEY,
                                    image_bytes=file_bytes,
                                    target_language=TARGET_LANG,
                                    image_format=img_fmt,
                                )

                                chapter = None
                                for tb in res["translated_blocks"]:
                                    if getattr(tb["block"], "label", "") in ("Chapter-title", "Title"):
                                        chapter = tb["translation"]
                                        break

                                page = {
                                    "number": len(active_book["pages"]) + 1,
                                    "original_text": res["ocr_result"].content,
                                    "translated_text": res["translated_text"],
                                    "chapter": chapter,
                                    "method": "image_ocr",
                                    "image_bytes": file_bytes,
                                }

                                active_book["pages"].append(page)
                                st.success(f"Page {page['number']} ingested.")
                                time.sleep(0.5)
                                st.rerun()

                            except Exception as e:
                                st.error(f"Processing error: {e}")

    with tab_camera_capture:
        camera_shot = st.camera_input(
            f"Capture page {len(active_book['pages']) + 1}",
            key=f"unified_cam_{len(active_book['pages'])}",
        )
        if camera_shot:
            shot_bytes = camera_shot.getvalue()
            if st.button(t('ingest_cam_btn', "INGEST CAPTURED IMAGE →"), type="primary", use_container_width=True):
                if not OCR_KEY or not TRANSLATE_KEY:
                    st.error("OCR and Translation keys required.")
                    st.stop()

                with st.spinner("Processing captured page..."):
                    try:
                        res = scan_and_translate(
                            ocr_key=OCR_KEY,
                            translate_key=TRANSLATE_KEY,
                            image_bytes=shot_bytes,
                            target_language=TARGET_LANG,
                            image_format="png",
                        )

                        chapter = None
                        for tb in res["translated_blocks"]:
                            if getattr(tb["block"], "label", "") in ("Chapter-title", "Title"):
                                chapter = tb["translation"]
                                break

                        page = {
                            "number": len(active_book["pages"]) + 1,
                            "original_text": res["ocr_result"].content,
                            "translated_text": res["translated_text"],
                            "chapter": chapter,
                            "method": "camera_ocr",
                            "image_bytes": shot_bytes,
                        }

                        active_book["pages"].append(page)
                        st.success(f"Page {page['number']} ingested.")
                        time.sleep(0.5)
                        st.rerun()

                    except Exception as e:
                        st.error(f"Processing error: {e}")

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Audiobook Playback & Page Directory ──────────────────
    if active_book["pages"]:
        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

        st.markdown(f"""
        <div class="audio-player-card">
            <div class="audio-player-meta">
                <div class="audio-player-title">
                    {t('audiobook_narration', 'AUDIOBOOK CONTINUOUS NARRATION')}
                </div>
                <div style="display: flex; gap: 8px;">
                    <span class="badge badge-primary">MULTI-CHUNK SPEECH</span>
                    <span class="badge badge-accent">{t('voice', 'VOICE')}: {voice_name.upper()}</span>
                </div>
            </div>
            <p style="font-size: 0.85rem; color: #52525B; margin-bottom: 14px;">
                Synthesizes continuous speech stream across all {len(active_book['pages'])} translated pages using Bodhan indic-speak.
            </p>
        """, unsafe_allow_html=True)

        col_audio_act1, col_audio_act2 = st.columns([3, 1], gap="medium")

        book_audio_key = f"book_audio_{active_book['id']}"

        with col_audio_act1:
            if st.button(t('synthesize_full_book', "SYNTHESIZE FULL AUDIOBOOK STREAM →"), type="primary", use_container_width=True):
                if not TTS_KEY:
                    st.error("Text to Speech Key missing.")
                else:
                    full_text = "\n\n".join(p["translated_text"] for p in active_book["pages"] if p["translated_text"])
                    if full_text.strip():
                        with st.spinner("Synthesizing multi-chunk audiobook audio..."):
                            try:
                                audio = text_to_speech_continuous(
                                    api_key=TTS_KEY,
                                    text=full_text,
                                    voice=voice_name,
                                    language=TARGET_LANG,
                                    style=tts_style,
                                )
                                st.session_state[book_audio_key] = audio
                            except Exception as e:
                                st.error(f"Narration error: {e}")
                    else:
                        st.warning("No text found across pages.")

        with col_audio_act2:
            if st.button(t('dispatch_bg', "DISPATCH TO BACKGROUND"), use_container_width=True):
                if not TTS_KEY:
                    st.error("Text to Speech Key missing.")
                else:
                    full_text = "\n\n".join(p["translated_text"] for p in active_book["pages"] if p["translated_text"])
                    if full_text.strip():
                        task_id = submit_background_task(
                            f"Audiobook: {active_book['name']}",
                            "pages/3_Book_Mode.py",
                            text_to_speech_continuous,
                            api_key=TTS_KEY,
                            text=full_text,
                            voice=voice_name,
                            language=TARGET_LANG,
                            style=tts_style,
                        )
                        st.session_state[f"book_task_{active_book['id']}"] = task_id
                        st.success("Task dispatched to background worker.")
                        st.rerun()

        bg_task_id = st.session_state.get(f"book_task_{active_book['id']}")
        if bg_task_id:
            task_data = get_task(bg_task_id)
            if task_data and task_data["status"] == "completed":
                st.session_state[book_audio_key] = task_data["result"]

        if st.session_state.get(book_audio_key):
            st.audio(st.session_state[book_audio_key], format="audio/wav")

        st.markdown("</div>", unsafe_allow_html=True)

        # Page-by-Page Collection View
        st.markdown(f"""
        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; margin-bottom: 8px;">
            {t('ingested_pages', 'INGESTED PAGES')} ({len(active_book['pages']):02d} TOTAL)
        </div>
        """, unsafe_allow_html=True)

        for page in active_book["pages"]:
            method_badge = "DIGITAL PDF" if page.get("method") == "digital_translation" else "SCANNED OCR"
            title_text = f"PAGE {page['number']:02d}  |  {method_badge}"
            if page.get("chapter"):
                title_text += f"  |  {page['chapter'].upper()}"

            with st.expander(title_text, expanded=False):
                col_p_img, col_p_txt = st.columns([1, 2], gap="medium")

                with col_p_img:
                    if page.get("image_bytes"):
                        st.image(page["image_bytes"], use_container_width=True)

                with col_p_txt:
                    st.markdown(f"#### {t('translated', 'TRANSLATED')}")
                    st.markdown(page["translated_text"])

                    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

                    col_pa1, col_pa2 = st.columns([2, 1], gap="small")
                    with col_pa1:
                        if st.button(f"{t('read_page_speech', 'SYNTHESIZE PAGE SPEECH')} (P.{page['number']:02d})", key=f"speak_page_{page['number']}", use_container_width=True):
                            if not TTS_KEY:
                                st.error("TTS Key missing.")
                            else:
                                page_text = page["translated_text"]
                                if page_text.strip():
                                    with st.spinner("Synthesizing page speech..."):
                                        try:
                                            p_audio = text_to_speech_continuous(
                                                api_key=TTS_KEY,
                                                text=page_text,
                                                voice=voice_name,
                                                language=TARGET_LANG,
                                                style=tts_style,
                                            )
                                            st.audio(p_audio, format="audio/wav")
                                        except Exception as e:
                                            st.error(f"TTS error: {e}")
                    with col_pa2:
                        with st.popover(t('view_source', "VIEW SOURCE TEXT")):
                            st.text(page.get("original_text", ""))
