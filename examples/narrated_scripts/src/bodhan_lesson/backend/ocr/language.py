"""Resolve the source language from a bounded Indic-OCR sample."""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from ..config import LANGUAGE_NAMES
from ..inference import ProviderError
from ..models import LanguageDecision, LanguageMethod, LanguageSample, Page
from ..report import AttemptLog
from .inputs import png_bytes, render_page
from .provider import OCRResponse

MAX_SAMPLE_PAGES = 3
MAX_EXCERPT_CHARS = 3000
SCRIPT_CANDIDATES = {
    "LATIN": ("en",),
    "DEVANAGARI": ("brx", "doi", "hi", "kok", "ks", "mai", "mr", "ne", "sa", "sd", "sat"),
    "BENGALI": ("as", "bn", "mni", "sat"),
    "ARABIC": ("ks", "sd", "ur"),
    "GUJARATI": ("gu",),
    "GURMUKHI": ("pa",),
    "KANNADA": ("kn",),
    "MALAYALAM": ("ml", "kok"),
    "MEETEI": ("mni",),
    "OL": ("sat",),
    "ORIYA": ("or", "sat"),
    "TAMIL": ("ta",),
    "TELUGU": ("te",),
}
OCR_MARKUP = re.compile(r"<[!/A-Za-z][^>]*>|https?://[^\s)]+|\\[A-Za-z]+")

LanguageClassifier = Callable[[tuple[str, ...], tuple[tuple[str, str], ...]], str]


@dataclass(frozen=True)
class PageEvidence:
    page_id: str
    markdown: str | None
    failure: str | None = None


def validate_override(override: str) -> str:
    code = override.strip().lower()
    if code not in LANGUAGE_NAMES:
        raise ValueError(f"Unsupported --source-lang {override!r}. "
                         f"Choose from {', '.join(LANGUAGE_NAMES)}.")
    return code


def resolve_language(
    override: str | None, evidence: tuple[PageEvidence, ...],
    classify: LanguageClassifier | None = None,
) -> LanguageDecision:
    if override is not None:
        return LanguageDecision(validate_override(override), "override", (),
                                "Source language supplied with --source-lang.")

    counts: Counter[str] = Counter()
    samples: list[LanguageSample] = []
    excerpts: list[tuple[str, str]] = []
    for page in evidence:
        if page.markdown is None:
            samples.append(LanguageSample(page.page_id, "failed", {}, page.failure))
            continue
        text = OCR_MARKUP.sub(" ", page.markdown).strip()
        scripts = Counter(
            unicodedata.name(char, "OTHER").split()[0] for char in text if char.isalpha()
        )
        if not scripts:
            samples.append(LanguageSample(page.page_id, "blank", {}))
            continue
        counts.update(scripts)
        samples.append(LanguageSample(page.page_id, "readable", dict(scripts)))
        excerpts.append((page.page_id, text[:MAX_EXCERPT_CHARS]))

    def decision(code: str | None, method: LanguageMethod, reason: str) -> LanguageDecision:
        return LanguageDecision(code, method, tuple(sample.page_id for sample in samples),
                                reason, samples=tuple(samples))

    if not counts:
        return decision(None, "unresolved", "Sample pages yielded no readable OCR letters.")

    dominant, letter_count = counts.most_common(1)[0]
    # A clear majority keeps small amounts of diagram lettering or footer text from
    # broadening the candidate set. Mixed pages otherwise keep every supported script.
    has_majority = letter_count / counts.total() >= 0.75
    scripts = (dominant,) if has_majority else tuple(counts)
    candidates = tuple(sorted({code for script in scripts
                               for code in SCRIPT_CANDIDATES.get(script, ())}))
    if not candidates:
        return decision(None, "unresolved", "OCR evidence uses an unsupported script.")

    if has_majority and letter_count >= 8 and dominant != "LATIN" and len(candidates) == 1:
        return decision(candidates[0], "script", f"Dominant script: {dominant.title()}.")

    if classify is None:
        return decision(None, "unresolved", "Script evidence requires a language classifier.")
    try:
        label = classify(candidates, tuple(excerpts)).strip().lower()
    except ProviderError as error:
        return decision(None, "unresolved", str(error))
    if label == "unknown":
        return decision(None, "unresolved", "Classifier found insufficient language evidence.")
    if label not in candidates or label not in LANGUAGE_NAMES:
        return decision(None, "unresolved", "Classifier returned a label outside the candidates.")
    return decision(label, "llm", "Classifier selected a supported code from the OCR evidence.")


def sample_language(
    pages: tuple[Page, ...],
    ocr: Callable[[str, bytes], OCRResponse], classify: LanguageClassifier, attempts: AttemptLog,
) -> LanguageDecision:
    first_attempt = len(attempts.attempts)
    evidence: list[PageEvidence] = []
    readable_count = 0
    for page in pages:
        try:
            with render_page(page) as image:
                with ImageOps.exif_transpose(image) as oriented:
                    encoded = png_bytes(oriented)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, pdfium.PdfiumError):
            evidence.append(PageEvidence(page.page_id, None, "Could not render the page image."))
            continue
        try:
            result = ocr(page.page_id, encoded)
        except ProviderError as error:
            evidence.append(PageEvidence(page.page_id, None, str(error)))
            continue
        evidence.append(PageEvidence(page.page_id, result.markdown))
        if any(char.isalpha() for char in OCR_MARKUP.sub(" ", result.markdown)):
            readable_count += 1
            if readable_count == MAX_SAMPLE_PAGES:
                break
    result = resolve_language(None, tuple(evidence), classify)
    return replace(result, attempts=tuple(attempts.attempts[first_attempt:]))
