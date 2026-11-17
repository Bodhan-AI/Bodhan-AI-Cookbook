"""Clean-item non-flags and true positives for the deterministic translation checks."""

import time

import pytest
from bodhan_eval.calibration import DEFAULT_THRESHOLD
from bodhan_eval.text_checks import entity_findings, format_findings, number_findings, script_findings

DIGIT_ZEROS = {
    "devanagari": 0x966,
    "bengali": 0x9E6,
    "gurmukhi": 0xA66,
    "gujarati": 0xAE6,
    "oriya": 0xB66,
    "tamil": 0xBE6,
    "telugu": 0xC66,
    "kannada": 0xCE6,
    "malayalam": 0xD66,
    "extended_arabic_indic": 0x6F0,
    "arabic_indic": 0x660,
}


def check_spans(findings, source, candidate):
    """Every finding's evidence must be the exact, non-empty text at its span."""
    texts = {"source": source, "reference": source, "translation": candidate}
    for finding in findings:
        text = texts[finding.span.field]
        assert finding.span.start < finding.span.end, finding
        assert text[finding.span.start : finding.span.end] == finding.evidence, finding
        if finding.anchor is not None:
            assert finding.anchor.start < finding.anchor.end <= len(texts[finding.anchor.field])
    return findings


def flags(findings, source, candidate):
    """Findings strong enough to pass the default calibration threshold."""
    return [finding for finding in check_spans(findings, source, candidate) if finding.strength >= DEFAULT_THRESHOLD]


def all_text_findings(source, candidate, lang):
    """Run the four deterministic text checks the workflow runs."""
    return [
        *number_findings(source, candidate, "source"),
        *format_findings(source, candidate),
        *entity_findings(source, candidate, "source"),
        *script_findings(candidate, lang, source),
    ]


# Numbers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("The town has 250,000 people.", "शहर में 2,50,000 लोग हैं।"),
        ("The town has 2,500,000 people.", "শহরে 25,00,000 জন মানুষ আছে।"),
        ("The project cost 2.5 million rupees.", "परियोजना की लागत 25 लाख रुपये थी।"),
        ("India has 1.2 billion people.", "भारत में 120 करोड़ लोग हैं।"),
        ("India has 1.2 billion people.", "இந்தியாவில் 120 கோடி மக்கள் உள்ளனர்."),
        ("About 3,000 birds arrived.", "लगभग 3 हज़ार पक्षी आए।"),
        ("About 3,000 birds arrived.", "लगभग 3 हज\u093c\u093eर पक्षी आए।"),
        ("About 5,000 birds arrived.", "சுமார் 5 ஆயிரம் பறவைகள் வந்தன."),
        ("The fund holds 2.5 million.", "நிதியில் 25 லட்சத்து ரூபாய் உள்ளது."),
        ("The fund holds 2.5 million.", "నిధిలో 25 లక్షల రూపాయలు ఉన్నాయి."),
        ("The fund holds 2.5 million.", "فنڈ میں 25 لاکھ روپے ہیں۔"),
        ("The fund holds 30 million.", "ফান্ডে 3 কোটি টাকা আছে।"),
        ("The fund holds 2 lakh.", "நிதியில் 200,000 உள்ளது."),
        ("Class starts at 08:30.", "कक्षा 8:30 बजे शुरू होती है।"),
        ("COVID-19 spread in 2020.", "கோவிட்-19 2020 இல் பரவியது."),
        ("The G-20 met in 2023.", "జి-20 2023లో సమావేశమైంది."),
        ("Apollo-11 landed.", "अपोलो-11 उतरा।"),
        ("The exam is on 12/05/2024.", "परीक्षा 12-05-2024 को है।"),
    ],
)
def test_equivalent_numbers_are_not_flagged(source, candidate):
    """Grouping, scale words, and date/time spelling differences keep the same value."""
    assert flags(number_findings(source, candidate, "source"), source, candidate) == []


@pytest.mark.parametrize("script", sorted(DIGIT_ZEROS))
def test_native_digits_normalize_in_every_script(script):
    """250 written with any script's decimal digits compares equal to ASCII 250."""
    zero = DIGIT_ZEROS[script]
    native = "".join(chr(zero + int(digit)) for digit in "250")
    assert number_findings("Use 250 g of flour.", f"{native} g आटा लें।", "source") == []
    [changed] = number_findings("Use 250 g of flour.", f"{native[:-1]}{chr(zero + 1)} g आटा लें।", "source")
    assert changed.strength >= 0.85


def test_spelled_out_number_is_never_a_strong_flag():
    """A value spelled out in the candidate ("दो") cannot be verified, so it stays soft."""
    source, candidate = "Add 2 cups of rice and 100 g of salt.", "दो कप चावल और 100 g नमक डालें।"
    findings = check_spans(number_findings(source, candidate, "source"), source, candidate)
    assert findings and all(finding.strength < DEFAULT_THRESHOLD for finding in findings)
    assert all(finding.severity == "minor" for finding in findings)


def test_unknown_scale_spelling_is_soft():
    """A power-of-ten mismatch beside an unrecognized word is likely a scale word, not an error."""
    source, candidate = "The project cost 2.5 million rupees.", "परियोजना की लागत 25 लक्खा रुपये थी।"
    findings = check_spans(number_findings(source, candidate, "source"), source, candidate)
    assert findings and all(finding.strength < DEFAULT_THRESHOLD for finding in findings)


@pytest.mark.parametrize(
    ("source", "candidate", "evidence"),
    [
        ("Heat the water to 100 degrees.", "पानी को 200 डिग्री तक गर्म करें।", "200"),
        ("The project cost 2.5 million rupees.", "परियोजना की लागत 35 लाख रुपये थी।", "35 लाख"),
        ("The town has 250,000 people.", "শহরে 2,60,000 জন মানুষ আছে।", "2,60,000"),
        ("The exam is on 12/05/2024.", "परीक्षा 12/06/2024 को है।", "06"),
    ],
)
def test_changed_numbers_are_flagged_on_the_candidate(source, candidate, evidence):
    """A real digit change is a strong finding located on the translation."""
    [finding] = flags(number_findings(source, candidate, "source"), source, candidate)
    assert finding.span.field == "translation" and finding.evidence == evidence
    assert finding.strength >= 0.85


# Format --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("It costs $5 and $10 per kilogram.", "इसकी कीमत $5 और $10 प्रति किलोग्राम है।"),
        ("Prices range from $5 to $10.", "कीमतें $10 तक, $5 से शुरू होती हैं।"),
        (r"Speed is $v = \text{distance}/\text{time}$.", r"गति $v = \text{दूरी}/\text{समय}$ है।"),
        (r"Use $$E = mc^2 \quad \mathrm{(energy)}$$ here.", r"यहाँ $$E = mc^2 \quad \mathrm{(ऊर्जा)}$$ का उपयोग करें।"),
        (
            "| Material | Mass |\n| --- | --- |\n| Copper | 8 g |\n| Iron | 12 g |",
            "| पदार्थ | द्रव्यमान |\n| --- | --- |\n| तांबा | 8 g |\n| लोहा | 12 g |",
        ),
        (
            "Use **dry** sand and `pip install x`. See [docs](https://example.org/a).",
            "**सूखी** रेत और `pip install x` का उपयोग करें। [दस्तावेज़](https://example.org/a) देखें।",
        ),
        ("## Steps\n\n1. Mix.\n2. Heat.", "## चरण\n\n1. मिलाएँ।\n2. गरम करें।"),
        ("The capital is ____.", "राजधानी ______ है।"),
        ("Use ***very*** dry sand.", "***बहुत*** सूखी रेत का उपयोग करें।"),
    ],
)
def test_clean_format_is_not_flagged(source, candidate):
    """Currency, translated \\text{} arguments, and translated table/markup stay clean."""
    assert flags(format_findings(source, candidate), source, candidate) == []


def test_merged_table_cell_is_flagged_on_the_row():
    """GFM pads short rows, so a merged cell is visible only by counting raw cells."""
    source = "| Material | Mass |\n| --- | --- |\n| Copper | 8 g |\n| Iron | 12 g |"
    candidate = "| पदार्थ | द्रव्यमान |\n| --- | --- |\n| तांबा 8 g |\n| लोहा | 12 g |"
    [finding] = flags(format_findings(source, candidate), source, candidate)
    assert finding.evidence == "| तांबा 8 g |" and finding.check == "markdown"
    emptied = candidate.replace("| तांबा 8 g |", "| तांबा |  |")
    [finding] = flags(format_findings(source, emptied), source, emptied)
    assert finding.evidence == "| तांबा |  |"


def test_broken_bold_and_code_are_flagged_on_the_candidate():
    """A lone delimiter renders literally; the span covers the line holding the stray markup."""
    source = "Use **dry** sand for the base.\n\nKeep it **level** and **firm**."
    candidate = "आधार के लिए **सूखी रेत का उपयोग करें।\n\nइसे **समतल** और **मज़बूत** रखें।"
    [finding] = flags(format_findings(source, candidate), source, candidate)
    assert finding.evidence == "आधार के लिए **सूखी रेत का उपयोग करें।" and finding.span.field == "translation"
    two_stray = "आधार के लिए **सूखी** रेत का उपयोग करें।\n\nइसे **समतल और **मज़बूत** रखें।"
    [finding] = flags(format_findings(source, two_stray), source, two_stray)
    assert finding.evidence == "इसे **समतल और **मज़बूत** रखें।"
    source, candidate = "Run `make test` first.", "पहले `make test चलाएँ।"
    [finding] = flags(format_findings(source, candidate), source, candidate)
    assert "`make test" in finding.evidence


def test_link_syntax_and_url_changes_are_reported_once():
    """Broken link syntax is a format finding; a changed URL is one entity finding on the candidate."""
    source = "See [the docs](https://example.org/a) for details."
    broken = "विवरण के लिए [दस्तावेज़] (https://example.org/a) देखें।"
    findings = flags(all_text_findings(source, broken, "hi"), source, broken)
    assert [(finding.category, finding.evidence) for finding in findings] == [("format", "[दस्तावेज़] (https://example.org/a)")]
    changed = "विवरण के लिए [दस्तावेज़](https://example.org/b) देखें।"
    findings = flags(all_text_findings(source, changed, "hi"), source, changed)
    assert [(finding.category, finding.span.field, finding.evidence) for finding in findings] == [
        ("entity", "translation", "https://example.org/b")
    ]


def test_latex_damage_points_at_the_candidate():
    """A removed delimiter and an altered formula are both located in the translation."""
    source = "Let $v = d/t$ denote speed. Here $d$ is distance."
    broken = "मान लीजिए v = d/t$ गति है। यहाँ $d$ दूरी है।"
    [finding] = flags(format_findings(source, broken), source, broken)
    assert finding.check == "latex" and finding.evidence == "v = d/t$"
    altered = "मान लीजिए $v = d \\cdot t$ गति है। यहाँ $d$ दूरी है।"
    [finding] = flags(format_findings(source, altered), source, altered)
    assert finding.evidence == "$v = d \\cdot t$" and source[finding.anchor.start : finding.anchor.end] == "$v = d/t$"


def test_lost_heading_points_at_the_candidate():
    """A heading broken by a missing space is reported on the translation."""
    source, candidate = "## Steps\n\nMix the sand.", "##चरण\n\nरेत मिलाएँ।"
    [finding] = flags(format_findings(source, candidate), source, candidate)
    assert finding.span.field == "translation"


# Wrong language / script ---------------------------------------------------


@pytest.mark.parametrize(
    ("source", "candidate", "lang"),
    [
        ("Use the Google Maps API to view NASA data.", "NASA डेटा देखने के लिए Google Maps API का उपयोग करें।", "hi"),
        ("The Indian Space Research Organisation launched it.", "इसे Indian Space Research Organisation ने लॉन्च किया।", "hi"),
        ("Machine learning models need data.", "मशीन लर्निंग मॉडल (machine learning models) को डेटा चाहिए।", "hi"),
        ("Run `print the value now` and open https://example.org/a/b/c.", "`print the value now` चलाएँ और https://example.org/a/b/c खोलें।", "hi"),
        ("Water boils at 100 degrees.", "پانی 100 ڈگری پر ابلتا ہے۔", "ur"),
        ("Water boils at 100 degrees.", "জল ১০০ ডিগ্রিতে ফোটে।", "as"),
        ("Water boils at 100 degrees.", "Water boils at 100 degrees.", "en"),
        (r"Energy is $E = \text{mass times speed}$.", r"ऊर्जा $E = \text{mass times speed}$ है।", "hi"),
        ("Run pip install numpy in the terminal.", "टर्मिनल में pip install numpy चलाएँ।", "hi"),
        ('He said "good morning everyone" loudly.', 'उसने ज़ोर से "good morning everyone" कहा।', "hi"),
    ],
)
def test_kept_latin_and_clean_scripts_are_not_flagged(source, candidate, lang):
    """Acronyms, brands, glosses, code, URLs, LaTeX, RTL Urdu, and English targets stay clean."""
    assert flags(script_findings(candidate, lang, source), source, candidate) == []
    assert flags(all_text_findings(source, candidate, lang), source, candidate) == []


def test_untranslated_sentence_in_hindi_paragraph():
    """One English sentence copied from the source is flagged on that sentence only."""
    source = "Water boils at 100 degrees. The boiling point drops at high altitude. This is a fact."
    candidate = "पानी 100 डिग्री पर उबलता है। The boiling point drops at high altitude. यह एक तथ्य है।"
    [finding] = flags(script_findings(candidate, "hi", source), source, candidate)
    assert finding.category == "wrong_language"
    assert finding.evidence == "The boiling point drops at high altitude"
    assert finding.strength >= 0.85


def test_untranslated_table_cell_phrase():
    """An untranslated table cell is flagged on the cell text."""
    source = "| Step | Action |\n| --- | --- |\n| 1 | Stir the mixture slowly |"
    candidate = "| चरण | क्रिया |\n| --- | --- |\n| 1 | Stir the mixture slowly |"
    [finding] = flags(script_findings(candidate, "hi", source), source, candidate)
    assert finding.evidence == "Stir the mixture slowly"


def test_whole_english_copy_and_unverified_latin():
    """A near copy of the source is strong; Latin text with no source to compare stays soft."""
    source = "Mix the sand with water and stir it slowly."
    [copied] = script_findings("Mix the sand with water and stir it gently.", "ta", source)
    assert copied.strength >= 0.9
    [unknown] = script_findings("An untranslated English sentence.", "hi")
    assert unknown.strength < DEFAULT_THRESHOLD


def test_devanagari_output_for_tamil_target():
    """Another Indic script is flagged per sentence, or for the whole passage."""
    candidate = "நீர் 100 டிகிரியில் கொதிக்கிறது. पानी ऊंचाई पर जल्दी उबलता है।"
    [finding] = flags(script_findings(candidate, "ta"), "", candidate)
    assert finding.evidence == "पानी ऊंचाई पर जल्दी उबलता है।" and finding.strength >= 0.85
    whole = "पानी 100 डिग्री पर उबलता है।"
    [finding] = flags(script_findings(whole, "ta"), "", whole)
    assert finding.strength >= 0.9


def test_bengali_script_for_telugu_and_devanagari_for_english():
    """Wrong-script lines are flagged for any target, including English."""
    candidate = "నీరు 100 డిగ్రీల వద్ద మరుగుతుంది.\nজল ১০০ ডিগ্রিতে ফোটে এবং বাষ্প হয়।"
    [finding] = flags(script_findings(candidate, "te"), "", candidate)
    assert finding.evidence.startswith("জল")
    english = "Water boils at 100 degrees.\nपानी ऊंचाई पर जल्दी उबलता है।"
    [finding] = flags(script_findings(english, "en"), "", english)
    assert finding.evidence.startswith("पानी")


# Robustness ----------------------------------------------------------------

TABLE = "| Material | Mass |\n| --- | --- |\n| Copper | 8 g |"
LATEX = "\\begin{equation}\nF = m a \\quad \\text{force}\n\\end{equation}\n\n$$x^2 + y^2 = r^2$$"
URDU = "پانی ۱۰۰ ڈگری پر ابلتا ہے۔ [ربط](https://example.org) **اہم**"


@pytest.mark.parametrize(
    ("source", "candidate", "lang"),
    [
        ("", "", "hi"),
        ("Text.", "", "ta"),
        ("", "कुछ", "hi"),
        (TABLE, TABLE, "en"),
        (TABLE, "| पदार्थ |", "hi"),
        (LATEX, LATEX, "bn"),
        (LATEX, "\\begin{equation}\nF = m", "bn"),
        ("Water boils at 100 degrees.", URDU, "ur"),
        ("Water boils at 100 degrees.", "Water boils.", "en"),
        ("$", "$$", "hi"),
        ("**", "`", "hi"),
        ("[a](", "[a](b", "xx"),
    ],
)
def test_checks_do_not_crash(source, candidate, lang):
    """Edge inputs return well-formed findings rather than raising."""
    check_spans(all_text_findings(source, candidate, lang), source, candidate)


def test_long_passage_is_fast():
    """A ~200k-character Markdown passage runs in linear-ish time."""
    block = (
        "## Section\n\nWater boils at 100 °C and costs $5 per 2,50,000 units. See [docs](https://example.org/a).\n\n"
        "| Material | Mass |\n| --- | --- |\n| Copper | 8 g |\n\n$x^2 + y^2 = r^2$\n\n"
    )
    translated = (
        "## खंड\n\nपानी 100 °C पर उबलता है और 2,50,000 इकाइयों की कीमत $5 है। [दस्तावेज़](https://example.org/a) देखें।\n\n"
        "| पदार्थ | द्रव्यमान |\n| --- | --- |\n| तांबा | 8 g |\n\n$x^2 + y^2 = r^2$\n\n"
    )
    repeat = 200_000 // len(block)
    source, candidate = block * repeat, translated * repeat
    started = time.perf_counter()
    findings = all_text_findings(source, candidate, "hi")
    assert time.perf_counter() - started < 20
    assert flags(findings, source, candidate) == []
