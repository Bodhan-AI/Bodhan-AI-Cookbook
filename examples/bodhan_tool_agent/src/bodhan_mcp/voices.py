# Voices per language, from the Cookbook's text-to-speech notebook.
VOICES = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]},
    "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]},
    "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
    "en": {"female": ["Kavya"], "male": ["Amit"]},
}

STYLES = [
    "AIR style news", "TV style news", "news", "Customer Care", "advertisements",
    "educational lecture", "children's stories", "single person narration audiobook",
    "happy", "sad", "anger", "fear", "disgust", "surprise",
]


def pick_voice(language: str, gender: str = "female", voice: str | None = None):
    """Returns (voice_name, None) on success or (None, problem_text) on failure."""
    if language not in VOICES:
        return None, f"Language '{language}' is not supported. Supported: {', '.join(sorted(VOICES))}."
    if gender not in ("female", "male"):
        return None, "gender must be 'female' or 'male'."
    allowed = VOICES[language]["female"] + VOICES[language]["male"]
    if voice:
        if voice not in allowed:
            return None, f"Voice '{voice}' does not exist for '{language}'. Choose from: {', '.join(allowed)}."
        return voice, None
    return VOICES[language][gender][0], None