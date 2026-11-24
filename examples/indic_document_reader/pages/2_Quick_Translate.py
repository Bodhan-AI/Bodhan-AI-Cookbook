"""
Quick Translate — Multilingual Translation, Phonetics & Continuous Speech
Minimalism & Swiss Style Architecture (Light Mode & Dynamic Localization)
"""

import streamlit as st
from datetime import datetime
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from constants import (
    get_language_name, get_native_name, LANGUAGES,
    get_service_key,
)
from bodhan_api import (
    translate, transliterate, text_to_speech, text_to_speech_continuous,
    BodhanAPIError,
)
from task_manager import submit_background_task, get_task, render_background_tasks_banner
from ui_theme import apply_custom_css, render_page_header
from i18n import t

# ── Roman Script Verification & Phonetic Engine ──────────────

def is_roman_script(text: str) -> bool:
    """Checks if text is predominantly Latin/Roman alphabet and not native Indic script."""
    if not text or not text.strip():
        return False
    latin_count = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    indic_count = sum(1 for c in text if 0x0900 <= ord(c) <= 0x0D7F)
    if indic_count > 0 and indic_count >= latin_count:
        return False
    return latin_count > 0


_BRAHMIC_OFFSET_MAP = {
    0x02: 'm', 0x03: 'h', 0x05: 'a', 0x06: 'aa', 0x07: 'i', 0x08: 'ee', 0x09: 'u', 0x0A: 'oo',
    0x0B: 'ri', 0x0E: 'e', 0x0F: 'ee', 0x10: 'ai', 0x12: 'o', 0x13: 'oo', 0x14: 'au',
    0x15: 'k', 0x16: 'kh', 0x17: 'g', 0x18: 'gh', 0x19: 'ng',
    0x1A: 'ch', 0x1B: 'chh', 0x1C: 'j', 0x1D: 'jh', 0x1E: 'ny',
    0x1F: 't', 0x20: 'th', 0x21: 'd', 0x22: 'dh', 0x23: 'n',
    0x24: 't', 0x25: 'th', 0x26: 'd', 0x27: 'dh', 0x28: 'n',
    0x2A: 'p', 0x2B: 'ph', 0x2C: 'b', 0x2D: 'bh', 0x2E: 'm',
    0x2F: 'y', 0x30: 'r', 0x31: 'rr', 0x32: 'l', 0x33: 'll',
    0x35: 'v', 0x36: 'sh', 0x37: 'sh', 0x38: 's', 0x39: 'h',
    0x3E: 'aa', 0x3F: 'i', 0x40: 'ee', 0x41: 'u', 0x42: 'oo', 0x43: 'ri',
    0x46: 'e', 0x47: 'ee', 0x48: 'ai', 0x4A: 'o', 0x4B: 'oo', 0x4C: 'au',
    0x4D: ''  # Halant / Virama
}

_BRAHMIC_SCRIPT_BASES = [
    (0x0900, 0x097F),  # Devanagari (hi, mr, sa, ne, kok, mai, doi, sd)
    (0x0980, 0x09FF),  # Bengali / Assamese (bn, as, mni)
    (0x0A00, 0x0A7F),  # Gurmukhi (pa)
    (0x0A80, 0x0AFF),  # Gujarati (gu)
    (0x0B00, 0x0B7F),  # Odia (or)
    (0x0B80, 0x0BFF),  # Tamil (ta)
    (0x0C00, 0x0C7F),  # Telugu (te)
    (0x0C80, 0x0CFF),  # Kannada (kn)
    (0x0D00, 0x0D7F),  # Malayalam (ml)
]


def transliterate_indic_to_roman(text: str) -> str:
    """Converts Indic script text into authentic phonetic Romanized Latin letters."""
    if not text:
        return ""
    result = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        code = ord(c)
        base = None
        for b_start, b_end in _BRAHMIC_SCRIPT_BASES:
            if b_start <= code <= b_end:
                base = b_start
                break
        if base is None:
            result.append(c)
            i += 1
            continue

        offset = code - base
        is_consonant = 0x15 <= offset <= 0x39
        roman_con = _BRAHMIC_OFFSET_MAP.get(offset, '')

        if is_consonant:
            if i + 1 < n:
                next_c = text[i + 1]
                next_code = ord(next_c)
                next_offset = next_code - base
                if next_offset == 0x4D:  # Virama
                    result.append(roman_con)
                    i += 2
                    continue
                elif 0x3E <= next_offset <= 0x4C:  # Matra vowel
                    result.append(roman_con + _BRAHMIC_OFFSET_MAP.get(next_offset, ''))
                    i += 2
                    continue
            result.append(roman_con + 'a')
            i += 1
        else:
            result.append(_BRAHMIC_OFFSET_MAP.get(offset, ''))
            i += 1

    return ''.join(result)

st.set_page_config(page_title="Quick Translate — Bodhan Workspace", layout="wide")
apply_custom_css()

# Render background task notification banner at top
render_background_tasks_banner()

# ── Auth Guard ───────────────────────────────────────────────

if not st.session_state.get("onboarding_complete"):
    st.warning("Setup required. Initialize target language to proceed.")
    st.page_link("streamlit_app.py", label="RETURN TO SETUP →")
    st.stop()

TRANSLATE_KEY = get_service_key(st.session_state, "translate")
TRANSLITERATE_KEY = get_service_key(st.session_state, "transliterate")
TTS_KEY = get_service_key(st.session_state, "tts")
TARGET_LANG = st.session_state.preferred_language

# ── Top Bar ──────────────────────────────────────────────────

render_page_header(
    title=t("mod_qt_title", "02 / QUICK TRANSLATE"),
    subtitle=t("qt_header_sub", "Direct text translation across Indian languages with phonetic transliteration and speech synthesis."),
    section_code="MODULE 02",
)

script_mode = st.session_state.get("script_mode", "native")
voice_name = st.session_state.get("voice_name", "Kavya")
tts_style = st.session_state.get("tts_style")

# ── Input Area Card ──────────────────────────────────────────

with st.container():
    st.markdown(f"""
    <div class="ui-card">
        <div class="ui-card-header">
            <span>{t('input_text_source', 'INPUT TEXT SOURCE')}</span>
            <span class="badge badge-accent">{t('auto_detection', 'AUTOMATIC DETECTION')}</span>
        </div>
    """, unsafe_allow_html=True)

    input_text = st.text_area(
        "Source Text",
        height=140,
        placeholder=t('enter_text_placeholder', 'Enter or paste text in any language...'),
        max_chars=8000,
        key="qt_input_area",
        label_visibility="collapsed",
    )

    col_btn1, col_btn2 = st.columns([3, 1], gap="medium")

    with col_btn1:
        translate_clicked = st.button(
            t('qt_translate_btn', "TRANSLATE & SYNTHESIZE SPEECH →"),
            type="primary",
            use_container_width=True,
            disabled=not input_text.strip(),
        )

    with col_btn2:
        if st.button(t('refresh_btn', "REFRESH STATUS"), use_container_width=True):
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# ── Worker function for background execution ─────────────────

def _do_quick_translate_and_tts(text_val, t_key, tl_key, tts_k, t_lang, s_mode, v_name, style_val):
    res = translate(
        api_key=t_key,
        text=text_val,
        target_language=t_lang,
        script=s_mode if s_mode != "native" else None,
    )

    roman_text = None
    if s_mode == "native" and t_lang != "en":
        # 1. Attempt official Bodhan transliterate API
        trans_key = tl_key or t_key
        if trans_key:
            try:
                roman_res = transliterate(
                    api_key=trans_key,
                    text=res.translation,
                    language=t_lang,
                    script="roman",
                )
                if roman_res.transliteration and is_roman_script(roman_res.transliteration):
                    roman_text = roman_res.transliteration
            except Exception:
                pass

        # 2. Attempt Roman translation via indic-translate (script="roman")
        if not roman_text or not is_roman_script(roman_text):
            try:
                roman_trans = translate(
                    api_key=t_key,
                    text=text_val,
                    target_language=t_lang,
                    script="roman",
                )
                if roman_trans.translation and is_roman_script(roman_trans.translation):
                    roman_text = roman_trans.translation
            except Exception:
                pass

        # 3. Guaranteed phonetic transliteration fallback
        if not roman_text or not is_roman_script(roman_text):
            roman_text = transliterate_indic_to_roman(res.translation)

    audio_data = None
    if tts_k and res.translation.strip():
        try:
            audio_data = text_to_speech_continuous(
                api_key=tts_k,
                text=res.translation,
                voice=v_name,
                language=t_lang,
                style=style_val,
            )
        except Exception:
            pass

    return {
        "input_text": text_val,
        "translation": res.translation,
        "roman_text": roman_text,
        "audio_bytes": audio_data,
        "truncated": res.truncated,
        "usage": res.usage,
    }


if translate_clicked and input_text.strip():
    if not TRANSLATE_KEY:
        st.error("Translation Key missing. Configure in Settings.")
        st.stop()

    task_id = submit_background_task(
        name=f"Quick Translate ({get_language_name(TARGET_LANG)})",
        page_route="pages/2_Quick_Translate.py",
        target_fn=_do_quick_translate_and_tts,
        text_val=input_text.strip(),
        t_key=TRANSLATE_KEY,
        tl_key=TRANSLITERATE_KEY,
        tts_k=TTS_KEY,
        t_lang=TARGET_LANG,
        s_mode=script_mode,
        v_name=voice_name,
        style_val=tts_style,
    )
    st.session_state["translate_task_id"] = task_id
    st.rerun()

# ── Result & Audio Section ───────────────────────────────────

current_task_id = st.session_state.get("translate_task_id")
task_info = get_task(current_task_id) if current_task_id else None

if task_info:
    if task_info["status"] == "running":
        st.markdown("""
        <div class="ui-card" style="border-color: #B38B6D; padding: 24px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase;">
                ASYNC EXECUTION
            </div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #111113; margin: 6px 0;">
                Processing translation and speech synthesis...
            </div>
            <div style="font-size: 0.85rem; color: #52525B;">
                Background worker running. Navigation across tabs is enabled.
            </div>
        </div>
        """, unsafe_allow_html=True)
        time.sleep(1.5)
        st.rerun()

    elif task_info["status"] == "failed":
        st.error(f"Translation failed: {task_info.get('error')}")

    elif task_info["status"] == "completed":
        data = task_info["result"]

        st.markdown(f"""
        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; margin-bottom: 8px;">
            {t('synthesis_output', 'SYNTHESIS OUTPUT')}
        </div>
        """, unsafe_allow_html=True)

        col_orig, col_trans = st.columns(2, gap="medium")

        with col_orig:
            st.markdown(f"""<div class="ui-card">
<div class="ui-card-header">
<span>{t('source_input', 'SOURCE INPUT')}</span>
<span class="badge badge-neutral">INPUT</span>
</div>
<div style="font-size: 1rem; line-height: 1.6; color: #111113;">
{data['input_text']}
</div>
</div>""", unsafe_allow_html=True)

        with col_trans:
            # Guarantee valid Roman text even on previous cached task records
            display_roman = data.get("roman_text")
            if not display_roman or not is_roman_script(display_roman):
                display_roman = transliterate_indic_to_roman(data.get("translation", ""))

            roman_html = ""
            if display_roman and is_roman_script(display_roman):
                roman_html = f"""<div style="margin-top: 14px; padding: 12px 14px; background: #F4F4F5; border: 1px solid #D4D4D8;">
<div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em;">{t('roman_script', 'ROMANIZED SCRIPT')}</div>
<div style="font-size: 0.95rem; color: #111113; font-family: 'JetBrains Mono', monospace; margin-top: 6px; line-height: 1.5;">{display_roman}</div>
</div>"""

            st.markdown(f"""<div class="ui-card">
<div class="ui-card-header">
<span>{t('target_label', 'TARGET')}: {get_native_name(TARGET_LANG)}</span>
<span class="badge badge-primary">{get_language_name(TARGET_LANG).upper()}</span>
</div>
<div style="font-size: 1.25rem; line-height: 1.6; font-weight: 600; color: #111113;">
{data['translation']}
</div>
{roman_html}
</div>""", unsafe_allow_html=True)

        # ── Audio Player Card ────────────────────────────────
        st.markdown(f"""
        <div class="audio-player-card">
            <div class="audio-player-meta">
                <div class="audio-player-title">
                    {t('audio_playback', 'AUDIO PLAYBACK')}
                </div>
                <span class="badge badge-accent">CONTINUOUS STREAM</span>
            </div>
        """, unsafe_allow_html=True)

        col_play1, col_play2 = st.columns([2, 1], gap="medium")

        with col_play1:
            if data.get("audio_bytes"):
                st.audio(data["audio_bytes"], format="audio/wav")
            else:
                if st.button(t('synthesize_stream_btn', "SYNTHESIZE SPEECH STREAM →"), type="primary", use_container_width=True):
                    if TTS_KEY:
                        with st.spinner("Synthesizing audio frames..."):
                            try:
                                aud = text_to_speech_continuous(
                                    api_key=TTS_KEY,
                                    text=data["translation"],
                                    voice=voice_name,
                                    language=TARGET_LANG,
                                    style=tts_style,
                                )
                                data["audio_bytes"] = aud
                                st.audio(aud, format="audio/wav")
                            except Exception as e:
                                st.error(f"TTS error: {e}")
                    else:
                        st.error("TTS Key missing.")

        with col_play2:
            st.markdown(f"""
            <div style="font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: #71717A; line-height: 1.6;">
                {t('voice', 'VOICE')}: {voice_name.upper()}<br>
                {t('target_label', 'TARGET')}: {get_language_name(TARGET_LANG).upper()}<br>
                ENGINE: INDIC-SPEAK
            </div>
            """, unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        # Record in history
        hist = st.session_state.get("translation_history", [])
        if not hist or hist[-1].get("translation") != data["translation"]:
            hist.append({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "input": data["input_text"][:80],
                "translation": data["translation"],
                "target": TARGET_LANG,
            })
            st.session_state["translation_history"] = hist

# ── History Section ──────────────────────────────────────────

if st.session_state.get("translation_history"):
    st.divider()
    with st.expander(t('session_logs', "SESSION LOGS (RECENT TRANSLATIONS)"), expanded=False):
        for i, entry in enumerate(reversed(st.session_state.translation_history[-6:])):
            st.markdown(f"**[{entry['timestamp']}]** `{entry['input']}` -> **{entry['translation']}**")
            st.divider()
