"""Static configuration, language list, price table and key lookup."""
import os
from dataclasses import dataclass

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

# Languages Indic-Translate / Indic-Speak document (ISO codes). Any of them is accepted by --langs.
LANGUAGES = {
    "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati",
    "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia", "as": "Assamese", "ur": "Urdu",
    "ne": "Nepali", "sa": "Sanskrit", "sd": "Sindhi", "ks": "Kashmiri", "kok": "Konkani", "mai": "Maithili",
    "doi": "Dogri", "mni": "Manipuri", "brx": "Bodo", "sat": "Santali",
}
# Indic-Translate refuses codemix only for these targets (docs); none of the dubbing languages is affected.
NO_CODEMIX = {"en"}

# Hosted prices (INR) from the assignment.
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
# Indic-OCR pricing is not published in the docs; set BODHAN_OCR_PRICE_PER_IMAGE (INR) to have it counted in the report.
PRICE_OCR_PER_IMAGE = float(os.environ.get("BODHAN_OCR_PRICE_PER_IMAGE", "0") or 0)

# Targets from the assignment.
SYNC_TOL_S = 0.300
STRETCH_MIN, STRETCH_MAX = 0.85, 1.2
LUFS_TARGET = -16.0
LENGTH_TOL_S = 0.100

# Back-transcription QA thresholds. Real ASR is not perfect on Indic scripts, so tune these against a few real runs.
QA_CER_FAIL = float(os.environ.get("BODHAN_DUB_CER_FAIL", "0.35"))

# Indic-Speak style values (docs). Optional; omitted = the voice's neutral reading.
STYLES = ["AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories", "disgust",
          "educational lecture", "fear", "happy", "news", "sad", "single person narration audiobook", "surprise"]

# Indic-Speak voices by the language they were recorded in (docs). Any voice can read any language, so a language's
# own pair is the default choice. A second voice of the same gender is used when two speakers share a gender.
VOICES = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]}, "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]}, "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]}, "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]}, "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]}, "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]}, "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]}, "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]}, "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]}, "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]}, "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]}, "ur": {"female": ["Saba"], "male": ["Zaid"]},
}
DEFAULT_VOICES = {"female": "Kavya", "male": "Amit"}


@dataclass
class Keys:
    transcribe: str
    translate: str
    speak: str
    ocr: str = ""        # optional: only needed with --ocr (on-screen text)

    @classmethod
    def from_env(cls) -> "Keys":
        shared = os.environ.get("BODHAN_API_KEY", "")
        return cls(
            transcribe=os.environ.get("BODHAN_TRANSCRIBE_KEY", shared),
            translate=os.environ.get("BODHAN_TRANSLATE_KEY", shared),
            speak=os.environ.get("BODHAN_SPEAK_KEY", shared),
            ocr=os.environ.get("BODHAN_OCR_KEY", shared),
        )

    def missing(self) -> list[str]:
        return [n for n, v in (("BODHAN_TRANSCRIBE_KEY", self.transcribe), ("BODHAN_TRANSLATE_KEY", self.translate),
                               ("BODHAN_SPEAK_KEY", self.speak)) if not v]
