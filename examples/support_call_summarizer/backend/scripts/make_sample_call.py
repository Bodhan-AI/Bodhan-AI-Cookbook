"""Create a synthetic two-speaker Hindi support call with Bodhan Text-to-Speech.

The recipe never ships real customer recordings. This script voices a short
scripted dialogue (agent and customer use different voices, so the file is
also useful for trying speaker diarization) and writes it to sample_data/.

    python scripts/make_sample_call.py
"""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import RECIPE_ROOT, get_settings  # noqa: E402

AGENT_VOICE, CUSTOMER_VOICE = "Kavya", "Amit"
PAUSE_SECONDS = 0.8

DIALOGUE = [
    ("agent", "नमस्ते, बोधन ब्रॉडबैंड सपोर्ट में आपका स्वागत है। मैं आपकी क्या मदद कर सकती हूँ?"),
    ("customer", "नमस्ते, मेरा इंटरनेट पिछले दो दिन से बंद है। राउटर पर लाल बत्ती जल रही है।"),
    ("agent", "असुविधा के लिए खेद है। कृपया अपना ग्राहक नंबर बताइए।"),
    ("customer", "मेरा ग्राहक नंबर चार पाँच दो सात एक है।"),
    ("agent", "धन्यवाद। आपके इलाके में केबल की मरम्मत चल रही है। कल शाम तक सेवा शुरू हो जाएगी।"),
    ("customer", "ठीक है, लेकिन क्या मुझे इन दो दिनों का पैसा वापस मिलेगा?"),
    ("agent", "जी हाँ, आपके अगले बिल में दो दिन का क्रेडिट जोड़ दिया जाएगा।"),
    ("customer", "बहुत अच्छा, धन्यवाद।"),
    ("agent", "आपका टिकट नंबर टी के टी एक शून्य चार पाँच दो है। आपका दिन शुभ हो।"),
]


def speak(client: httpx.Client, text: str, voice: str) -> tuple[np.ndarray, int]:
    resp = client.post(
        "/v1/audio/speech",
        json={
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps({"lang": "hi", "style": "Customer Care"}),
        },
    )
    resp.raise_for_status()
    with wave.open(io.BytesIO(resp.content), "rb") as wf:
        rate = wf.getframerate()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return samples, rate


def main() -> int:
    settings = get_settings()
    key = settings.bodhan_speak_api_key or settings.bodhan_api_key
    if not key:
        print("Set BODHAN_SPEAK_API_KEY (a key with indic-speak access) in .env first.", file=sys.stderr)
        return 1
    out = RECIPE_ROOT / "sample_data" / "sample_support_call_hindi.wav"
    parts: list[np.ndarray] = []
    rate = 24_000
    with httpx.Client(
        base_url=settings.bodhan_base_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    ) as client:
        for i, (who, line) in enumerate(DIALOGUE, start=1):
            print(f"  voicing line {i}/{len(DIALOGUE)} ({who})", file=sys.stderr)
            samples, rate = speak(client, line, AGENT_VOICE if who == "agent" else CUSTOMER_VOICE)
            parts += [samples, np.zeros(int(PAUSE_SECONDS * rate), dtype=np.int16)]
    out.parent.mkdir(exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.concatenate(parts).tobytes())
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
