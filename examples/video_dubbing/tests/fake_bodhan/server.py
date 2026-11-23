"""
Fake Bodhan server for offline testing and smoke runs.
Implemented as an httpx.MockTransport that simulates Indic-Transcribe,
Indic-Translate, Indic-Speak, and fault injection (429, 5xx, timeouts).
"""

from __future__ import annotations

import io
import json
import struct
import wave
from typing import Any, Dict, Optional

import httpx


def generate_synthetic_wav(duration_s: float = 2.0, sample_rate: int = 24000) -> bytes:
    """Generates a small in-memory WAV file for synthetic TTS responses."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        n_samples = int(duration_s * sample_rate)
        # 440 Hz low tone
        frames = bytearray()
        for i in range(n_samples):
            val = int(16000 * (i % 54) / 54)
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)
    return buf.getvalue()


class FakeBodhanTransport(httpx.AsyncBaseTransport):
    """Mocks https://api.bodhan.ai/v1 endpoints offline."""

    def __init__(self, failure_injection: Optional[Dict[str, Any]] = None):
        self.failure_injection = failure_injection or {}
        self.call_counts: Dict[str, int] = {
            "transcribe": 0,
            "translate": 0,
            "speak": 0,
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        # Fault injection checks
        if self.failure_injection.get("always_500"):
            return httpx.Response(
                500, json={"error": {"message": "Injected 500 error", "code": "server_error"}}
            )

        # 1. Transcribe: /v1/audio/transcriptions
        if "/audio/transcriptions" in path:
            self.call_counts["transcribe"] += 1
            if self.failure_injection.get("rate_limit_transcribe"):
                return httpx.Response(
                    429,
                    headers={"Retry-After": "1"},
                    json={
                        "error": {"message": "Rate limit exceeded", "code": "rate_limit_exceeded"}
                    },
                )

            await request.aread()
            content_str = request.content.decode("utf-8", errors="ignore")
            # Detect language in multipart
            req_lang = "en"
            for code in ["hi", "ta", "bn", "te", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur"]:
                if f'name="language"\r\n\r\n{code}' in content_str or f"language={code}" in content_str:
                    req_lang = code
                    break

            mock_texts = {
                "en": "Welcome to the introduction to artificial intelligence.",
                "hi": "आर्टिफिशियल इंटेलिजेंस के परिचय में आपका स्वागत है।",
                "ta": "செயற்கை நுண்ணறிவு அறிமுகத்திற்கு வரவேற்கிறோம்.",
                "bn": "কৃত্রিম বুদ্ধিমত্তার পরিচিতিতে স্বাগতম।",
                "te": "ఆర్టిఫిషియల్ ఇంటెలిజెన్స్ పరిచయానికి స్వాগతం.",
                "mr": "आर्टिफिशियल इंटेलिजेंसच्या परिचयात आपले स्वागत आहे.",
                "gu": "આર્ટિફિશિયલ ઇન્ટેલિજન્સના પરિચયમાં આપનું સ્વાગત છે.",
                "kn": "ಕೃತಕ ಬುದ್ಧಿಮತ್ತೆಯ ಪರಿಚಯಕ್ಕೆ ಸುಸ್ವಾಗತ.",
                "ml": "ആർട്ടിഫിഷ്യൽ ഇന്റലിജൻസിലേക്കുള്ള ആമുഖത്തിലേക്ക് സ്വാগതം.",
                "pa": "ਆਰਟੀਫੀਸ਼ੀਅਲ ਇੰਟੈਲੀਜੈਂਸ ਦੀ ਜਾਣ-ਪਛਾਣ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ।",
                "or": "କୃତ୍ରିମ ବୁଦ୍ଧିମତାର ପରିଚୟକୁ ସ୍ୱାଗତ।",
                "as": "কৃত্ৰিম বুদ্ধিমত্তাৰ পৰিচয়লৈ স্বাগতম।",
                "ur": "مصنوعی ذہانت کے تعارف में خوش آمدید۔",
            }
            resp_text = mock_texts.get(req_lang, mock_texts["en"])

            # Return verbose_json mock
            body = {
                "text": resp_text,
                "language": req_lang,
                "duration": 5.0,
                "segments": [
                    {
                        "id": 0,
                        "start": 0.5,
                        "end": 4.5,
                        "text": resp_text,
                    }
                ],
                "words": [],
                "usage": None,
            }
            return httpx.Response(200, json=body)

        # 2. Translate: /v1/chat/completions
        elif "/chat/completions" in path:
            self.call_counts["translate"] += 1
            body_json = json.loads(request.content.decode("utf-8"))
            model = body_json.get("model", "")

            # Fault injection for translate
            if self.failure_injection.get("translate_500"):
                return httpx.Response(
                    500, json={"error": {"message": "Injected translate 500", "code": "server_error"}}
                )

            # Check if this is an LLM mock or Bodhan Translate mock
            if model == "indic-translate":
                target_lang = body_json.get("target_language_code", "hi")
                user_msg = body_json["messages"][0]["content"]

                if self.failure_injection.get("wrong_script_translation"):
                    translated = "यह गलत लिपि का पाठ है।"
                else:
                    translated = None

                # Canned Indic translations
                mock_translations = {
                    "hi": "आर्टिफिशियल इंटेलिजेंस के परिचय में आपका स्वागत है।",
                    "ta": "செயற்கை நுண்ணறிவு அறிமுகத்திற்கு வரவேற்கிறோம்.",
                    "bn": "কৃত্রিম বুদ্ধিমত্তার পরিচিতিতে স্বাগতম।",
                    "te": "ఆర్టిఫిషియల్ ఇంటెలిజెన్స్ పరిచయానికి స్వాగతం.",
                    "mr": "आर्टिफिशियल इंटेलिजेंसच्या परिचयात आपले स्वागत आहे.",
                    "gu": "આર્ટિફિશિયલ ઇન્ટેલિજન્સના પરિચયમાં આપનું સ્વાગત છે.",
                    "kn": "ಕೃತಕ ಬುದ್ಧಿಮತ್ತೆಯ ಪರಿಚಯಕ್ಕೆ ಸುಸ್ವಾಗತ.",
                    "ml": "ആർട്ടിഫിഷ്യൽ ഇന്റലിജൻസിലേക്കുള്ള ആമുഖത്തിലേക്ക് സ്വാഗതം.",
                    "pa": "ਆਰਟੀਫੀਸ਼ੀਅਲ ਇੰਟੈਲੀਜੈਂਸ ਦੀ ਜਾਣ-ਪਛਾਣ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ।",
                    "or": "କୃତ୍ରିମ ବୁଦ୍ଧିମତାର ପରିଚୟକୁ ସ୍ୱାଗତ।",
                    "as": "কৃত্ৰিম বুদ্ধিমত্তাৰ পৰিচয়লৈ স্বাগতম।",
                    "ur": "مصنوعی ذہانت کے تعارف میں خوش آمدید۔",
                    "en": "Welcome to the introduction to artificial intelligence.",
                }
                if translated is None:
                    translated = mock_translations.get(
                        target_lang, f"[{target_lang} translated]: {user_msg}"
                    )

                resp_data = {
                    "id": f"chatcmpl-mock-{self.call_counts['translate']}",
                    "object": "chat.completion",
                    "created": 1740000000,
                    "model": "indic-translate",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": translated},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 15,
                        "completion_tokens": 20,
                        "total_tokens": 35,
                    },
                }
                return httpx.Response(200, json=resp_data)

            # Intelligent fallback for LLM chat completions
            prompt_str = str(body_json)
            if "retry_planner" in prompt_str.lower() or "chosen_action" in prompt_str.lower() or "retry policy" in prompt_str.lower():
                content = json.dumps({"action": "split_text", "reason": "Mock planner selected split_text"})
            elif "terms" in prompt_str.lower() and "terminology" in prompt_str.lower():
                content = json.dumps({"terms": ["artificial intelligence", "AI"]})
            elif "verdict" in prompt_str.lower():
                content = json.dumps({"verdict": "ok", "evidence": "Verified accurate by mock"})
            elif "condensed_en" in prompt_str:
                content = json.dumps({"condensed_en": "Welcome to AI."})
            elif "paraphrased_en" in prompt_str or "paraphrase" in prompt_str:
                content = json.dumps({"paraphrase": "Welcome to artificial intelligence.", "paraphrased_en": "Welcome to artificial intelligence."})
            elif "merge_indices" in prompt_str:
                content = json.dumps({"merge_indices": []})
            else:
                content = "{}"

            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                },
            )

        # 3. Speak: /v1/audio/speech
        elif "/audio/speech" in path:
            self.call_counts["speak"] += 1
            body_json = json.loads(request.content.decode("utf-8"))
            instructions = body_json.get("instructions", "{}")
            try:
                inst_dict = json.loads(instructions) if isinstance(instructions, str) else instructions
                lang = inst_dict.get("lang", "")
            except Exception:
                lang = ""

            if self.failure_injection.get("speak_500") or (
                lang and lang in self.failure_injection.get("speak_500_langs", [])
            ):
                return httpx.Response(
                    500,
                    json={"error": {"message": f"Injected speak 500 for {lang}", "code": "server_error"}},
                )

            text = body_json.get("input", "")
            # Estimate audio duration roughly from char count
            duration_s = max(1.0, len(text) / 12.0)
            wav_bytes = generate_synthetic_wav(duration_s=duration_s, sample_rate=24000)
            return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=wav_bytes)

        return httpx.Response(404, json={"error": "Not Found"})
