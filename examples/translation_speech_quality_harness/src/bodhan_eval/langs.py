"""Language registry: script, voices and small fingerprints for every Indic-Speak language.

Everything language-specific lives here as data, so supporting a new language is a table
row, not a code change. Script names are Unicode script names understood by the `regex` module.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lang:
    code: str
    name: str
    script: str  # Unicode script name, e.g. "Devanagari"
    voices: tuple[str, ...]  # first one is the default
    # Only used to tell apart languages that share a script: very frequent function words, and
    # characters that this language uses but its script-mates do not (Assamese ৰ ৱ, Marathi ळ).
    fingerprint: frozenset[str] = field(default_factory=frozenset)
    markers: frozenset[str] = field(default_factory=frozenset)
    suffixes: tuple[str, ...] = ()  # word endings typical of this language and not of its script-mates


def _fp(words: str) -> frozenset[str]:
    return frozenset(words.split())


LANGS: dict[str, Lang] = {
    lang.code: lang
    for lang in [
        Lang("as", "Assamese", "Bengali", ("Prastuti", "Ankur"), _fp("আৰু কৰে কৰা আছে হৈছে তেওঁ তেওঁলোক কৰিছে ওপৰত"), frozenset("ৰৱ")),
        Lang("bn", "Bengali", "Bengali", ("Ishita", "Sourav"), _fp("এবং করে করা থেকে আছে হয়েছে তারা জন্য করতে")),
        Lang("brx", "Bodo", "Devanagari", ("Gwrbw", "Sansuma")),
        Lang("doi", "Dogri", "Devanagari", ("Preeti", "Sham")),
        Lang("gu", "Gujarati", "Gujarati", ("Dhara", "Parth")),
        Lang("hi", "Hindi", "Devanagari", ("Kavya", "Suhani", "Amit"), _fp("है हैं का की के में और से को ने पर यह था थी थे भी लिए")),
        Lang("kn", "Kannada", "Kannada", ("Deepika", "Adarsh")),
        Lang("kok", "Konkani", "Devanagari", ("Anjali", "Sandeep")),
        Lang("ks", "Kashmiri", "Arabic", ("Zoon", "Ishfaq")),
        Lang("mai", "Maithili", "Devanagari", ("Vaidehi", "Madhukar")),
        Lang("ml", "Malayalam", "Malayalam", ("Lakshmi", "Kiran")),
        Lang("mni", "Manipuri", "Meetei_Mayek", ("Thoibi", "Chaoba")),
        Lang("mr", "Marathi", "Devanagari", ("Anagha", "Chinmay"), _fp("आहे आहेत आणि च्या ला मध्ये होते होतात काही जेथे तेथे करतात"), frozenset("ळ"),
             ("च्या", "ाचा", "ाची", "ाचे", "ांचा", "ांची", "ांचे", "ीचा", "ीची", "ीचे")),
        Lang("ne", "Nepali", "Devanagari", ("Srijana", "Sagar"), _fp("छ छन् र मा लाई हो हुन्छ भएको गर्न")),
        Lang("or", "Odia", "Oriya", ("Itishree", "Akash")),
        Lang("pa", "Punjabi", "Gurmukhi", ("Kaur", "Manpreet")),
        Lang("sa", "Sanskrit", "Devanagari", ("Bharati", "Aryaman")),
        Lang("sat", "Santali", "Ol_Chiki", ("Phulmani", "Sibu")),
        Lang("sd", "Sindhi", "Devanagari", ("Moomal", "Rano")),
        Lang("ta", "Tamil", "Tamil", ("Anitha", "Arun")),
        Lang("te", "Telugu", "Telugu", ("Sravani", "Vamsi")),
        Lang("ur", "Urdu", "Arabic", ("Saba", "Zaid")),
        Lang("en", "English", "Latin", ("Amit",)),
    ]
}


def get_lang(code: str) -> Lang:
    """Look up a language; unknown codes get a permissive entry instead of crashing the run."""
    code = code.strip()
    if code in LANGS:
        return LANGS[code]
    base = code.split("-")[0]
    if base in LANGS:
        return LANGS[base]
    return Lang(code, code, "Unknown", ())


def parse_langs(arg: str | None) -> list[str]:
    if not arg:
        return []
    return [c.strip() for c in arg.split(",") if c.strip()]
