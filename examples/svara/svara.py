"""Svara: Core AAC speech engine powered by Bodhan indic-speak.

Provides low-latency speech synthesis, code-mixed bilingual support,
voice personalization, and robust audio caching for non-verbal individuals.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

DEFAULT_BASE_URL = "https://api.bodhan.ai"

# Reference voice mapping from scripts/bodhan_api_rules.json
INDIC_VOICES: dict[str, dict[str, list[str]]] = {
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
}

SUPPORTED_STYLES = [
    "neutral",
    "happy",
    "sad",
    "anger",
    "fear",
    "surprise",
    "Customer Care",
    "news",
]

MULTILINGUAL_BOARDS: dict[str, list[dict[str, Any]]] = {
    "hi": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "Please help me, emergency hai.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor ko call karo please.", "style": "anger"},
                {"label": "Medicine", "text": "Mujhe medicine chahiye.", "style": "neutral"},
                {"label": "In Pain", "text": "Mujhe bahut pain ho raha hai.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "Mujhe thoda water chahiye, please.", "style": "neutral"},
                {"label": "Food", "text": "Mujhe khana khana hai, bhookh lagi hai.", "style": "neutral"},
                {"label": "Rest", "text": "Main thak gaya hoon, mujhe rest karna hai.", "style": "neutral"},
                {"label": "Washroom", "text": "Mujhe washroom jaana hai.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Namaste", "text": "Namaste, aap kaise hain?", "style": "happy"},
                {"label": "Thank You", "text": "Aapka bahut bahut thank you.", "style": "happy"},
                {"label": "Yes", "text": "Haan, bilkul.", "style": "neutral"},
                {"label": "No", "text": "Nahi, abhi nahi.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "Main aaj bahut happy hoon.", "style": "happy"},
                {"label": "Tired", "text": "Main thoda tired feel kar raha hoon.", "style": "sad"},
                {"label": "Okay", "text": "Sab theek hai, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "Meri baat suniye please.", "style": "neutral"},
            ],
        },
    ],
    "bn": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "দয়া করে আমাকে help করুন, emergency আছে।", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor কে call করুন please।", "style": "anger"},
                {"label": "Medicine", "text": "আমার medicine লাগবে।", "style": "neutral"},
                {"label": "In Pain", "text": "আমার খুব pain হচ্ছে।", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "আমাকে একটু জল দিন, please।", "style": "neutral"},
                {"label": "Food", "text": "আমার খিদে পেয়েছে, খাবার খাব।", "style": "neutral"},
                {"label": "Rest", "text": "আমি ক্লান্ত, একটু rest নেব।", "style": "neutral"},
                {"label": "Washroom", "text": "আমাকে washroom যেতে হবে।", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Nomoshkar", "text": "নমস্কার, আপনি কেমন আছেন?", "style": "happy"},
                {"label": "Thank You", "text": "আপনাকে অনেক অনেক ধন্যবাদ।", "style": "happy"},
                {"label": "Yes", "text": "হ্যাঁ, নিশ্চয়ই।", "style": "neutral"},
                {"label": "No", "text": "না, এখন না।", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "আমি আজকে খুব happy।", "style": "happy"},
                {"label": "Tired", "text": "আমি খুব tired feel করছি।", "style": "sad"},
                {"label": "Okay", "text": "সব ঠিক আছে, don't worry।", "style": "neutral"},
                {"label": "Listen", "text": "আমার কথাটা শুনুন please।", "style": "neutral"},
            ],
        },
    ],
    "mr": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "कृपया मला मदत करा, emergency आहे.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor ला call करा please.", "style": "anger"},
                {"label": "Medicine", "text": "मला medicine हवी आहे.", "style": "neutral"},
                {"label": "In Pain", "text": "मला खूप त्रास आणि pain होतोय.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "मला थोडं पाणी द्या, please.", "style": "neutral"},
                {"label": "Food", "text": "मला भूक लागली आहे, जेवण हवं आहे.", "style": "neutral"},
                {"label": "Rest", "text": "मी खूप थकलोय, मला rest करायची आहे.", "style": "neutral"},
                {"label": "Washroom", "text": "मला washroom ला जायचं आहे.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Namaskar", "text": "नमस्कार, कसे आहात तुम्ही?", "style": "happy"},
                {"label": "Thank You", "text": "तुमचे खूप खूप धन्यवाद.", "style": "happy"},
                {"label": "Yes", "text": "हो, नक्कीच.", "style": "neutral"},
                {"label": "No", "text": "नाही, आता नाही.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "मी आज खूप happy आहे.", "style": "happy"},
                {"label": "Tired", "text": "मला खूप tired वाटतंय.", "style": "sad"},
                {"label": "Okay", "text": "सगळं ठीक आहे, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "माझं बोलणं ऐका please.", "style": "neutral"},
            ],
        },
    ],
    "ta": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "தயவுசெய்து எனக்கு உதவுங்கள், emergency.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor-க்கு call பண்ணுங்க please.", "style": "anger"},
                {"label": "Medicine", "text": "எனக்கு medicine வேண்டும்.", "style": "neutral"},
                {"label": "In Pain", "text": "எனக்கு ரொம்ப வலிக்குது, pain இருக்கு.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "எனக்கு கொஞ்சம் தண்ணீர் வேண்டும், please.", "style": "neutral"},
                {"label": "Food", "text": "எனக்கு பசிக்குது, சாப்பாடு வேண்டும்.", "style": "neutral"},
                {"label": "Rest", "text": "நான் tired-ஆ இருக்கேன், rest எடுக்கணும்.", "style": "neutral"},
                {"label": "Washroom", "text": "நான் washroom போகணும்.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Vanakkam", "text": "வணக்கம், எப்படி இருக்கீங்க?", "style": "happy"},
                {"label": "Thank You", "text": "உங்களுக்கு ரொம்ப நன்றி.", "style": "happy"},
                {"label": "Yes", "text": "ஆம், கண்டிப்பா.", "style": "neutral"},
                {"label": "No", "text": "இல்லை, இப்போ வேண்டாம்.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "நான் இன்னைக்கு ரொம்ப happy-ஆ இருக்கேன்.", "style": "happy"},
                {"label": "Tired", "text": "நான் ரொம்ப tired-ஆ feel பண்றேன்.", "style": "sad"},
                {"label": "Okay", "text": "எல்லாம் சரி, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "நான் சொல்றதை கேளுங்க please.", "style": "neutral"},
            ],
        },
    ],
    "te": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "దయచేసి నాకు సహాయం చేయండి, emergency.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor కి call చేయండి please.", "style": "anger"},
                {"label": "Medicine", "text": "నాకు medicine కావాలి.", "style": "neutral"},
                {"label": "In Pain", "text": "నాకు చాలా pain గా ఉంది.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "నాకు కొంచెం water కావాలి, please.", "style": "neutral"},
                {"label": "Food", "text": "నాకు ఆకలిగా ఉంది, భోజనం కావాలి.", "style": "neutral"},
                {"label": "Rest", "text": "నేను tired అయ్యాను, rest తీసుకోవాలి.", "style": "neutral"},
                {"label": "Washroom", "text": "నేను washroom కి వెళ్ళాలి.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Namaskaram", "text": "నమస్కారం, మీరు ఎలా ఉన్నారు?", "style": "happy"},
                {"label": "Thank You", "text": "మీకు చాలా ధన్యవాదాలు.", "style": "happy"},
                {"label": "Yes", "text": "అవును, ఖచ్చితంగా.", "style": "neutral"},
                {"label": "No", "text": "వద్దు, ఇప్పుడు కాదు.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "నేను ఈరోజు చాలా happy గా ఉన్నాను.", "style": "happy"},
                {"label": "Tired", "text": "నేను tired గా feel అవుతున్నాను.", "style": "sad"},
                {"label": "Okay", "text": "అంతా బాగుంది, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "నా మాట వినండి please.", "style": "neutral"},
            ],
        },
    ],
    "kn": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "ದಯವಿಟ್ಟು ನನಗೆ ಸಹಾಯ ಮಾಡಿ, emergency.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor ಗೆ call ಮಾಡಿ please.", "style": "anger"},
                {"label": "Medicine", "text": "ನನಗೆ medicine ಬೇಕು.", "style": "neutral"},
                {"label": "In Pain", "text": "ನನಗೆ ತುಂಬಾ pain ಆಗ್ತಿದೆ.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "ನನಗೆ ಸ್ವಲ್ಪ ನೀರು ಕೊಡಿ, please.", "style": "neutral"},
                {"label": "Food", "text": "ನನಗೆ ಹಸಿವಾಗ್ತಿದೆ, ಊಟ ಬೇಕು.", "style": "neutral"},
                {"label": "Rest", "text": "ನಾನು tired ಆಗಿದ್ದೇನೆ, rest ಮಾಡಬೇಕು.", "style": "neutral"},
                {"label": "Washroom", "text": "ನಾನು washroom ಗೆ ಹೋಗಬೇಕು.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Namaskara", "text": "ನಮಸ್ಕಾರ, ಹೇಗಿದ್ದೀರಾ?", "style": "happy"},
                {"label": "Thank You", "text": "ನಿಮಗೆ ತುಂಬಾ ಧನ್ಯವಾದಗಳು.", "style": "happy"},
                {"label": "Yes", "text": "ಹೌದು, ಖಂಡಿತ.", "style": "neutral"},
                {"label": "No", "text": "ಇಲ್ಲ, ಈಗ ಬೇಡ.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "ನಾನು ಇವತ್ತು ತುಂಬಾ happy ಆಗಿದ್ದೇನೆ.", "style": "happy"},
                {"label": "Tired", "text": "ನನಗೆ tired ಅನಿಸ್ತಿದೆ.", "style": "sad"},
                {"label": "Okay", "text": "ಎಲ್ಲವೂ ಸರಿಯಾಗಿದೆ, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "ನನ್ನ ಮಾತು ಕೇಳಿ please.", "style": "neutral"},
            ],
        },
    ],
    "gu": [
        {
            "id": "emergency",
            "name": "Emergency & Medical",
            "phrases": [
                {"label": "Need Help", "text": "મહેરબાની કરીને મદદ કરો, emergency છે.", "style": "fear"},
                {"label": "Call Doctor", "text": "Doctor ને call કરો please.", "style": "anger"},
                {"label": "Medicine", "text": "મને medicine જોઈએ છે.", "style": "neutral"},
                {"label": "In Pain", "text": "મને બહુ pain થાય છે.", "style": "sad"},
            ],
        },
        {
            "id": "daily_needs",
            "name": "Daily Needs",
            "phrases": [
                {"label": "Water", "text": "મને થોડું પાણી આપો, please.", "style": "neutral"},
                {"label": "Food", "text": "મને ભૂખ લાગી છે, જમવું છે.", "style": "neutral"},
                {"label": "Rest", "text": "હું થાકી ગયો છું, મને rest કરવો છે.", "style": "neutral"},
                {"label": "Washroom", "text": "મારે washroom જવું છે.", "style": "neutral"},
            ],
        },
        {
            "id": "social",
            "name": "Social & Responses",
            "phrases": [
                {"label": "Namaste", "text": "નમસ્તે, કેમ છો?", "style": "happy"},
                {"label": "Thank You", "text": "તમારો ખૂબ ખૂબ આભાર.", "style": "happy"},
                {"label": "Yes", "text": "હા, ચોક્કસ.", "style": "neutral"},
                {"label": "No", "text": "ના, અત્યારે નહીં.", "style": "neutral"},
            ],
        },
        {
            "id": "feelings",
            "name": "Feelings",
            "phrases": [
                {"label": "Happy", "text": "હું આજે બહુ happy છું.", "style": "happy"},
                {"label": "Tired", "text": "મને tired feel થાય છે.", "style": "sad"},
                {"label": "Okay", "text": "બધું ठीक છે, don't worry.", "style": "neutral"},
                {"label": "Listen", "text": "મારી વાત સાંભળો please.", "style": "neutral"},
            ],
        },
    ],
}

DEFAULT_CATEGORIES = MULTILINGUAL_BOARDS["hi"]

MULTILINGUAL_CONCEPT_TOKENS: dict[str, dict[str, list[str]]] = {
    "hi": {
        "Need & Object": ["पानी (Water)", "खाना (Food)", "दवा (Medicine)", "आराम (Rest)", "Washroom"],
        "Descriptor": ["ठंडा (Cold)", "गरम (Warm)", "थोड़ा (A little)", "जल्दी (Urgent)", "बहुत (Severe)"],
        "Action & Intent": ["चाहिए (Want)", "दर्द (Pain)", "जाना है (Go)", "मदद (Help)", "कृपया (Please)"],
        "Person": ["मैं (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "mr": {
        "Need & Object": ["पाणी (Water)", "जेवण (Food)", "औषध (Medicine)", "विश्रांती (Rest)", "Washroom"],
        "Descriptor": ["गार (Cold)", "गरम (Warm)", "थोडं (A little)", "लवकर (Urgent)", "खूप (Severe)"],
        "Action & Intent": ["हवं आहे (Want)", "त्रास / दुखणे (Pain)", "जायचं आहे (Go)", "मदत (Help)", "कृपया (Please)"],
        "Person": ["मी (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "bn": {
        "Need & Object": ["জল (Water)", "খাবার (Food)", "ওষুধ (Medicine)", "বিশ্রাম (Rest)", "Washroom"],
        "Descriptor": ["ঠান্ডা (Cold)", "গরম (Warm)", "একটু (A little)", "তাড়াতাড়ি (Urgent)", "খুব (Severe)"],
        "Action & Intent": ["লাগবে (Want)", "ব্যথা (Pain)", "যেতে চাই (Go)", "সাহায্য (Help)", "দয়া করে (Please)"],
        "Person": ["আমি (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "ta": {
        "Need & Object": ["தண்ணீர் (Water)", "சாப்பாடு (Food)", "மருந்து (Medicine)", "ஓய்வு (Rest)", "Washroom"],
        "Descriptor": ["ஜில்லுன்னு (Cold)", "சூடான (Warm)", "கொஞ்சம் (A little)", "சீக்கிரம் (Urgent)", "ரொம்ப (Severe)"],
        "Action & Intent": ["வேண்டும் (Want)", "வலி (Pain)", "போகணும் (Go)", "உதவி (Help)", "தயவுசெய்து (Please)"],
        "Person": ["நான் (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "te": {
        "Need & Object": ["నీరు (Water)", "భోజనం (Food)", "మందు (Medicine)", "విశ్రాంతి (Rest)", "Washroom"],
        "Descriptor": ["చల్లని (Cold)", "వేడి (Warm)", "కొంచెం (A little)", "త్వరగా (Urgent)", "చాలా (Severe)"],
        "Action & Intent": ["కావాలి (Want)", "నొప్పి (Pain)", "వెళ్ళాలి (Go)", "సహాయం (Help)", "దయచేసి (Please)"],
        "Person": ["నేను (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "kn": {
        "Need & Object": ["ನೀರು (Water)", "ಊಟ (Food)", "ಔಷಧಿ (Medicine)", "ವಿಶ್ರಾಂತಿ (Rest)", "Washroom"],
        "Descriptor": ["ತಣ್ಣನೆಯ (Cold)", "ಬಿಸಿ (Warm)", "ಸ್ವಲ್ಪ (A little)", "ಬೇಗ (Urgent)", "ತುಂಬಾ (Severe)"],
        "Action & Intent": ["ಬೇಕು (Want)", "ನೋವು (Pain)", "ಹೋಗಬೇಕು (Go)", "ಸಹಾಯ (Help)", "ದಯವಿಟ್ಟು (Please)"],
        "Person": ["ನಾನು (I)", "Doctor", "Caregiver", "Nurse"],
    },
    "gu": {
        "Need & Object": ["પાણી (Water)", "જમવાનું (Food)", "દવા (Medicine)", "આરામ (Rest)", "Washroom"],
        "Descriptor": ["ઠંડુ (Cold)", "ગરમ (Warm)", "થોડું (A little)", "જલ્દી (Urgent)", "બહુ (Severe)"],
        "Action & Intent": ["જોઈએ (Want)", "દુખાવો (Pain)", "જવું છે (Go)", "મદદ (Help)", "મહેરબાની (Please)"],
        "Person": ["હું (I)", "Doctor", "Caregiver", "Nurse"],
    },
}

CONCEPT_TOKENS = MULTILINGUAL_CONCEPT_TOKENS["hi"]


class SvaraClient:
    """Client for Bodhan Indic-Speak TTS, Indic-Transcribe ASR, and assistive reasoning."""

    def __init__(
        self,
        api_key: str | None = None,
        transcribe_key: str | None = None,
        base_url: str | None = None,
        cache_dir: str | Path = "audio_cache",
    ) -> None:
        try:
            from dotenv import find_dotenv, load_dotenv
            load_dotenv(find_dotenv())
        except ImportError:
            pass

        self.api_key = api_key or os.environ.get("BODHAN_API_KEY") or os.environ.get("BODHAN_SPEAK_KEY")
        if not self.api_key:
            raise ValueError(
                "Bodhan API key not found. Set BODHAN_API_KEY in your environment or pass api_key."
            )

        self.transcribe_key = transcribe_key or os.environ.get("BODHAN_TRANSCRIBE_KEY") or self.api_key
        self.groq_key = os.environ.get("GROQ_API_KEY")

        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get_cache_path(self, text: str, voice: str, lang: str, style: str | None) -> Path:
        """Derive a deterministic file path from synthesis parameters."""
        raw_key = f"{lang}:{voice}:{style or 'neutral'}:{text.strip()}"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{lang}_{voice}_{key_hash}.wav"

    def synthesize(
        self,
        text: str,
        voice: str = "Kavya",
        lang: str = "hi",
        style: str | None = None,
        use_cache: bool = True,
        max_retries: int = 3,
    ) -> tuple[bytes, bool, float]:
        """Synthesize speech using Bodhan indic-speak.

        Returns:
            (audio_bytes, is_cached, latency_seconds)
        """
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Input text cannot be empty.")

        cache_path = self._get_cache_path(clean_text, voice, lang, style)

        if use_cache and cache_path.exists():
            return cache_path.read_bytes(), True, 0.0

        instructions: dict[str, str] = {"lang": lang}
        if style and style != "neutral":
            instructions["style"] = style

        payload = {
            "model": "indic-speak",
            "input": clean_text,
            "voice": voice,
            "instructions": json.dumps(instructions),
        }

        last_error = None
        for attempt in range(max_retries):
            t0 = time.perf_counter()
            try:
                resp = requests.post(
                    f"{self.base_url}/v1/audio/speech",
                    headers=self.headers,
                    json=payload,
                    timeout=120,
                )
                dt = time.perf_counter() - t0

                if resp.status_code == 429:
                    backoff = 2 * (attempt + 1)
                    time.sleep(backoff)
                    continue

                if not resp.ok:
                    try:
                        err = resp.json().get("error", {})
                        msg = err.get("message", resp.text)
                        code = err.get("code", resp.status_code)
                        raise RuntimeError(f"Bodhan API error ({code}): {msg}")
                    except (ValueError, KeyError, AttributeError):
                        resp.raise_for_status()

                audio_data = resp.content
                if use_cache:
                    cache_path.write_bytes(audio_data)

                return audio_data, False, dt

            except (requests.RequestException, RuntimeError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                else:
                    raise last_error from None

        raise RuntimeError(f"Speech synthesis failed after {max_retries} retries: {last_error}")

    def prewarm_cache(
        self,
        phrases: list[dict[str, str] | str],
        voice: str = "Kavya",
        lang: str = "hi",
        on_progress: Callable[[int, int, str, bool], None] | None = None,
    ) -> dict[str, Any]:
        """Pre-cache a collection of phrases to ensure zero-latency offline playback."""
        total = len(phrases)
        cached_count = 0
        new_count = 0

        for i, item in enumerate(phrases, 1):
            if isinstance(item, dict):
                text = item["text"]
                style = item.get("style")
            else:
                text = item
                style = None

            _, is_cached, _ = self.synthesize(
                text=text,
                voice=voice,
                lang=lang,
                style=style,
                use_cache=True,
            )

            if is_cached:
                cached_count += 1
            else:
                new_count += 1
                # Small delay to respect API gateway burst limits
                time.sleep(0.5)

            if on_progress:
                on_progress(i, total, text, is_cached)

        return {"total": total, "cached": cached_count, "generated": new_count}

    def transcribe(
        self,
        audio_data: bytes | Path | str,
        lang: str = "hi",
    ) -> tuple[str, float]:
        """Transcribe spoken audio from a caregiver/doctor using Bodhan indic-transcribe.

        Returns:
            (transcript_text, latency_seconds)
        """
        t0 = time.perf_counter()
        url = f"{self.base_url}/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.transcribe_key}"}

        if isinstance(audio_data, (str, Path)):
            with open(audio_data, "rb") as f:
                files = {"file": ("audio.wav", f, "audio/wav")}
                data = {"model": "indic-transcribe", "language": lang}
                resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        else:
            files = {"file": ("audio.wav", audio_data, "audio/wav")}
            data = {"model": "indic-transcribe", "language": lang}
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)

        dt = time.perf_counter() - t0
        if not resp.ok:
            raise RuntimeError(f"Transcription failed ({resp.status_code}): {resp.text}")

        text = resp.json().get("text", "").strip()
        return text, dt

    def suggest_contextual_replies(
        self,
        transcript: str,
        lang: str = "hi",
    ) -> tuple[list[str], float]:
        """Generate smart 1-tap reply options from incoming caregiver speech."""
        t0 = time.perf_counter()

        if self.groq_key and transcript.strip():
            headers = {
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an assistive AAC engine for non-verbal communicators in India. "
                            f"Given what the caregiver or doctor asked, output 3 or 4 short 1-tap reply options in language '{lang}'. "
                            "Output ONLY a raw JSON array of strings, for example: [\"Option 1\", \"Option 2\", \"Option 3\"]."
                        ),
                    },
                    {"role": "user", "content": f"Caregiver said: \"{transcript.strip()}\""},
                ],
                "temperature": 0.2,
            }
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=10,
                )
                if resp.ok:
                    raw = resp.json()["choices"][0]["message"]["content"].strip()
                    # Strip any markdown fences if present
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    options = json.loads(raw)
                    if isinstance(options, list) and options:
                        return [str(opt).strip() for opt in options[:4]], time.perf_counter() - t0
            except Exception:
                pass  # Gracefully fall back to heuristic

        # Heuristic intent matching fallback
        lower = transcript.lower()
        if any(w in lower for w in ["paani", "water", "juice", "peena", "drink"]):
            options = ["Haan, thoda paani please", "Nahi, abhi nahi", "Thanda paani chahiye", "Juice mangwa dijiye"]
        elif any(w in lower for w in ["khana", "food", "bhookh", "lunch", "dinner"]):
            options = ["Haan, khana khana hai", "Thodi der baad", "Bhookh nahi hai", "Thoda fruits chahiye"]
        elif any(w in lower for w in ["dawa", "medicine", "tablet", "injection"]):
            options = ["Haan, le li", "Nahi, abhi do please", "Paani ke saath chahiye", "Bahut kadwi hai"]
        elif any(w in lower for w in ["dard", "pain", "chot", "takleef"]):
            options = ["Bahut severe pain hai", "Thoda relief hai", "Sir mein dard hai", "Doctor ko bulao please"]
        else:
            options = ["Haan, bilkul", "Nahi, abhi nahi", "Theek hai, thank you", "Madad chahiye please"]

        return options, time.perf_counter() - t0

    def expand_telegraphic_tokens(
        self,
        tokens: list[str],
        lang: str = "hi",
    ) -> tuple[str, float]:
        """Expand 2-3 conceptual telegraphic tokens into a complete natural sentence."""
        t0 = time.perf_counter()
        if not tokens:
            return "", 0.0

        clean_tokens = [t.split("(")[0].strip() for t in tokens]
        tokens_str = ", ".join(clean_tokens)

        if self.groq_key:
            headers = {
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "qwen/qwen3.8-27b",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an AAC telegraphic concept expander. Non-verbal motor-impaired users tap 2-3 concept tokens. "
                            f"Expand them into ONE natural, polite Indian-language code-mixed sentence in language '{lang}'. "
                            "Output only the final expanded sentence string without commentary."
                        ),
                    },
                    {"role": "user", "content": f"Tokens: [{tokens_str}]"},
                ],
                "temperature": 0.2,
            }
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=10,
                )
                if resp.ok:
                    sentence = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
                    return sentence, time.perf_counter() - t0
            except Exception:
                pass

        # Heuristic slot grammar fallback
        sentence = f"{' '.join(clean_tokens)}, please."
        return sentence, time.perf_counter() - t0

    @staticmethod
    def get_concept_tokens(lang: str = "hi") -> dict[str, list[str]]:
        """Return categorized tokens for telegraphic intent building in the selected language."""
        return dict(MULTILINGUAL_CONCEPT_TOKENS.get(lang, MULTILINGUAL_CONCEPT_TOKENS["hi"]))

    @staticmethod
    def get_voices(lang: str = "hi") -> list[str]:
        """Return available voices for the specified language."""
        lang_data = INDIC_VOICES.get(lang, INDIC_VOICES["hi"])
        return lang_data.get("female", []) + lang_data.get("male", [])

    @staticmethod
    def get_styles() -> list[str]:
        """Return supported speaking style presets."""
        return list(SUPPORTED_STYLES)

    @staticmethod
    def load_default_phrases(lang: str = "hi", path: str | Path | None = None) -> list[dict[str, Any]]:
        """Load default AAC phrase categories for the specified language."""
        if path is not None:
            p = Path(path)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("categories", [])
        boards = MULTILINGUAL_BOARDS.get(lang, MULTILINGUAL_BOARDS["hi"])
        return [dict(cat) for cat in boards]
