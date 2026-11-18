"""Automated verification module for Bodhan Task Agent.

Executes code-driven verification checks after each generating tool step:
1. Speech: transcribes generated WAV via indic_transcribe and computes normalized CER.
2. Translation: script presence, length sanity, and back-translation to English via indic_translate.
3. OCR & Transcription: non-emptiness, script matching, and multi-page error auditing.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set

from bodhan_mcp.tools.language import (
    detect_script_language,
    get_script_ratio,
)
from bodhan_mcp.tools.translate import normalize_language


# ------------------------------------------------------------------------------
# Normalization & Error Metrics (CER & Text Similarity)
# ------------------------------------------------------------------------------


def normalize_text_for_eval(text: str) -> str:
    """Normalizes text with Unicode NFC and strips punctuation and whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    # Remove punctuation, symbols, whitespace, and danda/double-danda
    text = re.sub(r"[\s\.,!\?\"'`:;\-_/\(\)\[\]\{\}\<\>@#\$%\^&\*\+=~`|\\।॥]", "", text)
    return text.lower()


def compute_cer(reference: str, hypothesis: str) -> float:
    """Computes Normalized Character Error Rate: Levenshtein(ref, hyp) / max(len(ref), 1)."""
    norm_ref = normalize_text_for_eval(reference)
    norm_hyp = normalize_text_for_eval(hypothesis)

    if not norm_ref and not norm_hyp:
        return 0.0
    if not norm_ref:
        return 1.0
    if not norm_hyp:
        return 1.0

    r_len = len(norm_ref)
    h_len = len(norm_hyp)
    dp = [[0] * (h_len + 1) for _ in range(r_len + 1)]

    for i in range(r_len + 1):
        dp[i][0] = i
    for j in range(h_len + 1):
        dp[0][j] = j

    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            cost = 0 if norm_ref[i - 1] == norm_hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,  # deletion
                dp[i][j - 1] + 1,  # insertion
                dp[i - 1][j - 1] + cost,  # substitution
            )

    dist = dp[r_len][h_len]
    return dist / r_len


def normalize_digits_to_ascii(text: str) -> str:
    """Normalizes all Unicode Indic digits (Devanagari, Bengali, Tamil, etc.) to standard ASCII 0-9."""
    if not text:
        return ""
    result = []
    for ch in text:
        if ch.isdigit():
            try:
                dec = unicodedata.decimal(ch)
                result.append(str(dec))
            except (ValueError, TypeError):
                result.append(ch)
        else:
            result.append(ch)
    return "".join(result)


def check_translation_coverage(source_text: str, translated_text: str) -> Dict[str, Any]:
    """Non-similarity coverage check: segment count and number/digit preservation."""
    norm_source = normalize_digits_to_ascii(source_text)
    norm_target = normalize_digits_to_ascii(translated_text)

    source_numbers = re.findall(r"\d+", norm_source)
    target_numbers = re.findall(r"\d+", norm_target)

    # Number / digit preservation
    digits_preserved = set(source_numbers) == set(target_numbers)

    # Segment count
    source_segs = [s.strip() for s in re.split(r"[.!?।\n]+", source_text) if s.strip()]
    target_segs = [s.strip() for s in re.split(r"[.!?।\n]+", translated_text) if s.strip()]
    n_src = len(source_segs)
    n_tgt = len(target_segs)
    ratio = round(n_tgt / max(n_src, 1), 2)

    passed = digits_preserved and (0.5 <= ratio <= 2.0 or n_src == 0)

    return {
        "digits_preserved": digits_preserved,
        "source_digits": source_numbers,
        "target_digits": target_numbers,
        "source_segment_count": n_src,
        "target_segment_count": n_tgt,
        "segment_ratio": ratio,
        "passed": passed,
    }


def compute_token_overlap(text_a: str, text_b: str) -> float:
    """Computes Jaccard token overlap between two texts."""
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


# ------------------------------------------------------------------------------
# Step-Level Verification Functions
# ------------------------------------------------------------------------------


async def verify_speech_step(
    wav_path: str,
    source_text: str,
    language: str,
    mcp_client: Any,
    budget_manager: Any,
) -> Dict[str, Any]:
    """Transcribes generated speech WAV and verifies intelligibility using normalized CER.

    Thresholds: pass if CER <= 0.20, warn if <= 0.40, else fail.
    """
    if not source_text or not str(source_text).strip():
        return {
            "what": "indic_speak_audio_cer",
            "thresholds": "CER <= 0.20 (pass), <= 0.40 (warn)",
            "score": None,
            "verdict": "completed_unverified",
            "details": "Speech verification skipped: reference text was empty.",
            "cost": 0.0,
        }

    est_transcribe_cost = 0.01  # Conservative estimate for a short clip
    if not budget_manager.can_afford_verification(est_transcribe_cost):
        return {
            "what": "indic_speak_audio_cer",
            "thresholds": "CER <= 0.20 (pass), <= 0.40 (warn)",
            "score": None,
            "verdict": "completed_unverified",
            "details": f"Verification budget cannot cover transcription cost (Rs {est_transcribe_cost:.4f}).",
            "cost": 0.0,
        }

    # Transcribe via MCP client (normalize language to canonical ISO code)
    canon_code, _ = normalize_language(language)
    req_lang = canon_code or (language.strip().lower() if language else "hi")
    t_res, t_err = await mcp_client.call_tool(
        "indic_transcribe",
        {"file_path": wav_path, "language": req_lang},
    )

    if t_err or not t_res or not t_res.get("success"):
        return {
            "what": "indic_speak_audio_cer",
            "thresholds": "CER <= 0.20 (pass), <= 0.40 (warn)",
            "score": None,
            "verdict": "fail",
            "details": f"Verification transcription failed: {t_err or t_res.get('error')}",
            "cost": 0.0,
        }

    transcribed_text = t_res.get("transcript") or t_res.get("text") or ""
    cer = compute_cer(reference=source_text, hypothesis=transcribed_text)
    actual_cost = float(t_res.get("estimated_cost_inr") or 0.0)

    if cer <= 0.20:
        verdict = "pass"
    elif cer <= 0.40:
        verdict = "warn"
    else:
        verdict = "fail"

    return {
        "what": "indic_speak_audio_cer",
        "thresholds": "CER <= 0.20 (pass), <= 0.40 (warn)",
        "score": round(cer, 4),
        "verdict": verdict,
        "details": f"Speech CER: {cer:.2%} (Transcript: '{transcribed_text[:60]}...')",
        "cost": actual_cost,
    }


async def verify_translation_step(
    translated_text: str,
    source_text: Optional[str] = None,
    target_language: str = "hi",
    source_language: Optional[str] = None,
    mcp_client: Any = None,
    budget_manager: Any = None,
    truncated: bool = False,
    source_english_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifies translated text via non-emptiness, truncation, script ratio, and back-translation.

    Direction:
    - Indic to English: translates English back to source Indic language and compares in that language.
    - English to Indic: translates Indic back to English and compares in English.
    Reports both source_string and comparison_string in verification entry.
    """
    actual_source = source_text if source_text is not None else (source_english_text or "")

    # 1. Non-empty check
    if not translated_text or not translated_text.strip():
        return {
            "what": "indic_translate_back_translation",
            "thresholds": "Non-empty, script ratio >= 0.50, similarity >= 0.35",
            "score": 0.0,
            "verdict": "fail",
            "details": "Translation output is empty.",
            "source_string": actual_source,
            "comparison_string": "",
            "cost": 0.0,
        }

    # 2. Truncation check
    if truncated:
        return {
            "what": "indic_translate_back_translation",
            "thresholds": "Non-empty, truncated=False",
            "score": 0.0,
            "verdict": "fail",
            "details": "Translation output was marked truncated.",
            "source_string": actual_source,
            "comparison_string": "",
            "cost": 0.0,
        }

    # 3. Target script check
    norm_target = target_language.lower().strip()
    if norm_target in ("en", "english"):
        norm_target_code = "en"
    else:
        norm_target_code = norm_target

    script_ratio = get_script_ratio(translated_text, norm_target_code)
    if script_ratio < 0.40:
        return {
            "what": "indic_translate_script_presence",
            "thresholds": "Target script ratio >= 0.40",
            "score": round(script_ratio, 4),
            "verdict": "fail",
            "details": f"Script ratio {script_ratio:.2%} does not match expected script for '{target_language}'.",
            "source_string": actual_source,
            "comparison_string": translated_text,
            "cost": 0.0,
        }

    # 4. Length ratio check
    len_ratio = len(translated_text) / max(len(actual_source), 1)
    if len_ratio < 0.20 or len_ratio > 5.0:
        return {
            "what": "indic_translate_length_sanity",
            "thresholds": "0.20 <= length_ratio <= 5.0",
            "score": round(len_ratio, 2),
            "verdict": "warn",
            "details": f"Anomalous length ratio {len_ratio:.2f} relative to source.",
            "source_string": actual_source,
            "comparison_string": translated_text,
            "cost": 0.0,
        }

    # 5. Back-translation check
    est_back_cost = 0.002
    if budget_manager and not budget_manager.can_afford_verification(est_back_cost):
        return {
            "what": "indic_translate_back_translation",
            "thresholds": "Similarity >= 0.35 (pass), >= 0.20 (warn)",
            "score": None,
            "verdict": "completed_unverified",
            "details": f"Verification budget cannot cover back-translation cost (Rs {est_back_cost:.4f}).",
            "source_string": actual_source,
            "comparison_string": "",
            "cost": 0.0,
        }

    is_indic_to_en = norm_target_code == "en"
    if is_indic_to_en:
        # Indic -> English: translate English back to source Indic language
        src_lang_code = (source_language or "").lower().strip()
        if not src_lang_code or src_lang_code == "auto":
            src_lang_code = detect_script_language(actual_source) or "hi"
        bt_args = {
            "text": translated_text,
            "source_language": "en",
            "target_language": src_lang_code,
        }
    else:
        # English -> Indic: translate Indic back to English
        bt_args = {
            "text": translated_text,
            "source_language": target_language,
            "target_language": "en",
        }

    bt_res, bt_err = await mcp_client.call_tool("indic_translate", bt_args)

    if bt_err or not bt_res or not bt_res.get("success"):
        return {
            "what": "indic_translate_back_translation",
            "thresholds": "Similarity >= 0.35 (pass), >= 0.20 (warn)",
            "score": None,
            "verdict": "warn",
            "details": f"Back-translation call failed: {bt_err or bt_res.get('error')}",
            "source_string": actual_source,
            "comparison_string": "",
            "cost": 0.0,
        }

    back_text = bt_res.get("translated_text") or ""
    actual_cost = float(bt_res.get("estimated_cost_inr") or 0.0)

    # Compute segment count and number/digit coverage check
    coverage = check_translation_coverage(actual_source, translated_text)

    # Comparison: both strings are in the same language
    compare_source = actual_source
    compare_back = back_text

    diff_ratio = difflib.SequenceMatcher(None, compare_source.lower(), compare_back.lower()).ratio()
    token_overlap = compute_token_overlap(compare_source, compare_back)
    combined_score = (diff_ratio + token_overlap) / 2.0

    if combined_score >= 0.35:
        verdict = "pass"
    elif combined_score >= 0.20:
        verdict = "warn"
    else:
        verdict = "fail"

    return {
        "what": "indic_translate_back_translation",
        "thresholds": "Similarity >= 0.35 (pass), >= 0.20 (warn)",
        "score": round(combined_score, 4),
        "verdict": verdict,
        "details": f"Back-translation similarity: {combined_score:.2%} (Back: '{back_text[:60]}...')",
        "source_string": compare_source,
        "comparison_string": compare_back,
        "coverage_check": coverage,
        "cost": actual_cost,
    }


def verify_ocr_step(
    ocr_result: Dict[str, Any],
    expected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifies OCR output: non-emptiness, script matching, and multi-page error auditing."""
    full_text = ocr_result.get("full_text") or ocr_result.get("text") or ""
    if not full_text.strip():
        return {
            "what": "indic_ocr_text_integrity",
            "thresholds": "Non-empty output",
            "score": 0.0,
            "verdict": "fail",
            "details": "OCR returned empty text.",
            "cost": 0.0,
        }

    # Check for multi-page partial failures
    pages = ocr_result.get("pages") or []
    failed_pages = [p.get("page_number") for p in pages if not p.get("success", True)]
    if failed_pages:
        return {
            "what": "indic_ocr_multipage_integrity",
            "thresholds": "Zero failed pages",
            "score": len(pages) - len(failed_pages),
            "verdict": "warn",
            "details": f"OCR succeeded partially; failed pages: {failed_pages}.",
            "cost": 0.0,
        }

    # Check script if expected language is given
    if expected_language:
        ratio = get_script_ratio(full_text, expected_language)
        if ratio < 0.40:
            return {
                "what": "indic_ocr_script_matching",
                "thresholds": "Script ratio >= 0.40",
                "score": round(ratio, 4),
                "verdict": "warn",
                "details": f"OCR text script ratio {ratio:.2%} does not match expected language '{expected_language}'.",
                "cost": 0.0,
            }

    return {
        "what": "indic_ocr_integrity",
        "thresholds": "Non-empty and valid script",
        "score": 1.0,
        "verdict": "pass",
        "details": f"OCR text extracted ({len(full_text)} characters).",
        "cost": 0.0,
    }


def verify_transcription_step(
    transcribe_result: Dict[str, Any],
    expected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifies audio transcription output: non-emptiness and script presence."""
    transcript = transcribe_result.get("transcript") or transcribe_result.get("text") or ""
    if not transcript.strip():
        return {
            "what": "indic_transcribe_integrity",
            "thresholds": "Non-empty transcript",
            "score": 0.0,
            "verdict": "fail",
            "details": "Transcribe returned empty text.",
            "cost": 0.0,
        }

    if expected_language:
        ratio = get_script_ratio(transcript, expected_language)
        if ratio < 0.40:
            return {
                "what": "indic_transcribe_script_matching",
                "thresholds": "Script ratio >= 0.40",
                "score": round(ratio, 4),
                "verdict": "warn",
                "details": f"Transcript script ratio {ratio:.2%} does not match expected language '{expected_language}'.",
                "cost": 0.0,
            }

    return {
        "what": "indic_transcribe_integrity",
        "thresholds": "Non-empty and valid script",
        "score": 1.0,
        "verdict": "pass",
        "details": f"Transcript generated ({len(transcript)} characters).",
        "cost": 0.0,
    }


GROUNDING_STOPWORDS: Set[str] = {
    # Short words that might appear capitalized at sentence start
    "the",
    "and",
    "for",
    "are",
    "but",
    "not",
    "you",
    "all",
    "any",
    "can",
    "had",
    "her",
    "was",
    "one",
    "our",
    "out",
    "has",
    "his",
    "how",
    "its",
    "who",
    "did",
    "get",
    "now",
    "way",
    "new",
    "two",
    "it",
    "in",
    "on",
    "at",
    "by",
    "to",
    "of",
    "is",
    "as",
    "an",
    "be",
    "or",
    "if",
    "so",
    "we",
    "he",
    "my",
    "no",
    "up",
    "do",
    # 4+ letter English stopwords
    "that",
    "this",
    "with",
    "from",
    "have",
    "were",
    "been",
    "will",
    "into",
    "over",
    "such",
    "also",
    "than",
    "then",
    "them",
    "they",
    "their",
    "there",
    "these",
    "those",
    "what",
    "when",
    "where",
    "which",
    "while",
    "about",
    "after",
    "before",
    "between",
    "during",
    "under",
    "above",
    "through",
    "because",
    "could",
    "would",
    "should",
    "might",
    "must",
    "shall",
    "being",
    "other",
    "some",
    "many",
    "most",
    "more",
    "each",
    "both",
    "only",
    "very",
    "just",
    "even",
    "still",
    "well",
    "back",
    "much",
    "does",
    "done",
    "doing",
    "having",
    "cannot",
    "here",
    "your",
    "ours",
    "theirs",
    "itself",
    "himself",
    "herself",
    "themselves",
    "however",
    "therefore",
    "furthermore",
    "additionally",
    "within",
    "without",
    "across",
    "upon",
    "onto",
    "below",
    "until",
    "since",
    "though",
    "although",
    "whether",
    "among",
    "along",
    "around",
    "against",
}


def extract_grounding_content_words(text: str) -> Set[str]:
    """Extracts content words from text: numbers, capitalised names, and 4+ letter non-stopwords (lowercased)."""
    if not text:
        return set()
    words: Set[str] = set()
    # All numbers (integers and decimals)
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        words.add(num)
    # Capitalised names (2+ letters starting with uppercase), excluding stopwords
    for cap in re.findall(r"\b[A-Z][a-zA-Z]+\b", text):
        lc = cap.lower()
        if lc not in GROUNDING_STOPWORDS:
            words.add(lc)
    # Words of 4+ letters, excluding stopwords
    for word in re.findall(r"[A-Za-z]{4,}", text):
        lc = word.lower()
        if lc not in GROUNDING_STOPWORDS:
            words.add(lc)
    return words


def verify_summary_grounding(
    candidate_text: str,
    source_texts: List[str],
) -> Dict[str, Any]:
    """Deterministic grounding check (no LLM): fraction of candidate content words present in source text.

    Untuned heuristic thresholds (documented in DECISIONS.md ADR 023):
    - >= 0.50: pass
    - >= 0.30 and < 0.50: warn
    - < 0.30: fail
    """
    content_words = extract_grounding_content_words(candidate_text or "")
    combined_source = "\n".join(s for s in (source_texts or []) if isinstance(s, str))
    source_tokens = set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", combined_source.lower()))

    if not content_words:
        return {
            "what": "summary_grounding",
            "thresholds": "Content-word overlap >= 0.50 (pass), >= 0.30 (warn), < 0.30 (fail)",
            "score": 0.0,
            "verdict": "fail",
            "details": "Summary grounding failed: no content words extracted from input text.",
            "cost": 0.0,
        }

    matched = [w for w in content_words if w in source_tokens]
    score = len(matched) / len(content_words)

    if score >= 0.50:
        verdict = "pass"
    elif score >= 0.30:
        verdict = "warn"
    else:
        verdict = "fail"

    return {
        "what": "summary_grounding",
        "thresholds": "Content-word overlap >= 0.50 (pass), >= 0.30 (warn), < 0.30 (fail)",
        "score": round(score, 4),
        "verdict": verdict,
        "details": f"Summary grounding overlap: {score:.2%} ({len(matched)}/{len(content_words)} content words found in source).",
        "cost": 0.0,
    }


def verify_length_constraint(
    task_text: str,
    candidate_text: str,
) -> Optional[Dict[str, Any]]:
    """Checks target length constraint ('about N characters' or 'N words') within +-30%.

    Report-only: returns 'pass' within +-30% of N, 'warn' otherwise (never 'fail').
    Returns None if task_text specifies no character/word length target.
    """
    if not task_text or not candidate_text:
        return None

    char_match = re.search(
        r"(?:about|around|approximately|~|≈)?\s*(\d+)\s*(?:characters?|chars?)\b",
        task_text,
        re.IGNORECASE,
    )
    word_match = re.search(
        r"(?:about|around|approximately|~|≈)?\s*(\d+)\s*words?\b",
        task_text,
        re.IGNORECASE,
    )

    if char_match:
        target_n = int(char_match.group(1))
        actual = len(candidate_text.strip())
        unit = "characters"
    elif word_match:
        target_n = int(word_match.group(1))
        actual = len(candidate_text.strip().split())
        unit = "words"
    else:
        return None

    if target_n <= 0:
        return None

    low = target_n * 0.70
    high = target_n * 1.30
    ratio = actual / target_n
    verdict = "pass" if (low <= actual <= high) else "warn"

    return {
        "what": "length_constraint",
        "thresholds": f"Within +-30% of {target_n} {unit} ({int(round(low))}-{int(round(high))} {unit}) = pass, else warn",
        "score": round(ratio, 4),
        "verdict": verdict,
        "details": f"Length {actual} {unit} vs target {target_n} {unit} (ratio {ratio:.2f}; {'within' if verdict == 'pass' else 'outside'} +-30%).",
        "cost": 0.0,
    }
