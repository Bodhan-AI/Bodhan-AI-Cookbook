"""Everyday practice phrases (native script + English meaning) and voices.

Phrases are short on purpose: one sentence fits the ~30 s audio limit.
Voices come from scripts/bodhan_api_rules.json in the cookbook repo.
"""

LANGUAGES = {"hi": "Hindi", "ta": "Tamil", "bn": "Bengali"}

VOICES = {
    "hi": {"Female": "Kavya", "Male": "Amit"},
    "ta": {"Female": "Anitha", "Male": "Arun"},
    "bn": {"Female": "Ishita", "Male": "Sourav"},
}

# (native-script phrase, English meaning)
PHRASES = {
    "hi": [
        ("आप कैसे हैं?", "How are you?"),
        ("मेरा नाम राहुल है।", "My name is Rahul."),
        ("मुझे एक गिलास पानी चाहिए।", "I need a glass of water."),
        ("यह कितने का है?", "How much is this?"),
        ("स्टेशन कहाँ है?", "Where is the station?"),
    ],
    "ta": [
        ("நீங்கள் எப்படி இருக்கிறீர்கள்?", "How are you?"),
        ("எனக்கு ஒரு டம்ளர் தண்ணீர் வேண்டும்.", "I need a glass of water."),
        ("இது எவ்வளவு?", "How much is this?"),
        ("பேருந்து நிலையம் எங்கே இருக்கிறது?", "Where is the bus stand?"),
        ("மிக்க நன்றி.", "Thank you very much."),
    ],
    "bn": [
        ("আপনি কেমন আছেন?", "How are you?"),
        ("আমার এক গ্লাস জল দরকার।", "I need a glass of water."),
        ("এটার দাম কত?", "How much is this?"),
        ("স্টেশন কোথায়?", "Where is the station?"),
        ("আপনাকে অনেক ধন্যবাদ।", "Thank you very much."),
    ],
}
