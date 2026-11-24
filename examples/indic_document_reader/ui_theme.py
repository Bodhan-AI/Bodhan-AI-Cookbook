"""
UI Theme & Design System — Minimalism & Swiss Style (Light Mode Architecture)
Implements mathematical grids, objective typographic hierarchy, sharp geometry (0-2px radii),
high-contrast monochrome palette (#FFFFFF surface, #FAFAF9 background, #111113 off-black text/actions,
#B38B6D taupe accents), and strict zero-emoji interface rules.
"""

import streamlit as st
from constants import get_language_name, get_native_name
from i18n import t

def apply_custom_css():
    """Injects Swiss Style Light Mode design tokens and CSS overrides into the page."""
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');

        /* ── Base Reset & Swiss Typography (Light Palette) ── */
        .stApp {
            font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important;
            background-color: #FAFAF9 !important;
            color: #111113 !important;
            letter-spacing: -0.01em;
        }

        /* ── Headings: Bold, Objective, Tight Tracking ── */
        h1, h2, h3, h4, h5, h6 {
            font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important;
            font-weight: 700 !important;
            letter-spacing: -0.03em !important;
            color: #111113 !important;
        }

        p, span, label, div {
            font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
            letter-spacing: -0.01em;
            color: inherit;
        }

        /* ── Monospace Utilities ── */
        .swiss-mono, code, pre {
            font-family: "JetBrains Mono", "SF Mono", Consolas, monospace !important;
            font-size: 0.85rem !important;
            letter-spacing: -0.02em !important;
            background-color: #F4F4F5 !important;
            color: #111113 !important;
            border: 1px solid #E4E4E7 !important;
            border-radius: 0px !important;
            padding: 2px 4px;
        }

        /* ── Swiss Grid Top Header Bar (Light Surface) ── */
        .swiss-topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 20px;
            background: #FFFFFF;
            border: 1px solid #E4E4E7;
            border-radius: 0px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        }
        .swiss-topbar-title {
            font-size: 1.1rem;
            font-weight: 700;
            color: #111113;
            letter-spacing: -0.02em;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .swiss-topbar-meta {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* ── Minimal Sharp Badges (No rounded pills) ── */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 8px;
            border-radius: 2px;
            font-size: 0.72rem;
            font-weight: 600;
            line-height: 1.2;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-family: "JetBrains Mono", monospace;
        }
        .badge-primary {
            background: #111113;
            color: #FFFFFF;
            border: 1px solid #111113;
        }
        .badge-accent {
            background: #FAF6F0;
            color: #B38B6D;
            border: 1px solid #B38B6D;
        }
        .badge-neutral {
            background: #F4F4F5;
            color: #52525B;
            border: 1px solid #E4E4E7;
        }
        .badge-active {
            background: #E4E4E7;
            color: #111113;
            border: 1px solid #D4D4D8;
        }

        /* ── Sharp Surface Cards (0-2px corner radius) ── */
        .ui-card {
            background: #FFFFFF;
            border: 1px solid #E4E4E7;
            border-radius: 0px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
            color: #111113;
        }
        .ui-card:hover {
            border-color: #D4D4D8;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
        }
        .ui-card-header {
            font-size: 1rem;
            font-weight: 700;
            color: #111113;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            letter-spacing: -0.02em;
            border-bottom: 1px solid #E4E4E7;
            padding-bottom: 10px;
        }

        /* ── Sharp Rectangular Buttons (0px base radius) ── */
        .stButton > button {
            border-radius: 0px !important;
            padding: 12px 20px !important;
            font-size: 0.88rem !important;
            font-weight: 600 !important;
            min-height: 44px !important;
            letter-spacing: -0.01em !important;
            transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.1s ease !important;
            border: 1px solid #D4D4D8 !important;
        }
        .stButton > button[kind="primary"] {
            background: #111113 !important;
            color: #FFFFFF !important;
            border: 1px solid #111113 !important;
        }
        .stButton > button[kind="primary"]:hover {
            background: #27272A !important;
            border-color: #27272A !important;
            color: #FFFFFF !important;
        }
        .stButton > button[kind="primary"]:active {
            transform: translateY(1px) !important;
        }
        .stButton > button[kind="secondary"] {
            background: #FFFFFF !important;
            border: 1px solid #D4D4D8 !important;
            color: #111113 !important;
        }
        .stButton > button[kind="secondary"]:hover {
            background: #F4F4F5 !important;
            border-color: #A1A1AA !important;
            color: #111113 !important;
        }

        /* ── Inputs, Textareas, Selectboxes ── */
        .stTextInput > div > div > input,
        .stTextArea > div > div > textarea,
        .stSelectbox > div > div {
            background-color: #FFFFFF !important;
            border: 1px solid #D4D4D8 !important;
            border-radius: 0px !important;
            color: #111113 !important;
            font-size: 0.92rem !important;
        }
        .stTextInput > div > div > input:focus,
        .stTextArea > div > div > textarea:focus {
            border-color: #111113 !important;
            box-shadow: none !important;
        }

        /* ── Tabs (Grid-Aligned, Sharp) ── */
        .stTabs [data-baseweb="tab-list"] {
            gap: 0px;
            background-color: #F4F4F5;
            padding: 0px;
            border-radius: 0px;
            border-bottom: 1px solid #E4E4E7;
        }
        .stTabs [data-baseweb="tab"] {
            height: 44px;
            border-radius: 0px;
            font-size: 0.85rem;
            font-weight: 600;
            color: #71717A;
            border: none !important;
            padding: 0 20px;
            letter-spacing: -0.01em;
        }
        .stTabs [data-baseweb="tab"][aria-selected="true"] {
            background-color: #FFFFFF !important;
            color: #111113 !important;
            border-bottom: 2px solid #111113 !important;
        }

        /* ── Audio Card (Structured & Clean) ── */
        .audio-player-card {
            background: #FFFFFF;
            border: 1px solid #E4E4E7;
            border-radius: 0px;
            padding: 20px;
            margin: 18px 0;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
            color: #111113;
        }
        .audio-player-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
            border-bottom: 1px solid #E4E4E7;
            padding-bottom: 8px;
        }
        .audio-player-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: #111113;
            letter-spacing: -0.02em;
        }

        /* ── Expander ── */
        .streamlit-expanderHeader {
            background-color: #FFFFFF !important;
            border: 1px solid #E4E4E7 !important;
            border-radius: 0px !important;
            font-weight: 600 !important;
            color: #111113 !important;
        }

        /* ── Dividers ── */
        hr {
            border-color: #E4E4E7 !important;
            margin: 24px 0 !important;
        }

        /* ── Sidebar Styling ── */
        [data-testid="stSidebar"] {
            background-color: #F4F4F5 !important;
            border-right: 1px solid #E4E4E7 !important;
        }

        /* Radio buttons and checkboxes */
        .stRadio > div {
            gap: 12px;
        }
    </style>
    """, unsafe_allow_html=True)



def render_sidebar():
    """
    Renders universal Swiss-style sidebar with persistent, instant language selector,
    active configuration details, and system metadata across all pages.
    """
    from constants import get_translation_languages, language_display_options, get_default_voice

    with st.sidebar:
        st.markdown(f"""
        <div style="padding: 12px 0 16px 0; border-bottom: 1px solid #E4E4E7; margin-bottom: 16px;">
            <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;">
                {t('sys_core', 'SYSTEM 01 / CORE')}
            </div>
            <div style="font-size: 1.25rem; font-weight: 700; color: #111113; letter-spacing: -0.02em; margin-top: 4px;">
                {t('app_title', 'BODHAN READER')}
            </div>
            <div style="font-size: 0.8rem; color: #71717A; margin-top: 2px;">
                {t('app_subtitle', 'Indic Multimodal Interface')}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # If onboarding is not complete, show pending configuration card
        if not st.session_state.get("onboarding_complete", False):
            st.markdown(f"""
            <div class="ui-card" style="padding: 14px; margin-bottom: 16px;">
                <div style="font-size: 0.68rem; font-family: 'JetBrains Mono', monospace; color: #B38B6D; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;">
                    {t('init_badge', 'SYSTEM INITIALIZATION')}
                </div>
                <div style="font-size: 0.82rem; color: #52525B; margin-top: 8px; line-height: 1.5;">
                    {t('pending_config', 'Configuration pending. Select target language to initialize.')}
                </div>
            </div>
            """, unsafe_allow_html=True)
            st.markdown("""
            <div style="font-size: 0.7rem; font-family: 'JetBrains Mono', monospace; color: #A1A1AA; letter-spacing: 0.05em; text-transform: uppercase; margin-top: 16px;">
                ENGINE: BODHAN.AI API<br>
                GRID: SWISS LIGHT 1280
            </div>
            """, unsafe_allow_html=True)
            return

        # Global Language Switcher (Active after onboarding)
        st.markdown(f"""
        <div style="font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; margin-bottom: 6px;">
            {t('pref_lang_label', 'TARGET LANGUAGE')}
        </div>
        """, unsafe_allow_html=True)

        lang_options = language_display_options(get_translation_languages())
        current_lang = st.session_state.get("preferred_language", "en") or "en"

        def _on_sidebar_lang_change():
            sel_disp = st.session_state.get("global_sidebar_lang_selector")
            code = lang_options.get(sel_disp, "en")
            if code:
                st.session_state.preferred_language = code
                st.session_state.voice_name = get_default_voice(code)

        current_display = None
        for display, code in lang_options.items():
            if code == current_lang:
                current_display = display
                break

        current_index = list(lang_options.keys()).index(current_display) if current_display else 0

        # Keep widget key in sync with current_display if it was changed elsewhere
        if "global_sidebar_lang_selector" in st.session_state and current_display:
            if st.session_state["global_sidebar_lang_selector"] != current_display:
                st.session_state["global_sidebar_lang_selector"] = current_display

        st.selectbox(
            "Global Target Language",
            options=list(lang_options.keys()),
            index=current_index,
            label_visibility="collapsed",
            key="global_sidebar_lang_selector",
            on_change=_on_sidebar_lang_change,
        )

        # Active target metadata card
        st.markdown(f"""
        <div class="ui-card" style="padding: 12px; margin-top: 14px; margin-bottom: 16px;">
            <div style="font-size: 0.68rem; font-family: 'JetBrains Mono', monospace; color: #71717A; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;">
                {t('active_target', 'ACTIVE TARGET')}
            </div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #111113; margin: 4px 0 2px 0;">
                {get_native_name(current_lang)}
            </div>
            <div style="font-size: 0.78rem; color: #52525B;">
                {get_language_name(current_lang)}
            </div>
            <div style="display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap;">
                <span class="badge badge-primary">{t('voice', 'VOICE')}: {st.session_state.get('voice_name', 'Default')}</span>
                <span class="badge badge-neutral">{t('script', 'SCRIPT')}: {st.session_state.get('script_mode', 'native').upper()}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div style="font-size: 0.7rem; font-family: 'JetBrains Mono', monospace; color: #A1A1AA; letter-spacing: 0.05em; text-transform: uppercase; margin-top: 16px;">
            ENGINE: BODHAN.AI API<br>
            GRID: SWISS LIGHT 1280
        </div>
        """, unsafe_allow_html=True)


def render_page_header(title: str, subtitle: str, section_code: str = "SYSTEM"):
    """
    Renders an objective Swiss top navigation bar with category breadcrumb
    and language/voice metadata. Strict zero-emoji policy.
    """
    apply_custom_css()
    render_sidebar()
    target_lang = st.session_state.get("preferred_language", "en")
    voice_name = st.session_state.get("voice_name", "Kavya")

    lang_display = f"{get_native_name(target_lang)} / {get_language_name(target_lang)}" if target_lang else "Not Selected"

    target_badge_label = t("target_label", "TARGET")
    voice_badge_label = t("voice", "VOICE")

    st.markdown(f"""
    <div class="swiss-topbar">
        <div class="swiss-topbar-title">
            <span class="badge badge-accent">{section_code}</span>
            <span>{title}</span>
        </div>
        <div class="swiss-topbar-meta">
            <span class="badge badge-neutral">
                {target_badge_label}: {lang_display}
            </span>
            <span class="badge badge-active">
                {voice_badge_label}: {voice_name}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if subtitle:
        st.markdown(f"""
        <div style="font-size: 0.92rem; color: #52525B; line-height: 1.5; margin-bottom: 20px; max-width: 800px;">
            {subtitle}
        </div>
        """, unsafe_allow_html=True)
