"""Test auto-detect on Tamil, Telugu, and Bengali audio clips generated via Indic-Speak.
Runs without language parameter in Indic-Transcribe to verify auto-detection reliability.
"""

import os
import json
import requests

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
env_vars = {}
with open(ENV_PATH, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

SPEAK_KEY = env_vars["BODHAN_SPEAK_KEY"]
TRANSCRIBE_KEY = env_vars["BODHAN_TRANSCRIBE_KEY"]

def main():
    test_cases = [
        {
            "lang": "ta",
            "name": "Tamil",
            "text": "வணக்கம் எப்படி இருக்கிறீர்கள்",
        },
        {
            "lang": "te",
            "name": "Telugu",
            "text": "నమస్కారం మీరు ఎలా ఉన్నారు",
        },
        {
            "lang": "bn",
            "name": "Bengali",
            "text": "নমস্কার আপনি কেমন আছেন",
        },
    ]

    results = {}

    for tc in test_cases:
        lang = tc["lang"]
        name = tc["name"]
        text = tc["text"]
        print(f"\n--- Testing {name} ({lang}) ---")

        # 1. Generate speech
        speak_resp = requests.post(
            "https://api.bodhan.ai/v1/audio/speech",
            headers={"Authorization": f"Bearer {SPEAK_KEY}"},
            json={
                "model": "indic-speak",
                "input": text,
                "voice": "Amit",
                "instructions": json.dumps({"lang": lang}),
            },
            timeout=20,
        )
        if speak_resp.status_code != 200:
            print(f"Failed to generate speech for {name}: {speak_resp.status_code} {speak_resp.text}")
            results[lang] = {"speak_status": speak_resp.status_code, "error": speak_resp.text}
            continue

        audio_bytes = speak_resp.content
        audio_path = os.path.join(os.path.dirname(__file__), f"test_{lang}.mp3")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        print(f"Generated {name} audio: {len(audio_bytes)} bytes saved to {audio_path}")

        # 2. Transcribe WITHOUT language param (auto-detect test)
        with open(audio_path, "rb") as af:
            trans_resp_no_lang = requests.post(
                "https://api.bodhan.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TRANSCRIBE_KEY}"},
                files={"file": (f"test_{lang}.mp3", af, "audio/mpeg")},
                data={"model": "indic-transcribe", "vad": True},
                timeout=20,
            )
        no_lang_data = trans_resp_no_lang.json() if trans_resp_no_lang.status_code == 200 else trans_resp_no_lang.text
        print(f"Transcribe WITHOUT lang param: {no_lang_data}")

        # 3. Transcribe WITH explicit language param (for baseline comparison)
        with open(audio_path, "rb") as af:
            trans_resp_with_lang = requests.post(
                "https://api.bodhan.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TRANSCRIBE_KEY}"},
                files={"file": (f"test_{lang}.mp3", af, "audio/mpeg")},
                data={"model": "indic-transcribe", "language": lang, "vad": True},
                timeout=20,
            )
        with_lang_data = trans_resp_with_lang.json() if trans_resp_with_lang.status_code == 200 else trans_resp_with_lang.text
        print(f"Transcribe WITH explicit lang param: {with_lang_data}")

        results[lang] = {
            "expected_text": text,
            "without_lang": no_lang_data,
            "with_lang": with_lang_data,
        }

    print("\n=== SUMMARY OF AUTO-DETECT TESTS ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

