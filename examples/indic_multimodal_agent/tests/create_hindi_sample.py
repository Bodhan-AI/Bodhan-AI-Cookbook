"""Generate a realistic Hindi voice note sample for Vertical Slice end-to-end testing."""

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

sample_text = (
    "नमस्ते दोस्तों। आज की यह महत्वपूर्ण बैठक दोपहर 3 बजे आयोजित की जाएगी। "
    "कृपया सभी सदस्य समय पर जुड़ें। हमारे पास चर्चा के लिए कुल 5 मुख्य बिंदु हैं। धन्यवाद।"
)

samples_dir = os.path.join(os.path.dirname(__file__), "..", "samples")
os.makedirs(samples_dir, exist_ok=True)
audio_path = os.path.join(samples_dir, "hindi_voicenote.mp3")

print(f"Generating realistic Hindi voice note: '{sample_text[:50]}...'")

resp = requests.post(
    "https://api.bodhan.ai/v1/audio/speech",
    headers={"Authorization": f"Bearer {SPEAK_KEY}"},
    json={
        "model": "indic-speak",
        "input": sample_text,
        "voice": "Amit",
        "instructions": json.dumps({"lang": "hi"}),
    },
    timeout=25,
)

if resp.status_code == 200:
    with open(audio_path, "wb") as f:
        f.write(resp.content)
    print(f"Successfully generated sample audio at: {audio_path} ({len(resp.content)} bytes)")
else:
    print(f"Failed to generate sample: {resp.status_code} {resp.text}")

