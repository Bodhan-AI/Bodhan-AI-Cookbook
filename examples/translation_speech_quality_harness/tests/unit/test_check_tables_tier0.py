"""Must-catch / must-not-flag tables for the tier-0 checks ``script`` and ``format`` (TESTING.md §2).

Tier-0 checks are deterministic proof, so they must flag on their own: a must-catch row needs a fused
flag of the right category (on the right text where a span applies); a must-not-flag row needs no flag
and nothing in the review queue.
"""

from __future__ import annotations

import pytest

from bodhan_eval.checks.deterministic import FormatCheck, ScriptCheck

# ---------------------------------------------------------------------------
# script / language
# ---------------------------------------------------------------------------

SCRIPT_CATCH = [
    pytest.param("hi", "Water boils at 100 degrees at sea level.", "Water boils at 100 degrees at sea level.",
                 "Water boils", id="whole-text-english"),
    pytest.param("hi", "Water boils at 100 °C. It turns into steam when heated further.",
                 "पानी 100 °C पर उबलता है। It turns into steam when heated further.",
                 "It turns into steam", id="one-sentence-english"),
    pytest.param("hi", "Water boils at 100 degrees.", "தண்ணீர் 100 டிகிரியில் கொதிக்கிறது.",
                 "தண்ணீர்", id="tamil-text-for-hindi"),
    pytest.param("hi", "Water boils at one hundred degrees at sea level. It is lower at height and it is not always the same.",
                 "पाणी शंभर अंशांवर उकळते आणि त्याची वाफ होते. हे तापमान समुद्रसपाटीवर आहे. उंचावर ते कमी आहे आणि ते नेहमी सारखे नाही.",
                 "पाणी", id="marathi-text-for-hindi"),
    pytest.param("as", "Water boils at 100 degrees and makes steam. Heat is needed for this.",
                 "জল সমুদ্রপৃষ্ঠে ১০০ ডিগ্রিতে ফোটে এবং বাষ্প তৈরি করে। এর জন্য তাপ লাগে।",
                 "জল", id="bengali-text-for-assamese"),
    pytest.param("hi", "The Ganga is 2,525 km long. It is the longest river in India.",
                 "The Ganga is 2,525 km long. यह भारत की सबसे लंबी नदी है।",
                 "The Ganga is 2,525 km long", id="short-english-sentence-with-name-and-number"),
    pytest.param("hi", "Water boils at 100 degrees. It is the boiling point of water at sea level.",
                 "पानी 100 डिग्री पर उबलता है। (It is the boiling point of water at sea level.)",
                 "It is the boiling point", id="english-sentence-in-brackets-not-after-a-term"),
]  # fmt: skip

SCRIPT_CLEAN = [
    pytest.param("hi", "Isaac Newton studied light. DNA carries genetic information. H2O is water.",
                 "आइज़क न्यूटन ने प्रकाश का अध्ययन किया। DNA आनुवंशिक जानकारी ले जाता है। H2O पानी है।", id="kept-acronyms-formula"),
    pytest.param("hi", "Isaac Newton studied the nature of light.",
                 "Isaac Newton ने प्रकाश की प्रकृति का अध्ययन किया।", id="name-kept-in-latin"),
    pytest.param("hi", "Use $E = mc^2$ and run `print(x)`; see https://example.org/energy for details.",
                 "$E = mc^2$ का उपयोग करें और `print(x)` चलाएँ; विवरण के लिए https://example.org/energy देखें।",
                 id="latex-code-url"),
    pytest.param("hi", "This software is free to use.", "यह software उपयोग के लिए मुफ़्त है।", id="single-kept-technical-word"),
    pytest.param("hi", "This mobile phone runs very fast on a 5G network and its battery lasts the whole day.",
                 "यह mobile phone 5 G network पर बहुत तेज़ी से चलता है और इसकी बैटरी पूरे दिन चलती है।",
                 id="english-words-around-a-number-without-function-words"),
    pytest.param("hi", "Isaac Newton wrote Principia Mathematica in 1687.",
                 "Isaac Newton ने 1687 में Principia Mathematica लिखी।", id="kept-names-and-a-year"),
    pytest.param("ta", "The sun rises in the east.", "சூரியன் கிழக்கில் உதிக்கிறது.", id="correct-tamil"),
    pytest.param("bn", "Water boils at 100 degrees and makes steam.", "জল ১০০ ডিগ্রিতে ফোটে এবং বাষ্প তৈরি করে।", id="correct-bengali"),
    pytest.param("mr", "Water boils at 100 degrees and it is hot.", "पाणी 100 अंशांवर उकळते आणि ते गरम आहे.", id="correct-marathi"),
    pytest.param("as", "Water boils and makes steam.", "পানী উতলে আৰু ভাপ কৰে।", id="correct-assamese"),
    pytest.param("ta", "Newton's laws of motion are three physical laws.",
                 "நியூட்டனின் இயக்க விதிகள் (Newton's laws of motion) மூன்று இயற்பியல் விதிகளாகும்.",
                 id="english-gloss-in-brackets-after-the-term"),  # seen live (ta)
]  # fmt: skip


@pytest.mark.parametrize(("lang", "source", "translation", "flagged_text"), SCRIPT_CATCH)
def test_script_must_catch(make_ctx, run_one, fuse_item, lang, source, translation, flagged_text) -> None:
    r = run_one(ScriptCheck, make_ctx(), source, translation, lang=lang)
    flags, _ = fuse_item(r)
    hits = [f for f in flags if f.category == "wrong_language"]
    assert hits, [s.evidence for s in r.signals]
    assert any(flagged_text in (f.text or "") for f in hits)


@pytest.mark.parametrize(("lang", "source", "translation"), SCRIPT_CLEAN)
def test_script_must_not_flag(make_ctx, run_one, fuse_item, lang, source, translation) -> None:
    r = run_one(ScriptCheck, make_ctx(), source, translation, lang=lang)
    assert r.checks["script"]["status"] == "ok"
    assert fuse_item(r) == ([], []), [s.evidence for s in r.signals]


# ---------------------------------------------------------------------------
# format: Markdown, tables, LaTeX, code, links
# ---------------------------------------------------------------------------

TABLE_SRC = "| A | B |\n|---|---|\n| 1 | 2 |"

FORMAT_CATCH = [
    pytest.param(TABLE_SRC, "| क | ख |\n|---|---|\n| 1  2 |", "| 1  2 |", id="table-row-lost-a-pipe"),
    pytest.param(TABLE_SRC, "| क | ख |\n| 1 | 2 |", None, id="separator-row-missing"),
    pytest.param(
        "# Water\nWater boils at 100 °C.", "पानी\nपानी 100 °C पर उबलता है।", "पानी", id="heading-marker-dropped"
    ),
    pytest.param("Speed is $v = d/t$ here.", "गति $v = d/t यहाँ है।", "$v = d/t यहाँ है।", id="stray-dollar"),
    pytest.param("Speed: $v = d/t$", "गति: $v = d \\times t$", "$v = d \\times t$", id="formula-changed"),
    pytest.param("Ratio: $\\frac{a}{b}$", "अनुपात: $\\frac{a}{b$", "$\\frac{a}{b$", id="unbalanced-braces"),
    pytest.param("This is **important** text.", "यह **महत्वपूर्ण पाठ है।", "**", id="unmatched-bold"),
    pytest.param(
        "See [docs](https://example.org/a).", "[दस्तावेज़](https://example.org/b) देखें।", None, id="link-url-altered"
    ),
]

FORMAT_CLEAN = [
    pytest.param("| Quantity | Unit |\n|---|---|\n| Speed | m/s |", "| राशि | इकाई |\n|---|---|\n| गति | m/s |",
                 id="translated-table-headers"),
    pytest.param("Speed: $v = \\text{distance}/\\text{time}$", "गति: $v = \\text{दूरी}/\\text{समय}$", id="translated-text-command"),
    pytest.param("Set $x = 25$ here.", "यहाँ $x = 52$ रखें।", id="digits-only-formula-change-owned-by-numbers"),
    pytest.param("Water boils at 100 degrees.", "पानी 100 डिग्री पर उबलता है।", id="plain-prose"),
    pytest.param("## Steps\n\n1. Boil water.\n2. Add salt.\n\n- Use **clean** water.",
                 "## चरण\n\n1. पानी उबालें।\n2. नमक डालें।\n\n- **साफ़** पानी का उपयोग करें।", id="lists-headings-bold"),
    pytest.param("Run `make check`, then see [the docs](https://example.org/d).",
                 "`make check` चलाएँ, फिर [दस्तावेज़](https://example.org/d) देखें।", id="code-and-link-kept"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "translation", "flagged_text"), FORMAT_CATCH)
def test_format_must_catch(make_ctx, run_one, fuse_item, source, translation, flagged_text) -> None:
    r = run_one(FormatCheck, make_ctx(), source, translation)
    flags, _ = fuse_item(r)
    hits = [f for f in flags if f.category == "format"]
    assert hits, [s.evidence for s in r.signals]
    if flagged_text is not None:
        assert any(flagged_text in (f.text or "") for f in hits), [f.text for f in hits]


@pytest.mark.parametrize(("source", "translation"), FORMAT_CLEAN)
def test_format_must_not_flag(make_ctx, run_one, fuse_item, source, translation) -> None:
    r = run_one(FormatCheck, make_ctx(), source, translation)
    assert r.checks["format"]["status"] in ("ok", "not_applicable")
    assert fuse_item(r) == ([], []), [s.evidence for s in r.signals]
