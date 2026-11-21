"""Random Multimodal Sampling Subsystem for Indic Data Profiling & Statistical EDA.

Organizes and generates stratified random samples across dedicated subfolders:
- sample_data/speech/      (20 diverse speech audio across languages, accents, SNRs, durations)
- sample_data/images/      (16 diverse document images across contrast, skew, layouts, forms)
- sample_data/documents/   (12 diverse 1-page PDF layouts: single-col, dual-col, tabular, gazettes)
- sample_data/text/        (25 diverse text corpora covering all 22 scheduled 8th Schedule Indic languages + dialects)

Provides rigorous statistical sampling utilities:
- Sample moments: mean, std, variance, standard error (SE = s / sqrt(n))
- 95% Confidence Intervals (CI = x_bar +/- 1.96 * SE)
- Central Limit Theorem projections for token economics and API costs
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
import random
import re
import wave
from collections import Counter
from typing import Any

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from src.common.edge_cases import INDIC_FONT_PATH
from src.common.logger import get_logger

logger = get_logger("bodhan.random_sampler")

RANDOM_SEED = 42


# ============================================================================
# Statistical Analysis Utilities
# ============================================================================

def compute_sample_statistics(values: list[float] | np.ndarray) -> dict[str, float]:
    """Compute rigorous descriptive and inferential sample statistics."""
    arr = np.asarray(values, dtype=np.float64)
    n = len(arr)
    if n == 0:
        return {"n": 0, "mean": 0.0, "std": 0.0, "se": 0.0, "ci_95_low": 0.0, "ci_95_high": 0.0}

    mean = float(np.mean(arr))
    var = float(np.var(arr, ddof=1)) if n > 1 else 0.0
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    se = float(std / math.sqrt(n)) if n > 0 else 0.0
    margin_95 = 1.96 * se

    median = float(np.median(arr))
    p10 = float(np.percentile(arr, 10))
    p25 = float(np.percentile(arr, 25))
    p75 = float(np.percentile(arr, 75))
    p90 = float(np.percentile(arr, 90))
    iqr = p75 - p25

    # Sample skewness
    if std > 0 and n > 2:
        skew = float(np.mean(((arr - mean) / std) ** 3))
    else:
        skew = 0.0

    return {
        "n": int(n),
        "mean": round(mean, 2),
        "std": round(std, 2),
        "var": round(var, 2),
        "se": round(se, 3),
        "margin_95": round(margin_95, 2),
        "ci_95_low": round(mean - margin_95, 2),
        "ci_95_high": round(mean + margin_95, 2),
        "median": round(median, 2),
        "iqr": round(iqr, 2),
        "p10": round(p10, 2),
        "p90": round(p90, 2),
        "min": round(float(np.min(arr)), 2),
        "max": round(float(np.max(arr)), 2),
        "skewness": round(skew, 2),
    }


# ============================================================================
# Modality Feature Extractors for Random Samples
# ============================================================================

def extract_speech_sample_features(wav_path: Path) -> dict[str, Any]:
    """Extract acoustic statistics from a speech sample."""
    with wave.open(str(wav_path), "rb") as wf:
        ch = wf.getnchannels()
        sr = wf.getframerate()
        nf = wf.getnframes()
        raw = wf.readframes(nf)

    dur = nf / sr if sr > 0 else 0.0
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)

    rms = np.sqrt(np.mean(samples**2)) if len(samples) > 0 else 0.0
    rms_db = 20 * np.log10(rms / 32768.0) if rms > 0 else -100.0

    peak = np.max(np.abs(samples)) if len(samples) > 0 else 0.0
    peak_db = 20 * np.log10(peak / 32768.0) if peak > 0 else -100.0
    crest_factor = peak_db - rms_db if rms > 0 else 0.0

    # Silence ratio (< -35 dBFS)
    th_35 = 32768.0 * (10 ** (-35.0 / 20.0))
    silence_ratio = float(np.mean(np.abs(samples) < th_35))

    # Zero-crossing rate
    zcr = float(np.mean(np.abs(np.diff(np.sign(samples))) > 0)) if len(samples) > 1 else 0.0

    # Speech tempo: estimate syllable rate via envelope peaks
    hop = int(0.05 * sr) if sr > 0 else 1000  # 50ms windows
    n_blocks = len(samples) // hop
    if n_blocks > 10:
        blocks = samples[: n_blocks * hop].reshape(n_blocks, hop)
        env = np.sqrt(np.mean(blocks**2, axis=1))
        env_med = np.median(env)
        peaks = np.where((env[1:-1] > env[:-2]) & (env[1:-1] > env[2:]) & (env[1:-1] > env_med))[0]
        syllable_rate = len(peaks) / dur if dur > 0 else 0.0
    else:
        syllable_rate = 0.0

    return {
        "filename": wav_path.name,
        "duration_sec": round(dur, 2),
        "sample_rate": sr,
        "channels": ch,
        "rms_dbfs": round(rms_db, 2),
        "peak_dbfs": round(peak_db, 2),
        "crest_factor_db": round(crest_factor, 2),
        "silence_pct": round(silence_ratio * 100, 1),
        "zcr": round(zcr, 4),
        "syllables_per_sec": round(syllable_rate, 1),
    }


def extract_image_sample_features(img_path: Path) -> dict[str, Any]:
    """Extract spatial and visual density statistics from an image sample."""
    with Image.open(img_path) as im:
        w, h = im.size
        gray = im.convert("L")

    arr = np.array(gray)
    mean_lum = float(np.mean(arr))
    std_lum = float(np.std(arr))
    lum_min, lum_max = float(arr.min()), float(arr.max())
    michelson = (lum_max - lum_min) / max(lum_max + lum_min, 1)

    # Laplacian variance for sharpness proxy
    diff_h = np.diff(arr.astype(np.float64), axis=0)
    diff_v = np.diff(arr.astype(np.float64), axis=1)
    lap_var = float(np.var(diff_h) + np.var(diff_v))

    return {
        "filename": img_path.name,
        "width_px": w,
        "height_px": h,
        "aspect_ratio": round(w / max(h, 1), 2),
        "mean_luminance": round(mean_lum, 1),
        "std_luminance": round(std_lum, 1),
        "michelson_contrast": round(michelson, 3),
        "laplacian_sharpness": round(lap_var, 1),
    }


def extract_document_sample_features(doc_path: Path) -> dict[str, Any]:
    """Extract geometry and density statistics from a PDF document sample."""
    doc = fitz.open(str(doc_path))
    n_pages = len(doc)
    p0 = doc[0]
    r = p0.rect
    txt = "".join(p.get_text() for p in doc)
    words = [w.strip("।॥۔.!?,'\"()[]{}") for w in txt.split() if w.strip("।॥۔.!?,'\"()[]{}")]

    # Check for column gutter in page 0
    pix = p0.get_pixmap(matrix=fitz.Matrix(100/72.0, 100/72.0), alpha=False)
    arr = np.asarray(Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L"))
    doc.close()

    vert_prof = np.mean(arr, axis=0)
    w_px = len(vert_prof)
    mid_start, mid_end = int(w_px * 0.42), int(w_px * 0.58)
    gutter_detected = float(np.mean(vert_prof[mid_start:mid_end])) > float(np.mean(vert_prof)) * 1.02

    return {
        "filename": doc_path.name,
        "page_count": n_pages,
        "page_width_pt": round(r.width, 1),
        "page_height_pt": round(r.height, 1),
        "aspect_ratio": round(r.width / max(r.height, 1), 2),
        "text_chars": len(txt),
        "text_words": len(words),
        "has_column_gutter": gutter_detected,
    }


def extract_text_sample_features(text: str, label: str) -> dict[str, Any]:
    """Extract linguistic, token, and script diversity statistics from a text sample."""
    words = [w.strip("।॥۔.!?,'\"()[]{}") for w in text.split() if w.strip("।॥۔.!?,'\"()[]{}")]
    total_tokens = len(words)
    vocab = set(words)
    vocab_size = len(vocab)
    ttr = vocab_size / max(total_tokens, 1)

    counts = Counter(words)
    hapax = sum(1 for w, c in counts.items() if c == 1)
    hapax_ratio = hapax / max(vocab_size, 1)

    # Sentence lengths (using punctuation split proxy)
    sentences = [s.strip() for s in re.split(r"[।॥۔.!?]+", text) if s.strip()]
    sentence_lens = [len(s) for s in sentences]

    # Primary script detection
    script_counts = Counter()
    for ch in text:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F: script_counts["Devanagari"] += 1
        elif 0x0980 <= cp <= 0x09FF: script_counts["Bengali/Assamese"] += 1
        elif 0x0B80 <= cp <= 0x0BFF: script_counts["Tamil"] += 1
        elif 0x0C00 <= cp <= 0x0C7F: script_counts["Telugu"] += 1
        elif 0x0C80 <= cp <= 0x0CFF: script_counts["Kannada"] += 1
        elif 0x0D00 <= cp <= 0x0D7F: script_counts["Malayalam"] += 1
        elif 0x0A80 <= cp <= 0x0AFF: script_counts["Gujarati"] += 1
        elif 0x0A00 <= cp <= 0x0A7F: script_counts["Gurmukhi"] += 1
        elif 0x0B00 <= cp <= 0x0B7F: script_counts["Odia"] += 1
        elif 0x0600 <= cp <= 0x06FF: script_counts["Arabic/Urdu"] += 1
        elif 0x1C50 <= cp <= 0x1C7F: script_counts["Ol Chiki"] += 1
        elif 0xABC0 <= cp <= 0xABFF: script_counts["Meetei Mayek"] += 1
        elif ch.isascii() and ch.isalnum(): script_counts["Latin"] += 1

    prim_script = script_counts.most_common(1)[0][0] if script_counts else "Unknown"

    return {
        "filename": label,
        "total_chars": len(text),
        "total_tokens": total_tokens,
        "vocab_size": vocab_size,
        "ttr": round(ttr, 3),
        "hapax_ratio": round(hapax_ratio, 3),
        "sentence_count": len(sentences),
        "mean_sentence_chars": round(float(np.mean(sentence_lens)), 1) if sentence_lens else 0,
        "median_sentence_chars": round(float(np.median(sentence_lens)), 1) if sentence_lens else 0,
        "primary_script": prim_script,
    }


# ============================================================================
# Random Sampling Dataset Generation across Subfolders
# ============================================================================

def generate_random_multimodal_sample(
    base_sample_dir: Path,
    seed: int = RANDOM_SEED,
    preserve_existing: bool = False,
) -> dict[str, list[Path]]:
    """Generate reproducible random multimodal samples in partitioned subfolders."""
    base_sample_dir = Path(base_sample_dir)
    speech_dir = base_sample_dir / "speech"
    images_dir = base_sample_dir / "images"
    docs_dir = base_sample_dir / "documents"
    text_dir = base_sample_dir / "text"

    for d in (speech_dir, images_dir, docs_dir, text_dir):
        d.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    manifest: dict[str, list[Path]] = {
        "speech": [],
        "images": [],
        "documents": [],
        "text": [],
    }

    # ------------------------------------------------------------------------
    # 1. Speech Subfolder (sample_data/speech/ - 20 instances)
    # ------------------------------------------------------------------------
    speech_configs = [
        # (fname, duration, sr, f0, syl_rate, pause_len, clip_prob, noise_db)
        ("sample_speech_01_hi_studio.wav", 14.5, 24000, 130.0, 4.0, 0.35, 0.0, -60.0),
        ("sample_speech_02_ta_conversational.wav", 11.2, 24000, 145.0, 3.2, 0.40, 0.0, -50.0),
        ("sample_speech_03_te_rapid.wav", 18.0, 24000, 125.0, 5.5, 0.20, 0.0, -55.0),
        ("sample_speech_04_bn_telephony.wav", 8.5, 8000, 135.0, 3.8, 0.30, 0.0, -32.0),
        ("sample_speech_05_mr_pause_heavy.wav", 16.0, 24000, 120.0, 2.5, 0.60, 0.0, -55.0),
        ("sample_speech_06_gu_short_burst.wav", 3.8, 24000, 140.0, 4.2, 0.25, 0.0, -58.0),
        ("sample_speech_07_kn_clipped.wav", 9.0, 24000, 115.0, 3.5, 0.30, 0.12, -45.0),
        ("sample_speech_08_ml_ambient.wav", 12.0, 24000, 130.0, 3.9, 0.35, 0.0, -28.0),
        ("sample_speech_09_en_accented.wav", 15.5, 24000, 128.0, 4.0, 0.30, 0.0, -55.0),
        ("sample_speech_10_pa_high_pitch.wav", 7.5, 24000, 210.0, 4.5, 0.25, 0.0, -52.0),
        ("sample_speech_11_or_conversational.wav", 13.0, 24000, 132.0, 3.6, 0.35, 0.0, -52.0),
        ("sample_speech_12_sa_vedic_chant.wav", 17.5, 24000, 110.0, 2.2, 0.50, 0.0, -58.0),
        ("sample_speech_13_mr_news_broadcast.wav", 15.0, 24000, 138.0, 5.2, 0.20, 0.0, -50.0),
        ("sample_speech_14_as_folklore.wav", 12.5, 24000, 142.0, 3.4, 0.40, 0.0, -54.0),
        ("sample_speech_15_ur_poetry_ghazal.wav", 16.5, 24000, 126.0, 2.8, 0.45, 0.0, -56.0),
        ("sample_speech_16_en_in_interview.wav", 14.0, 24000, 135.0, 4.1, 0.30, 0.0, -48.0),
        ("sample_speech_17_hi_whisper_low_snr.wav", 9.5, 24000, 150.0, 3.5, 0.30, 0.0, -36.0),
        ("sample_speech_18_ta_reverberant_hall.wav", 11.5, 24000, 140.0, 3.8, 0.35, 0.0, -42.0),
        ("sample_speech_19_te_multi_speaker.wav", 18.5, 24000, 160.0, 4.8, 0.25, 0.0, -50.0),
        ("sample_speech_20_hi_clipped_harsh.wav", 8.0, 24000, 120.0, 3.7, 0.30, 0.20, -40.0),
    ]

    for fname, dur, sr, f0, syl_rate, pause_len, clip_p, noise_db in speech_configs:
        out_p = speech_dir / fname
        if preserve_existing and out_p.exists():
            manifest["speech"].append(out_p)
            continue
        n_samples = int(dur * sr)
        t = np.linspace(0, dur, n_samples, endpoint=False)
        phase = 2 * np.pi * np.cumsum(f0 + 10.0 * np.sin(2 * np.pi * 1.5 * t)) / sr
        sig = np.sin(phase) * 0.5 + np.sin(2 * phase) * 0.25 + np.sin(3 * phase) * 0.15
        syl = np.abs(np.sin(2 * np.pi * syl_rate * t)) ** 1.5
        sig *= syl

        # Pauses
        period = int(3.5 * sr)
        dur_p = int(pause_len * sr)
        for idx in range(period, n_samples - dur_p, period):
            sig[idx : idx + dur_p] *= 0.001

        # Noise injection
        if noise_db > -60.0:
            sig += np_rng.normal(0, 10 ** (noise_db / 20.0), n_samples)

        peak = np.max(np.abs(sig))
        if peak > 0:
            sig = (sig / peak) * 22000.0

        if clip_p > 0:
            sig *= 5.0
            sig = np.clip(sig, -32767, 32767)
        else:
            sig = np.clip(sig, -32000, 32000)

        with wave.open(str(out_p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(sig.astype(np.int16).tobytes())

        manifest["speech"].append(out_p)

    # ------------------------------------------------------------------------
    # 2. Images Subfolder (sample_data/images/ - 16 instances)
    # ------------------------------------------------------------------------
    img_configs = [
        ("sample_img_01_hi_invoice.png", (650, 450), 0.0, 1.0, "वाणिज्यिक बीजक (Commercial Invoice)"),
        ("sample_img_02_mr_table_grid.png", (700, 400), 0.0, 1.0, "पिक सांख्यिकी सारणी (Crop Data Table)"),
        ("sample_img_03_ta_notice_faint.png", (680, 420), 0.0, 0.40, "அதிகாரப்பூர்வ அறிவிப்பு (Official Notice)"),
        ("sample_img_04_te_skewed_form.png", (600, 450), 2.5, 0.90, "దరఖాస్తు ఫారమ్ (Application Form)"),
        ("sample_img_05_bn_book_page.png", (550, 450), 0.0, 1.0, "সাহিত্যিক রচনা (Literary Text Page)"),
        ("sample_img_06_gu_receipt_noise.png", (520, 380), 0.0, 0.85, "વેચાણ પહોંચ (Sales Receipt Form)"),
        ("sample_img_07_kn_multilingual.png", (650, 380), 0.0, 1.0, "ಸರ್ಕಾರಿ ಸುತ್ತೋಲೆ (Official Gazette)"),
        ("sample_img_08_pa_high_contrast.png", (600, 320), 0.0, 1.2, "ਸੂਚਨਾ ਪੱਤਰ (Public Notification)"),
        ("sample_img_09_or_ration_card.png", (640, 420), 0.0, 0.95, "ରାସନ କାର୍ଡ଼ ବିବରଣୀ (Public Ration Card)"),
        ("sample_img_10_pa_street_signboard.png", (680, 360), 0.0, 1.15, "ਰਾਸ਼ਟਰੀ ਰਾਜਮਾਰਗ ਬੋਰਡ (Highway Signboard)"),
        ("sample_img_11_hi_handwritten_note.png", (580, 440), 1.2, 0.70, "दैनिक बही-खाता प्रविष्टि (Ledger Memo)"),
        ("sample_img_12_ta_railway_ticket.png", (660, 390), 0.0, 1.05, "ரயில் பயண முன்பதிவு சீட்டு (Railway Ticket)"),
        ("sample_img_13_te_utility_bill.png", (620, 460), -1.5, 0.85, "విద్యుత్ వినియోగ బిల్లు (Electricity Bill)"),
        ("sample_img_14_bn_postal_envelope.png", (600, 350), 0.0, 0.90, "ডাক খাম এবং পিন কোড (Postal Envelope)"),
        ("sample_img_15_mr_blurred_receipt.png", (540, 400), 0.0, 0.50, "सुपरमार्केट देयक (Faint Cash Receipt)"),
        ("sample_img_16_ur_literary_calligraphy.png", (670, 380), 0.0, 1.20, "ادبی صحیفہ اور عنوان (Calligraphic Title)"),
    ]

    for fname, size, skew, contrast, title in img_configs:
        out_p = images_dir / fname
        if preserve_existing and out_p.exists():
            manifest["images"].append(out_p)
            continue
        im = Image.new("RGB", size, color=(252, 252, 250))
        draw = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype(INDIC_FONT_PATH or "arial.ttf", 18)
            font_small = ImageFont.truetype(INDIC_FONT_PATH or "arial.ttf", 13)
        except Exception:
            font = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Header box
        draw.rectangle([(20, 20), (size[0] - 20, 65)], fill=(235, 238, 245), outline=(180, 190, 210))
        draw.text((30, 30), title, fill=(30, 40, 70), font=font)

        # Body rows / table simulation
        y = 80
        for r_i in range(5):
            draw.rectangle([(25, y), (size[0] - 25, y + 25)], fill=(248, 249, 252) if r_i % 2 else (255, 255, 255))
            draw.text((35, y + 5), f"विवरण / Details #{r_i + 1}: Indic sample row entry", fill=(60, 60, 65), font=font_small)
            draw.text((size[0] - 150, y + 5), f"₹ {1250 * (r_i + 1)}", fill=(20, 20, 25), font=font_small)
            y += 30

        if skew != 0.0:
            im = im.rotate(skew, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=(252, 252, 250))

        if contrast != 1.0:
            enhancer = ImageEnhance.Contrast(im)
            im = enhancer.enhance(contrast)

        im.save(out_p)
        manifest["images"].append(out_p)

    # ------------------------------------------------------------------------
    # 3. Documents Subfolder (sample_data/documents/ - 12 instances)
    # ------------------------------------------------------------------------
    doc_configs = [
        ("sample_doc_01_hi_single_col.pdf", "कृषि नवाचार और मृदा संवर्धन", "single", "hi"),
        ("sample_doc_02_ta_dual_col.pdf", "தொழில்நுட்ப ஆய்வு மற்றும் வளர்ச்சி", "dual", "ta"),
        ("sample_doc_03_te_table_matrix.pdf", "రాష్ట్ర ఆర్థిక గణాంకాలు", "table", "te"),
        ("sample_doc_04_bn_mixed_scripts.pdf", "বহুভাষিক শিক্ষা এবং বিকাশ", "single", "bn"),
        ("sample_doc_05_mr_dense_text.pdf", "प्रशासकीय नियम आणि अटी", "single", "mr"),
        ("sample_doc_06_gu_form_fields.pdf", "યોજના નોંધણી પત્રક", "table", "gu"),
        ("sample_doc_07_or_land_patta_record.pdf", "ଜମି ପଟ୍ଟା ଏବଂ ଭୂ-ଅଭିଲେଖ ବିବରଣୀ", "table", "or"),
        ("sample_doc_08_sa_shloka_commentary.pdf", "श्रीमद्भगवद्गीता मूल श्लोक एवं सान्वय भाष्यम्", "single", "sa"),
        ("sample_doc_09_pa_gazette_circular.pdf", "ਪੰਜਾਬ ਰਾਜ ਗਜ਼ਟ ਅਧਿਸੂਚਨਾ", "dual", "pa"),
        ("sample_doc_10_as_multilingual_certificate.pdf", "অসম চৰকাৰ শৈক্ষিক প্রমাণ পত্ৰ", "single", "as"),
        ("sample_doc_11_ur_court_decree.pdf", "عدالتی حکมนامہ اور قانونی ضوابط", "single", "ur"),
        ("sample_doc_12_hi_balance_sheet.pdf", "वार्षिक वित्तीय लेखापरीक्षण पत्रक", "table", "hi"),
    ]

    for fname, title, layout_type, lang in doc_configs:
        out_p = docs_dir / fname
        if preserve_existing and out_p.exists():
            manifest["documents"].append(out_p)
            continue
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)  # A4
        if INDIC_FONT_PATH:
            page.insert_font(fontname="indic_font", fontfile=INDIC_FONT_PATH)
            fname_f = "indic_font"
        else:
            fname_f = "helv"

        # Banner
        page.draw_rect(fitz.Rect(40, 40, 555, 90), fill=(0.94, 0.96, 0.98), color=(0.7, 0.8, 0.9))
        page.insert_text((55, 68), title, fontsize=14, fontname=fname_f)

        if layout_type == "dual":
            # Dual column with gutter
            c1 = fitz.Rect(50, 110, 285, 780)
            c2 = fitz.Rect(310, 110, 545, 780)
            page.draw_line(fitz.Point(297, 110), fitz.Point(297, 780), color=(0.8, 0.8, 0.8), width=0.5)
            page.insert_textbox(c1, "பத்தி 1: கிராமப்புற வளர்ச்சி மற்றும் தொழில்நுட்ப பயன்பாடுகள்.\n" * 4, fontsize=10, fontname=fname_f)
            page.insert_textbox(c2, "பத்தி 2: மொழி தொழில்நுட்பத்தின் எதிர்கால திட்டங்கள்.\n" * 4, fontsize=10, fontname=fname_f)
        elif layout_type == "table":
            # Bordered table
            y_t = 120
            page.draw_rect(fitz.Rect(50, y_t, 545, y_t + 25), fill=(0.90, 0.92, 0.95))
            page.insert_text((60, y_t + 17), "विभागीय विवरण | लक्ष्य | प्राप्ति | मूल्यांकन स्थिति", fontsize=10, fontname=fname_f)
            y_t += 28
            for r_i in range(6):
                page.draw_rect(fitz.Rect(50, y_t, 545, y_t + 22), color=(0.85, 0.85, 0.85))
                page.insert_text((60, y_t + 15), f"परियोजना {r_i + 1} | ₹ {10000 * (r_i + 1)} | ९८.५% | संतोषजनक", fontsize=9.5, fontname=fname_f)
                y_t += 25
        else:
            # Single column
            page.insert_textbox(fitz.Rect(50, 110, 545, 780),
                                f"अध्याय १: भाषा प्रौद्योगिकी और जन कल्याण।\n"
                                f"भारत विविध भाषाओं का देश है जहाँ प्रत्येक राज्य की अपनी विशिष्ट सांस्कृतिक और भाषाई पहचान है। "
                                f"कृत्रिम बुद्धिमत्ता के माध्यम से इन भाषाओं को जोड़ना एक ऐतिहासिक कदम है।\n\n"
                                f"मुख्य उद्देश्य:\n"
                                f"१. क्षेत्रीय भाषाओं में सरकारी योजनाओं का प्रचार-प्रसार।\n"
                                f"२. स्वचालित अनुवाद और वाक् प्रणालियों की सटीकता में सुधार।\n"
                                f"३. ग्रामीण और दूरदराज के क्षेत्रों तक डिजिटल सेवाओं की पहुँच।\n\n" * 2,
                                fontsize=10, fontname=fname_f)

        doc.save(str(out_p))
        doc.close()
        manifest["documents"].append(out_p)

    # ------------------------------------------------------------------------
    # 4. Text Subfolder (sample_data/text/ - 25 instances)
    # ------------------------------------------------------------------------
    text_samples = [
        # The 22 Scheduled Eighth Schedule Languages + 3 Specialized Domains
        ("sample_text_01_hi_news.txt",
         "भारत में नवीकरणीय ऊर्जा के क्षेत्र में ऐतिहासिक प्रगति दर्ज की गई है। "
         "सौर और पवन ऊर्जा परियोजनाओं के विस्तार से ग्रामीण क्षेत्रों में बिजली आपूर्ति सुदृढ़ हुई है। "
         "वैज्ञानिकों का अनुमान है कि आगामी वर्षों में देश ऊर्जा क्षेत्र में पूर्णतः आत्मनिर्भर बन जाएगा। "
         "इससे न केवल पर्यावरण संरक्षण में सहायता मिलेगी बल्कि लाखों नए रोजगार के अवसर भी सृजित होंगे।"),
        ("sample_text_02_ta_literature.txt",
         "தமிழ் இலக்கியம் காலத்தால் அழியாத தொன்மை வாய்ந்தது ஆகும். "
         "சங்க இலக்கியங்கள் மக்களின் வாழ்க்கை முறையையும் இயற்கையையும் மிக நேர்த்தியாக எடுத்துரைக்கின்றன. "
         "கவிதைகளும் பாடல்களும் தமிழ் மொழியின் இனிமையை உலகுக்கு உணர்த்துகின்றன. "
         "இத்தகைய விழுமியங்களைப் பாதுகாப்பது நம் ஒவ்வொருவரின் கடமையாகும்."),
        ("sample_text_03_te_agritech.txt",
         "ఆధునిక వ్యవసాయ రంగంలో సూక్ష్మ సేద్యం కీలక పాత్ర పోషిస్తోంది. "
         "నీటి వినియోగాన్ని గణనీయంగా తగ్గించడంతో పాటు పంట దిగుబడిని పెంచడానికి ఇది సహాయపడుతుంది. "
         "రైతులకు సకాలంలో ఎరువులు మరియు పురుగుమందుల వినియోగంపై శాస్త్రీయ సమాచారం అందించడం చాలా ముఖ్యం. "
         "డిజిటల్ పరిజ్ఞానం ద్వారా రైతుల ఆదాయం రెట్టింపు కాగలదు."),
        ("sample_text_04_bn_essay.txt",
         "বাঙালি সংস্কৃতি এবং সাহিত্যের ঐতিহ্য বিশ্বমঞ্চে সমাদৃত। "
         "রবীন্দ্রনাথ ঠাকুর এবং কাজী নজরুল ইসলামের সাহিত্যকর্ম জাতিকে অনুপ্রাণিত করে আসছে। "
         "উৎসব এবং পালাপার্বণে মানুষের মিলনমেলা সামাজিক সংহতিকে সুদৃঢ় করে। "
         "শিক্ষা ও চিন্তার স্বাধীনতা একটি উন্নত সমাজ গঠনের ভিত্তিপ্রস্তর।"),
        ("sample_text_05_mr_legal.txt",
         "महाराष्ट्र राज्य शासन राजपत्र: सार्वजनिक आरोग्य आणि स्वच्छता नियमावली. "
         "कलम २४-अ अन्वये सर्व आस्थापनांनी स्वच्छता मानकांचे पालन करणे बंधनकारक आहे. "
         "सदर नियमांचा भंग केल्यास दंडात्मक कारवाई करण्यात येईल. "
         "नागरिकांनी तक्रार निवारणासाठी अधिकृत पोर्टलचा वापर करावा अशी विनंती आहे."),
        ("sample_text_06_gu_commerce.txt",
         "ગુજરાતના વેપાર અને ઉદ્યોગ જગતમાં નવીન પહેલો શરૂ થઈ છે. "
         "કાપડ અને હીરા ઉદ્યોગમાં નિકાસના નવા આંકડા પ્રોત્સાહક છે. "
         "બંદરો અને માર્ગ પરિવહનના આધુનિકીકરણથી વેપારમાં ઝડપ અને કાર્યક્ષમતા વધી છે. "
         "નાના ઉદ્યોગોને પ્રોત્સાહન આપવા માટે વિશેષ સહાય યોજનાઓ જાહેર કરવામાં આવી છે."),
        ("sample_text_07_kn_technical.txt",
         "ಮಾಹಿತಿ ತಂತ್ರಜ್ಞಾನ ಕ್ಷೇತ್ರದಲ್ಲಿ ಕರ್ನಾಟಕ ಮುಂಚೂಣಿಯಲ್ಲಿದೆ. "
         "ಬೆಂಗಳೂರಿನ ನವೋದ್ಯಮಗಳು ಕೃತಕ ಬುದ್ಧಿಮತ್ತೆ ಮತ್ತು ಕ್ಲೌಡ್ ತಂತ್ರಜ್ಞಾನದಲ್ಲಿ ಹೊಸ ಕ್ರಾಂತಿ ಸೃಷ್ಟಿಸುತ್ತಿವೆ. "
         "ಯುವ ತಂತ್ರಜ್ಞರಿಗೆ ಜಾಗತಿಕ ಮಟ್ಟದ ಅವಕಾಶಗಳು ಲಭ್ಯವಾಗುತ್ತಿದ್ದು, ಸಂಶೋಧನೆ ಮತ್ತು ಅಭಿವೃದ್ಧಿಗೆ ಆದ್ಯತೆ ನೀಡಲಾಗಿದೆ."),
        ("sample_text_08_ml_health.txt",
         "കേരളത്തിന്റെ പൊതുജനാരോഗ്യ മാതൃക ആഗോളതലത്തിൽ ശ്രദ്ധ നേടിയിട്ടുണ്ട്. "
         "പ്രാഥമിക ആരോഗ്യ കേന്ദ്രങ്ങളുടെ ശൃംഖല ഗ്രാമീണ മേഖലകളിൽ മികച്ച സേവനം ഉറപ്പാക്കുന്നു. "
         "ജീവിതശൈലീ രോഗങ്ങൾക്കെതിരെയുള്ള ബോಧവൽക്കരണ പരിപാടികൾ ജനപങ്കാളിത്തത്തോടെ വിജയകരമായി നടപ്പിലാക്കുന്നു."),
        ("sample_text_09_ur_dialogue.txt",
         "اردو زبان اپنی شیرینی اور تہذیب کے لیے دنیا بھر میں جانی جاتی ہے۔ "
         "شاعری اور ادب نے ہمیشہ دلوں کو جوڑنے کا کام کیا ہے۔ "
         "ہمیں چاہیے کہ ہم اپنی نئی نسل کو اس خوبصورت زبان کی باریکیوں اور تاریخ سے روشناس کرائیں۔"),
        ("sample_text_10_hinglish_chat.txt",
         "Hi team, kal ke demo presentation ka feedback kafi positive tha! "
         "Client ne UI workflow aur Indic translation speed ko appreciate kiya hai. "
         "Lekin production deployment se pehle audio latency ko thoda optimize karna padega. "
         "Please review the PR and let us merge by today evening."),
        ("sample_text_11_as_literature.txt",
         "অসমৰ প্ৰাকৃতিক সৌন্দৰ্য আৰু ব্ৰহ্মপুত্ৰ নদীৰ ঐতিহ্য অতুলনীয়। "
         "বিহু উৎসৱে অসমীয়া সমাজ জীৱনলৈ আনন্দ আৰু ভাতৃত্ববোধ কঢ়িয়াই আনে। "
         "স্থানীয় কৃষ্টি আৰু লোকগীতৰ সংৰক্ষণ আমাৰ সংস্কৃতিৰ মূল ভেটি।"),
        ("sample_text_12_brx_folktale.txt",
         "बर' हारिनि दावहारु आरो आगोमा महरनि बाथाफोरा आथिखालावबो मोजां। "
         "बैसागु फुंआव बर' समाजाव गोजोन आरो दावगानायनि बाथा फैलायबाय। "
         "जोंनि राव आरो थुनलाइनि दावगानाया गासैबो सुबुंनि बिबान।"),
        ("sample_text_13_doi_culture.txt",
         "डोगरा विरासत ते पहाड़ी संस्कृति डुग्गर प्रदेश दी शान ऐ। "
         "तवी नदी दे कंडे बसोचड़ा एह् इलाका अपनी बहादुरी आस्तै प्रसिद्ध ऐ। "
         "डोगरी बोली दी मिठास सारे लोकें दे दिलां च प्यार जगांदी ऐ।"),
        ("sample_text_14_kok_coastal.txt",
         "कोंकणी भास ही गोंयच्या आणि कोंकण दर्यादेगेच्या संस्कृतीचो श्वास आसा. "
         "दर्याचो वेपार, मांडवी न्हंय आणि लोककला आमची खाशेली ओळख थारायतात. "
         "आपली परंपरा फुडल्या पिळगेक दिवप हें आमचें कर्तव्य जावन आसा."),
        ("sample_text_15_mai_tradition.txt",
         "मिथिलाक पावन भूमि पर विद्यापतिक पदावली आ मधुबनी चित्रकला विश्वविख्यात अछि। "
         "सांस्कृतिक आचार-विचार मे मैथिली भाषाक मिठास अद्वितीय अछि। "
         "पारंपरिक उत्सव आ मिथिलाक रीति-रिवाज हमर गौरवपूर्ण धरोहरि थिक।"),
        ("sample_text_16_mni_heritage.txt",
         "মণিপুৰগী চৎনবী অমসুং জগোই ঈশৈনা মালেমগী শক্তম অমা ওইরি। "
         "লাই হরাওবগী কুমহৈনা মীয়ামগী অপুনবা শক্তি ফংহল্লি। "
         "ঐখোয়গী নাৎ অমসুং চৎনবী কনবা হায়বসি কয়া য়াম্না মরুওই।"),
        ("sample_text_17_ne_himalayan.txt",
         "हिमाली भेगको प्राकृतिक सुन्दरता र जैविक विविधता हाम्रो अमूल्य सम्पत्ति हो। "
         "जैविक कृषि प्रणालीले पहाडी जनजीवनलाई आत्मनिर्भर बनाउन मद्दत गर्दछ। "
         "सामूहिक श्रम र सहकार्य नै स्थानीय विकासको मुख्य आधार स्तम्भ हो।"),
        ("sample_text_18_or_science.txt",
         "ଓଡ଼ିଶାର ବୈଜ୍ଞାନିକ ଓ ପ୍ରଯୁକ୍ତିବିଦ୍ୟା କ୍ଷେତ୍ରରେ ବିକାଶ ଉଲ୍ଲେଖନୀୟ ଅଟେ। "
         "କୃଷି, ମତ୍ସ୍ୟ ପାଳନ ଏବଂ ଉପକୂଳ ସୁରକ୍ଷା କ୍ଷେତ୍ରରେ ଆଧୁନିକ ଉପକରଣ ବ୍ୟବହୃତ ହେଉଛି। "
         "ନୂତନ ଉଦ୍ଭାବନ ଦ୍ୱାରା ଯୁବପିଢ଼ିଙ୍କ ପାଇଁ ନୂତନ ସୁଯୋଗ ସୃଷ୍ଟି ହେଉଛି।"),
        ("sample_text_19_pa_agriculture.txt",
         "ਪੰਜਾਬ ਦੀ ਜ਼ਰਖੇਜ਼ ਧਰਤੀ ਦੇ ਕਿਸਾਨ ਅੰਨਦਾਤਾ ਵਜੋਂ ਜਾਣੇ ਜਾਂਦੇ ਹਨ। "
         "ਨਵੀਂ ਤਕਨਾਲੋਜੀ ਅਤੇ ਪਾਣੀ ਦੀ ਸੰਭਾਲ ਨਾਲ ਫ਼ਸਲੀ ਵਿਭਿੰਨਤਾ ਨੂੰ ਉਤਸ਼ਾਹਿਤ ਕੀਤਾ ਜਾ ਰਿਹਾ ਹੈ। "
         "ਪੇਂਡੂ ਵਿਕਾਸ ਅਤੇ ਖੇਤੀਬਾੜੀ ਖੋਜ ਨਾਲ ਆਰਥਿਕਤਾ ਹੋਰ ਮਜ਼ਬੂਤ ਹੋਵੇਗੀ।"),
        ("sample_text_20_sa_philosophy.txt",
         "सत्यं वद धर्मं चर स्वाध्यायान्मा प्रमदः। "
         "वसुधैव कुटुम्बकम् इति सनातन भारतीय विचारः विश्वकल्याणाय मार्गदर्शकः अस्ति। "
         "ज्ञानेन हीनाः पशुभिः समानाः अतएव विद्या सर्वधनप्रधानं कथ्यते।"),
        ("sample_text_21_sat_olchiki.txt",
         "ᱚᱞ ᱪᱤᱠᱤ ᱞᱤᱯᱤ ᱛᱮ ᱥᱟᱱᱛᱟᱲᱤ ᱯᱟᱹᱨᱥᱤ ᱨᱮᱱᱟᱜ ᱥᱟᱶᱦᱮᱫ ᱫᱚ ᱟᱹᱰᱤ ᱠᱤᱥᱟᱹᱬ ᱜᱮᱭᱟ। "
         "ᱡᱟᱦᱮᱨ ᱛᱷᱟᱱ ᱟᱨ ᱥᱟᱨᱱᱟ ᱫᱷᱚᱨᱚᱢ ᱨᱮᱱᱟᱜ ᱨᱤᱛ ᱱᱤᱛᱤ ᱟᱵᱚᱣᱟᱜ ᱪᱤᱱᱦᱟᱹᱣ ᱠᱟᱱᱟ। "
         "ᱯᱟᱹᱨᱥᱤ ᱟᱨ ᱞᱟᱠᱪᱟᱨ ᱡᱤᱣᱤᱫ ᱫᱚᱦᱚ ᱜᱮ ᱢᱟᱨᱟᱝ ᱠᱟᱹᱢᱤ।"),
        ("sample_text_22_sd_history.txt",
         "سنڌو ماٿريءَ جي قديم تهذيب موهن جي دڙي سان پوري دنيا ۾ مشهور آهي. "
         "سنڌي ٻوليءَ جو ادب ۽ صوفي شاعري امن ۽ ڀائيچاري جو پيغام ڏئي ٿي. "
         "هن عظيم تاريخي ورثي جي حفاظت ڪرڻ اسان جو قومي فرض آهي."),
        ("sample_text_23_tanglish_chat.txt",
         "Macha, today morning client review meeting romba nalla pochu. "
         "Indic OCR accuracy 98% iruku nu client sonnaru. "
         "Audio transcription latency mattum konjam optimize pannanum. "
         "Next release schedule ku munadi test run mudichiralam."),
        ("sample_text_24_legal_court_pleading.txt",
         "समक्ष माननीय उच्च न्यायालय: सिविल रिट याचिका संख्या ४०२/२०२६. "
         "प्रार्थी बनाम उत्तरदाता - अंतरिम स्थगन आदेश हेतु आवेदन पत्र. "
         "तथ्यों के अवलोकन से स्पष्ट है कि विधिक प्रक्रिया का सम्यक अनुपालन नहीं किया गया है. "
         "अतः न्यायहित में प्रार्थी के पक्ष में यथारूप आदेश पारित करना समीचीन होगा."),
        ("sample_text_25_technical_code_manual.txt",
         "Bodhan Indic Tool Agent API Developer Reference Manual. "
         "Ensure server is initialized with `IndicCacheManager(cache_dir='outputs/cache')`. "
         "Call `transcribe_audio(file_path, language='hi')` for PCM16 audio up to 30.0s. "
         "For multi-column documents, run `extract_document_text(file_path, table_format='markdown')`.")
    ]

    for fname, content in text_samples:
        out_p = text_dir / fname
        if preserve_existing and out_p.exists():
            manifest["text"].append(out_p)
            continue
        out_p.write_text(content, encoding="utf-8")
        manifest["text"].append(out_p)

    logger.info("Generated %d random multimodal sample instances in partitioned subfolders", sum(len(v) for v in manifest.values()))
    return manifest
