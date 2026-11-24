"""Step 0 Probe Script: Live calls to all 4 Bodhan endpoints to verify exact shapes and limits.
Reads API keys from .env.
"""

import os
import json
import base64
import io
import requests
from PIL import Image, ImageDraw, ImageFont

# Find .env in project root
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
env_vars = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

TRANSCRIBE_KEY = env_vars.get("BODHAN_TRANSCRIBE_KEY") or os.environ.get("BODHAN_TRANSCRIBE_KEY")
TRANSLATE_KEY = env_vars.get("BODHAN_TRANSLATE_KEY") or os.environ.get("BODHAN_TRANSLATE_KEY")
SPEAK_KEY = env_vars.get("BODHAN_SPEAK_KEY") or os.environ.get("BODHAN_SPEAK_KEY")
OCR_KEY = env_vars.get("BODHAN_OCR_KEY") or os.environ.get("BODHAN_OCR_KEY")

results = {}

print("=== STEP 0: PROBING BODHAN ENDPOINTS ===")

# --- 0. PROBE /v1/models (General OpenAI-compatible discovery) ---
print("\n--- 0. Checking /v1/models ---")
try:
    resp = requests.get(
        "https://api.bodhan.ai/v1/models",
        headers={"Authorization": f"Bearer {TRANSLATE_KEY}"},
        timeout=10,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        results["models"] = resp.json()
        print(f"Models response: {json.dumps(resp.json(), indent=2)}")
    else:
        results["models"] = {"status": resp.status_code, "text": resp.text}
        print(f"Models response text: {resp.text}")
except Exception as e:
    results["models"] = {"error": str(e)}
    print(f"Error checking /v1/models: {e}")

# --- 1. PROBE Indic-Translate ---
print("\n--- 1. Probing Indic-Translate ---")
try:
    payload = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "Good morning, how are you?"}],
        "target_language_code": "hi",
        "target_script": "native",
    }
    resp = requests.post(
        "https://api.bodhan.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {TRANSLATE_KEY}"},
        json=payload,
        timeout=15,
    )
    print(f"Status: {resp.status_code}")
    print(f"Headers: {dict(resp.headers)}")
    translate_json = resp.json()
    results["indic_translate"] = translate_json
    print(f"Response: {json.dumps(translate_json, indent=2, ensure_ascii=False)}")

    # Test with source_language_code parameter
    payload_with_src = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "Hello"}],
        "target_language_code": "hi",
        "source_language_code": "en",
        "target_script": "native",
    }
    resp_src = requests.post(
        "https://api.bodhan.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {TRANSLATE_KEY}"},
        json=payload_with_src,
        timeout=15,
    )
    results["indic_translate_with_source_param"] = {
        "status": resp_src.status_code,
        "response": resp_src.json() if resp_src.status_code == 200 else resp_src.text,
    }
    print(f"Translate with source_language_code status: {resp_src.status_code}")
except Exception as e:
    results["indic_translate"] = {"error": str(e)}
    print(f"Error probing Indic-Translate: {e}")

# --- 2. PROBE Indic-Speak ---
print("\n--- 2. Probing Indic-Speak ---")
audio_bytes = None
try:
    payload = {
        "model": "indic-speak",
        "input": "नमस्ते, यह एक परीक्षण है।",
        "voice": "Amit",
        "instructions": '{"lang": "hi"}',
    }
    resp = requests.post(
        "https://api.bodhan.ai/v1/audio/speech",
        headers={"Authorization": f"Bearer {SPEAK_KEY}"},
        json=payload,
        timeout=20,
    )
    print(f"Status: {resp.status_code}")
    print(f"Content-Type: {resp.headers.get('Content-Type')}")
    print(f"Content-Length: {len(resp.content)} bytes")
    results["indic_speak"] = {
        "status": resp.status_code,
        "content_type": resp.headers.get("Content-Type"),
        "byte_length": len(resp.content),
    }
    if resp.status_code == 200:
        audio_bytes = resp.content
        temp_audio_path = os.path.join(os.path.dirname(__file__), "probe_speech.wav")
        with open(temp_audio_path, "wb") as f:
            f.write(audio_bytes)
        print(f"Saved test speech audio to {temp_audio_path}")
    else:
        print(f"Error body: {resp.text}")
        results["indic_speak"]["error_text"] = resp.text

    # Probe /v1/audio/voices or similar if exists
    try:
        resp_voices = requests.get(
            "https://api.bodhan.ai/v1/audio/voices",
            headers={"Authorization": f"Bearer {SPEAK_KEY}"},
            timeout=10,
        )
        print(f"Voices endpoint status: {resp_voices.status_code}")
        if resp_voices.status_code == 200:
            results["voices"] = resp_voices.json()
            print(f"Voices response: {json.dumps(resp_voices.json(), indent=2)}")
        else:
            results["voices"] = {"status": resp_voices.status_code, "text": resp_voices.text}
    except Exception as e:
        results["voices"] = {"error": str(e)}

except Exception as e:
    results["indic_speak"] = {"error": str(e)}
    print(f"Error probing Indic-Speak: {e}")

# --- 3. PROBE Indic-Transcribe ---
print("\n--- 3. Probing Indic-Transcribe ---")
try:
    temp_audio_path = os.path.join(os.path.dirname(__file__), "probe_speech.wav")
    if os.path.exists(temp_audio_path):
        # Test A: With language="hi"
        with open(temp_audio_path, "rb") as af:
            resp_trans_hi = requests.post(
                "https://api.bodhan.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TRANSCRIBE_KEY}"},
                files={"file": ("probe_speech.wav", af, "audio/wav")},
                data={"model": "indic-transcribe", "language": "hi", "vad": True},
                timeout=20,
            )
        print(f"Transcribe (with language='hi') Status: {resp_trans_hi.status_code}")
        hi_json = resp_trans_hi.json() if resp_trans_hi.status_code == 200 else resp_trans_hi.text
        results["indic_transcribe_with_lang"] = {
            "status": resp_trans_hi.status_code,
            "response": hi_json,
        }
        print(f"Transcribe with lang: {json.dumps(hi_json, indent=2, ensure_ascii=False)}")

        # Test B: WITHOUT language param (Audio language detection test)
        with open(temp_audio_path, "rb") as af:
            resp_trans_nolang = requests.post(
                "https://api.bodhan.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TRANSCRIBE_KEY}"},
                files={"file": ("probe_speech.wav", af, "audio/wav")},
                data={"model": "indic-transcribe", "vad": True},
                timeout=20,
            )
        print(f"Transcribe (WITHOUT language param) Status: {resp_trans_nolang.status_code}")
        nolang_json = resp_trans_nolang.json() if resp_trans_nolang.status_code == 200 else resp_trans_nolang.text
        results["indic_transcribe_without_lang"] = {
            "status": resp_trans_nolang.status_code,
            "response": nolang_json,
        }
        print(f"Transcribe without lang: {json.dumps(nolang_json, indent=2, ensure_ascii=False)}")

        # Test C: verbose_json or timestamps test
        with open(temp_audio_path, "rb") as af:
            resp_trans_verbose = requests.post(
                "https://api.bodhan.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TRANSCRIBE_KEY}"},
                files={"file": ("probe_speech.wav", af, "audio/wav")},
                data={"model": "indic-transcribe", "language": "hi", "response_format": "verbose_json"},
                timeout=20,
            )
        print(f"Transcribe (verbose_json) Status: {resp_trans_verbose.status_code}")
        verbose_json = resp_trans_verbose.json() if resp_trans_verbose.status_code == 200 else resp_trans_verbose.text
        results["indic_transcribe_verbose"] = {
            "status": resp_trans_verbose.status_code,
            "response": verbose_json,
        }
        print(f"Transcribe verbose: {json.dumps(verbose_json, indent=2, ensure_ascii=False)}")
    else:
        print("probe_speech.wav not found, cannot probe transcribe.")
except Exception as e:
    results["indic_transcribe"] = {"error": str(e)}
    print(f"Error probing Indic-Transcribe: {e}")

# --- 4. PROBE Indic-OCR ---
print("\n--- 4. Probing Indic-OCR ---")
try:
    # Generate a small image with clear text
    img = Image.new("RGB", (240, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 30), "नमस्ते 123", fill=(0, 0, 0))
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    b64_img = base64.b64encode(img_byte_arr.getvalue()).decode()

    payload = {
        "model": "indic-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}
                ],
            }
        ],
    }
    resp_ocr = requests.post(
        "https://api.bodhan.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {OCR_KEY}"},
        json=payload,
        timeout=20,
    )
    print(f"OCR Status: {resp_ocr.status_code}")
    ocr_json = resp_ocr.json() if resp_ocr.status_code == 200 else resp_ocr.text
    results["indic_ocr"] = {
        "status": resp_ocr.status_code,
        "response": ocr_json,
    }
    print(f"OCR response: {json.dumps(ocr_json, indent=2, ensure_ascii=False)}")
except Exception as e:
    results["indic_ocr"] = {"error": str(e)}
    print(f"Error probing Indic-OCR: {e}")

# Output summary to JSON file
summary_path = os.path.join(os.path.dirname(__file__), "step0_results.json")
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"\nSaved all results to {summary_path}")

