"""Scheme Saathi web app.  Run:  streamlit run app.py"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import scheme_summariser as ss

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data" / "sample_scheme_notice.pdf"
load_dotenv(HERE / ".env")

st.set_page_config(page_title="Scheme Saathi", page_icon="📜", layout="wide")


@st.cache_resource
def client() -> ss.Bodhan:
    return ss.Bodhan(cache_dir=ss.OUTPUTS / "cache")


# Every API result is cached on the input, so re-runs and language switches don't re-bill.
@st.cache_data(show_spinner=False)
def read_document(data: bytes, name: str) -> tuple[list[bytes], str]:
    pages = ss.document_to_pages(data, name)
    ocr = ss.ocr_pages(client(), pages)
    return pages, ss.ocr_to_markdown(ocr)


@st.cache_data(show_spinner=False)
def make_card(markdown: str) -> ss.SchemeCard:
    return ss.build_card(markdown, client())


@st.cache_data(show_spinner=False)
def english(_card: ss.SchemeCard, key: str) -> ss.SchemeCard:
    return ss.english_card(client(), _card)


@st.cache_data(show_spinner=False)
def localise(_card: ss.SchemeCard, key: str, lang: str) -> ss.LocalCard:
    return ss.localise_card(client(), _card, lang)


@st.cache_data(show_spinner=False)
def section_audio(_local: ss.LocalCard, key: str, lang: str) -> dict[str, bytes]:
    return ss.speak_card(client(), _local)


@st.cache_data(show_spinner=False)
def answer(_card: ss.SchemeCard, key: str, lang: str, audio: bytes | None, text: str | None) -> ss.SpokenAnswer:
    return ss.ask(client(), _card, lang, audio=audio, text=text)


@contextmanager
def api_step(message: str):
    """Spinner for one API step; a Bodhan error stops the page with a readable message, not a traceback."""
    with st.spinner(message):
        try:
            yield
        except ss.BudgetExceeded as exc:
            st.error(f"Budget used up: {exc}")
            st.stop()
        except ss.BodhanError as exc:
            busy = str(exc).startswith("429")
            st.error(
                "Bodhan is busy right now (rate limit). Wait a minute and press **R** to retry; finished steps are cached."
                if busy
                else f"Bodhan API error: {exc}"
            )
            st.stop()


# ---------------------------------------------------------------------------- sidebar

st.sidebar.title("📜 Scheme Saathi")
st.sidebar.caption("Government scheme notices, explained in your language. Powered only by Bodhan AI models.")

missing = client().missing_keys()
if missing:
    names = ", ".join(f"`{ss.MODEL_KEY_VARS[m]}`" for m in missing)
    st.error(f"No key for {', '.join(missing)}. Set {names} or `BODHAN_API_KEY` in `.env` (see `.env.example`). Keys: console.bodhan.ai.")
    st.stop()

lang_codes = [c for c in ss.LANGUAGES if c != "en"] + ["en"]
lang = st.sidebar.selectbox("Your language", lang_codes, format_func=lambda c: ss.LANGUAGES[c][0])
uploaded = st.sidebar.file_uploader("Scheme notice (PDF, PNG or JPEG)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    data, name = uploaded.getvalue(), uploaded.name
elif SAMPLE.exists():
    data, name = SAMPLE.read_bytes(), SAMPLE.name
    st.sidebar.info("Using the synthetic sample notice. Upload your own above.")
else:
    st.info("Upload a scheme notice, or run `python make_sample_notice.py` to create the sample.")
    st.stop()

doc_key = hashlib.sha256(data).hexdigest()[:16]

# ---------------------------------------------------------------------------- 1. read

with api_step("Reading the notice with indic-ocr…"):
    pages, markdown = read_document(data, name)
with api_step("Finding the sections…"):
    card = make_card(markdown)

st.title(card.title)
missing = [ss.FIELD_LABELS[k] for k, v in card.fields.items() if not v]
cols = st.columns(4)
cols[0].metric("Pages read", len(pages))
cols[1].metric("Sections found", f"{len(ss.FIELDS) - len(missing)}/{len(ss.FIELDS)}")
cols[2].metric("Dates", len(card.facts["dates"]))
cols[3].metric("Amounts", len(card.facts["amounts"]))

tab_card, tab_ask, tab_source = st.tabs([f"🗂️ Scheme card ({ss.LANGUAGES[lang][0]})", "🎙️ Ask a question", "📄 Original notice"])

# ---------------------------------------------------------------------------- 2. card

with tab_card:
    with api_step(f"Translating into {ss.LANGUAGES[lang][0]}…"):
        local = localise(card, doc_key, lang)
    play = st.toggle("🔊 Read each section aloud (indic-speak)", value=False)
    clips = {}
    if play:
        with api_step("Generating audio, about 3 minutes for a full card…"):
            clips = section_audio(local, doc_key, lang)
        st.audio(ss.join_wavs(clips.values(), gap_ms=600), format="audio/wav")
        st.caption("Whole card above, or play one section below.")

    for key, _, _ in ss.FIELDS:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            left.subheader(local.labels[key])
            lines = local.fields.get(key) or []
            if lines:
                left.markdown("\n".join(f"- {line}" for line in lines))
            else:
                left.warning(local.not_stated)
            if key in clips:
                left.audio(clips[key], format="audio/wav")
            source = card.fields.get(key) or []
            if source and lang != "en":
                heading = card.headings.get(key, "")
                right.caption(f"From the notice{f' — “{heading}”' if heading else ''}:")
                right.markdown("\n".join(f"- {line}" for line in source))

    st.divider()
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Card (Markdown)", local.to_markdown(), f"scheme_card_{lang}.md")
    d2.download_button("⬇️ WhatsApp text (roman script)" if local.roman else "⬇️ WhatsApp text", local.to_whatsapp(), f"scheme_card_{lang}.txt")
    if clips:
        d3.download_button("⬇️ Audio (WAV)", ss.join_wavs(clips.values(), gap_ms=600), f"scheme_card_{lang}.wav")
    if local.roman:
        with st.expander("WhatsApp preview (roman script)"):
            st.code(local.to_whatsapp(), language=None)

# ---------------------------------------------------------------------------- 3. ask

with tab_ask:
    st.write(
        f"Ask in **{ss.LANGUAGES[lang][0]}**, by voice (up to ~30 seconds) or by typing, in native or roman script. "
        "The answer is read back from the notice itself; if the notice doesn't cover it, you'll be told so."
    )
    voice = st.audio_input("Record your question")
    typed = st.text_input("…or type it", placeholder="e.g. kaun se documents chahiye?" if lang == "hi" else "")
    if voice is not None or typed:
        audio = voice.getvalue() if voice is not None else None
        with api_step("Listening, matching and answering…"):
            reply = answer(english(card, doc_key), doc_key, lang, audio, None if audio else typed)
        st.markdown(f"**You asked:** {reply.question}")
        if lang != "en":
            st.caption(f"In English: {reply.question_en}")
        (st.success if reply.answer.found else st.warning)(reply.text)
        if reply.audio:
            st.audio(reply.audio, format="audio/wav", autoplay=True)
        if reply.answer.found:
            with st.expander("Source lines from the notice"):
                st.markdown("\n".join(f"- {line}" for line in reply.answer.source_lines))

# ---------------------------------------------------------------------------- source

with tab_source:
    left, right = st.columns(2)
    for i, png in enumerate(pages, 1):
        left.image(png, caption=f"Page {i}", width="stretch")
    right.markdown("**indic-ocr output**")
    right.code(markdown, language="markdown")
    if any(card.facts.values()):
        right.markdown("**Key facts spotted**")
        right.json(card.facts)
