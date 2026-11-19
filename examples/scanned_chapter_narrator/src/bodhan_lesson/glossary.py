"""Math and science bilingual glossary and technical terminology manager."""

from __future__ import annotations

import re

# Core bilingual technical glossaries for NCERT Math & Science
CORE_GLOSSARIES: dict[str, dict[str, str]] = {
    # English -> Hindi
    "hi": {
        "line": "रेखा",
        "lines": "रेखाएँ",
        "angle": "कोण",
        "angles": "कोण",
        "point": "बिंदु",
        "points": "बिंदु",
        "ray": "किरण",
        "rays": "किरणें",
        "line segment": "रेखाखंड",
        "acute angle": "न्यून कोण",
        "right angle": "समकोण",
        "obtuse angle": "अधिक कोण",
        "straight angle": "ऋजु कोण",
        "reflex angle": "प्रतिवर्ती कोण",
        "complementary angles": "पूरक कोण",
        "supplementary angles": "संपूरक कोण",
        "adjacent angles": "आसन्न कोण",
        "linear pair": "रैखिक युग्म",
        "vertically opposite angles": "शीर्षाभिमुख कोण",
        "intersecting lines": "प्रतिच्छेदी रेखाएँ",
        "non-intersecting lines": "अप्रतिच्छेदी रेखाएँ",
        "parallel lines": "समांतर रेखाएँ",
        "transversal": "तिर्यक रेखा",
        "alternate interior angles": "एकांतर अंतः कोण",
        "alternate exterior angles": "एकांतर बाह्य कोण",
        "consecutive interior angles": "क्रमागत अंतः कोण",
        "corresponding angles": "संगत कोण",
        "axiom": "अभिगृहीत",
        "theorem": "प्रमेय",
        "proof": "उपपत्ति",
        "collinear": "संरेखी",
        "non-collinear": "असंरेखी",
        "degree": "अंश",
        "triangle": "त्रिभुज",
        "quadrilateral": "चतुर्भुज",
        "vertex": "शीर्ष",
        "arm": "भुजा",
        "equation": "समीकरण",
        "figure": "आकृति",
        "example": "उदाहरण",
    },
    # English -> Tamil
    "ta": {
        "line": "கோடு",
        "lines": "கோடுகள்",
        "angle": "கோணம்",
        "angles": "கோணங்கள்",
        "point": "புள்ளி",
        "points": "புள்ளிகள்",
        "ray": "கதிர்",
        "rays": "கதிர்கள்",
        "line segment": "கோட்டுத்துண்டு",
        "acute angle": "குறுங்கோணம்",
        "right angle": "செங்கோணம்",
        "obtuse angle": "விரிகோணம்",
        "straight angle": "நேர்கோணம்",
        "reflex angle": "பின்வளை கோணம்",
        "complementary angles": "நிரப்புக் கோணங்கள்",
        "supplementary angles": "மிகைநிரப்புக் கோணங்கள்",
        "adjacent angles": "அடுத்துள்ள கோணங்கள்",
        "linear pair": "நேரிய இணைக்கோணங்கள்",
        "vertically opposite angles": "குத்தெதிர்க் கோணங்கள்",
        "intersecting lines": "வெட்டும் கோடுகள்",
        "non-intersecting lines": "வெட்டா கோடுகள்",
        "parallel lines": "இணைக்கோடுகள்",
        "transversal": "குறுக்குவெட்டி",
        "alternate interior angles": "ஒன்றுவிட்ட உள்க்கோணங்கள்",
        "alternate exterior angles": "ஒன்றுவிட்ட வெளிக்கோணங்கள்",
        "corresponding angles": "ஒத்த கோணங்கள்",
        "axiom": "வெளிப்படை உண்மை",
        "theorem": "தேற்றம்",
        "proof": "நிரூபணம்",
        "collinear": "ஒரே நேர்கோட்டமை",
        "degree": "பாகை",
        "triangle": "முக்கோணம்",
        "figure": "படம்",
        "example": "எடுத்துக்காட்டு",
    },
    # English -> Bengali
    "bn": {
        "line": "রেখা",
        "lines": "রেখাসমূহ",
        "angle": "কোণ",
        "angles": "কোণসমূহ",
        "point": "বিন্দু",
        "ray": "রশ্মি",
        "line segment": "রেখাংশ",
        "acute angle": "সূক্ষ্মকোণ",
        "right angle": "সমকোণ",
        "obtuse angle": "স্থূলকোণ",
        "straight angle": "সরলকোণ",
        "reflex angle": "প্রবৃদ্ধ কোণ",
        "complementary angles": "পূরক কোণ",
        "supplementary angles": "সম্পূরক কোণ",
        "adjacent angles": "সন্নিহিত কোণ",
        "linear pair": "রৈখিক যুগল কোণ",
        "vertically opposite angles": "বিপ্রতীপ কোণ",
        "parallel lines": "সমান্তরাল রেখা",
        "transversal": "ছেদক",
        "alternate interior angles": "একান্তর অন্তঃস্থ কোণ",
        "corresponding angles": "অনুরূপ কোণ",
        "axiom": "স্বতঃসিদ্ধ",
        "theorem": "উপপাদ্য",
        "proof": "প্রমাণ",
        "collinear": "সমরেখ",
        "triangle": "ত্রিভুজ",
        "figure": "চিত্র",
        "example": "উদাহরণ",
    },
    # English -> Telugu
    "te": {
        "line": "సరళరేఖ",
        "angle": "కోణం",
        "point": "బిందువు",
        "ray": "కిరణం",
        "line segment": "రేఖాఖండం",
        "acute angle": "లఘుకోణం",
        "right angle": "లంబకోణం",
        "obtuse angle": "గురుకోణం",
        "straight angle": "సరళకోణం",
        "complementary angles": "పూరక కోణాలు",
        "supplementary angles": "సంపూరక కోణాలు",
        "adjacent angles": "ఆసన్న కోణాలు",
        "parallel lines": "సమాంతర రేఖలు",
        "transversal": "తిర్యగ్రేఖ",
        "theorem": "సిద్ధాంతం",
        "proof": "ఉపపత్తి",
        "triangle": "త్రిభుజం",
        "figure": "పటం",
    },
}


def get_glossary_for_lang(target_lang: str) -> dict[str, str]:
    """Retrieve the core glossary for a target language."""
    return CORE_GLOSSARIES.get(target_lang, {})


def extract_matched_terms(text: str, target_lang: str) -> dict[str, str]:
    """Extract known technical terms present in the source text for the target language."""
    glossary = get_glossary_for_lang(target_lang)
    if not glossary or not text:
        return {}

    matched = {}
    lower_text = text.lower()
    for term, target_term in sorted(glossary.items(), key=lambda x: -len(x[0])):
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, lower_text):
            matched[term] = target_term

    return matched
