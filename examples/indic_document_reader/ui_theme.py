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


def render_page_header(title: str, subtitle: str, section_code: str = "SYSTEM"):
    """
    Renders an objective Swiss top navigation bar with category breadcrumb
    and language/voice metadata. Strict zero-emoji policy.
    """
    apply_custom_css()
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
