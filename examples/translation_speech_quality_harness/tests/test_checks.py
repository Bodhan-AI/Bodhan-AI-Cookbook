"""Each deterministic check on hand-made cases (no network)."""
import asyncio

import numpy as np

from bodhan_eval.checks import REGISTRY, Context
from bodhan_eval.checks.length import baselines
from bodhan_eval.dataset import Item
from bodhan_eval.langs import get_lang


def run(name, source, translation, lang="hi", bt=None, stats=None, **art):
    ctx = Context(Item(id="t", source=source, lang=lang), get_lang(lang), translation, stats or {})
    if bt:
        ctx.artifacts["backtranslation"] = bt
    ctx.artifacts.update(art)
    chk = REGISTRY[name]
    return asyncio.run(chk.run(ctx)) if chk.applies(ctx) else []


SRC = "Water boils at 100 °C at sea level."
GOOD = "समुद्र तल पर पानी 100 डिग्री सेल्सियस पर उबलता है।"


def test_numbers_clean_with_native_digits():
    assert run("numbers", SRC, "समुद्र तल पर पानी १०० डिग्री सेल्सियस पर उबलता है।", bt="Water boils at 100 degrees Celsius.") == []


def test_numbers_substitution_is_localized():
    bad = GOOD.replace("100", "10")
    sigs = run("numbers", SRC, bad, bt="Water boils at 10 degrees.")
    assert len(sigs) == 1 and sigs[0].category == "number" and sigs[0].severity == "critical"
    s, e = sigs[0].span
    assert bad[s:e] == "10" and SRC[slice(*sigs[0].source_span)] == "100"


def test_numbers_spelled_out_is_only_weak_evidence():
    sigs = run("numbers", "There are 3 schools.", "तीन विद्यालय हैं।", bt="There are three schools.")
    assert sigs and sigs[0].confidence < 0.3


def test_numbers_source_word_written_as_digit_is_fine():
    assert run("numbers", "There are three schools.", "3 विद्यालय हैं।", bt="There are 3 schools.") == []


def test_numbers_unit_change_via_back_translation():
    sigs = run("numbers", "The road is 32 km long.", "सड़क 32 लंबी है।", bt="The road is 32 miles long.")
    assert any("Unit changed" in s.message for s in sigs)


def test_format_table_cell_count():
    src = "| Part | Function |\n|---|---|\n| Root | Absorbs water |"
    tr = "| भाग | कार्य |\n|---|---|\n| जड़  पानी सोखती है |"
    sigs = run("format", src, tr)
    assert sigs and sigs[0].category == "format" and tr[slice(*sigs[0].span)].startswith("| जड़")


def test_format_unbalanced_dollar_and_math_change():
    src = "Area is $A = \\pi r^2$ here."
    assert run("format", src, "क्षेत्रफल $A = \\pi r^2$ है।") == []
    sigs = run("format", src, "क्षेत्रफल $A = \\pi r^2 है।")
    assert sigs and "Unbalanced" in sigs[0].message
    sigs = run("format", src, "क्षेत्रफल $A = \\pi r^3$ है।")
    assert sigs and "LaTeX" in sigs[0].message


def test_script_untranslated_whole_and_sentence():
    sigs = run("script_lang", SRC, SRC)
    assert sigs[0].category == "wrong_language" and sigs[0].severity == "critical"
    tr = GOOD + " Regular exercise keeps the heart strong and healthy."
    sigs = run("script_lang", SRC + " Regular exercise keeps the heart strong and healthy.", tr)
    assert sigs and sigs[0].severity == "major" and tr[slice(*sigs[0].span)].startswith("Regular")


def test_script_other_indic_script():
    sigs = run("script_lang", SRC, "கடல் மட்டத்தில் நீர் 100°C வெப்பநிலையில் கொதிக்கிறது.", lang="hi")
    assert sigs and sigs[0].category == "wrong_language"


def test_script_keeps_latin_names_from_source():
    assert run("script_lang", "ISRO launched Chandrayaan-3 from Sriharikota.", "ISRO ने Chandrayaan-3 को श्रीहरिकोटा से लॉन्च किया।") == []


def test_script_same_script_language_fingerprint():
    marathi = "पाणी समुद्रसपाटीवर १०० अंश सेल्सिअस तापमानाला उकळते आणि ते वाफेत बदलते आहे आणि ते आहे."
    sigs = run("script_lang", SRC, marathi, lang="hi")
    assert any("Marathi" in s.message for s in sigs)


def test_entities_transliterated_name_is_preserved():
    assert run("entities", "ISRO launched Chandrayaan-3 to study the Moon.", "इसरो ने चंद्रमा का अध्ययन करने के लिए चंद्रयान-3 लॉन्च किया।",
               bt="ISRO launched Chandrayaan-3 to study the Moon.") == []


def test_entities_swapped_name_is_suspected():
    sigs = run("entities", "Isaac Newton described the laws of motion.", "गैलीलियो ने गति के नियमों का वर्णन किया।",
               bt="Galileo described the laws of motion.")
    assert sigs and sigs[0].category == "term" and "Newton" in sigs[0].evidence["entity"]


def test_length_uses_dataset_baseline():
    pairs = [("hi", "a" * 100, "b" * 110)] * 10
    stats = {"length": baselines(pairs)}
    assert run("length", "a" * 100, "b" * 108, stats=stats) == []
    sigs = run("length", "a" * 100, "b" * 40, stats=stats)
    assert sigs and sigs[0].category == "omission" and sigs[0].confidence <= 0.6


def test_backtranslation_coverage_flags_missing_sentence():
    src = "Plants make food using sunlight. They release oxygen into the surrounding air."
    sigs = run("backtranslation", src, "पौधे सूर्य के प्रकाश से भोजन बनाते हैं।", bt="Plants make food with sunlight.")
    assert sigs and sigs[0].category == "omission" and src[slice(*sigs[0].source_span)].startswith("They release")


def _voiced(seconds, sr=24000, f0=140.0):
    t = np.arange(int(seconds * sr)) / sr
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (0.3 * env * x / np.max(np.abs(x))).astype(np.float32)


def test_audio_long_internal_silence_and_noise():
    sr = 24000
    x = np.concatenate([_voiced(1.5), np.zeros(int(2.0 * sr), np.float32), _voiced(1.5)])
    sigs = run("audio_signal", SRC, GOOD, audio=(x, sr))
    assert any("silence" in s.message for s in sigs)
    rng = np.random.default_rng(0)
    y = np.concatenate([_voiced(1.5), rng.normal(0, 0.3, int(1.0 * sr)).astype(np.float32), _voiced(1.5)])
    sigs = run("audio_signal", SRC, GOOD, audio=(y, sr))
    assert any("noise" in s.message for s in sigs)
    assert run("audio_signal", SRC, GOOD, audio=(_voiced(4.0), sr)) == []


def test_asr_truncation_repetition_and_clean():
    tr = "समुद्र तल पर पानी उबलता है। यह एक सामान्य प्रक्रिया है। हर कोई इसे जानता है।"
    clean = run("asr_alignment", SRC, tr, asr_text="समुद्र तल पर पानी उबलता है यह एक सामान्य प्रकिया है हर कोई इसे जानता है")
    assert clean == []
    trunc = run("asr_alignment", SRC, tr, asr_text="समुद्र तल पर पानी उबलता है यह एक")
    assert trunc and trunc[0].category == "speech_omission" and trunc[0].evidence["truncation"]
    assert tr[slice(*trunc[0].span)].startswith("सामान्य")
    rep = run("asr_alignment", SRC, tr, asr_text="समुद्र तल पर पानी उबलता है यह एक सामान्य प्रक्रिया है यह एक सामान्य प्रक्रिया है हर कोई इसे जानता है")
    assert rep and rep[0].category == "speech_repetition"


def test_asr_numbers_spoken_as_words_are_not_errors():
    tr = "पानी 100 डिग्री पर उबलता है।"
    assert run("asr_alignment", SRC, tr, asr_text="पानी हंड्रेड डिग्री पर उबलता है") == []
