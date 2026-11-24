"""An in-process fake of the Bodhan hosted API (httpx MockTransport) for offline tests.

It is a test double only: the pipeline never falls back to it. TTS returns a tone whose pitch encodes the text and whose
length depends on the text length. STT recognises those tones by pitch (pitch survives time-stretching) and returns the
text, so the real back-transcription loop runs. Doubled audio comes back as the text twice, which is how a "repeat"
failure is simulated. Anything that is not one of our tones is "source speech" and returns `stt_text`.
"""
import io
import json
import zlib

import httpx
import numpy as np
import soundfile as sf

SR = 24000


def freq_for(text: str) -> float:
    return 400.0 + (zlib.crc32(text.encode()) % 1500) * 2.0


def tone(seconds: float, f: float) -> bytes:
    t = np.arange(int(SR * seconds)) / SR
    buf = io.BytesIO()
    sf.write(buf, (0.3 * np.sin(2 * np.pi * f * t)).astype("float32"), SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeBodhan:
    def __init__(self, stt_text=None, chars_per_sec: float = 14.0, repeat_once: set | None = None, truncate_once: set | None = None, fail_langs: set | None = None):
        self.calls = {"stt": 0, "mt": 0, "tts": 0}
        self.stt_text = stt_text or (lambda n: f"this is spoken sentence number {n}.")
        self.cps = chars_per_sec
        self.repeat_once, self.truncate_once = repeat_once or set(), truncate_once or set()
        self.done: set[str] = set()
        self.table: dict[float, tuple[str, float]] = {}   # freq -> (text, nominal seconds)
        self.n_source = 0
        self.fail_langs = fail_langs or set()
        self.mt_log: list[str] = []
        self.tts_log: list[dict] = []     # every speech request body, to check voice/style reach the API
        self.ocr_calls, self.ocr_requests, self.ocr_fail = 0, [], 0     # ocr_fail: answer the next N calls with 502
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle), base_url="https://api.bodhan.ai")

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/translate":
            self.calls["mt"] += 1
            body = json.loads(req.content)
            self.mt_log.append(body["text"])
            if body["target_language"] in self.fail_langs:
                return httpx.Response(400, json={"error": {"message": "unsupported"}})
            return httpx.Response(200, json={"translation": f"[{body['target_language']}] " + body["text"], "truncated": False,
                                             "usage": {"output_tokens": max(1, len(body["text"]) // 3)}})
        if path == "/v1/audio/speech":
            self.calls["tts"] += 1
            body = json.loads(req.content)
            text = body["input"]
            self.tts_log.append({"text": text, "voice": body["voice"], "instr": json.loads(body["instructions"])})
            nominal = max(0.4, len(text) / self.cps)
            f = freq_for(text)
            self.table[f] = (text, nominal)
            dur = nominal
            if text in self.repeat_once and ("r", text) not in self.done:
                self.done.add(("r", text))
                dur = nominal * 2
            if text in self.truncate_once and ("t", text) not in self.done:
                self.done.add(("t", text))
                dur = nominal * 0.35
            return httpx.Response(200, content=tone(dur, f), headers={"content-type": "audio/wav"})
        if path == "/v1/chat/completions":
            return self._ocr(json.loads(req.content))
        if path == "/v1/audio/transcriptions":
            self.calls["stt"] += 1
            return httpx.Response(200, json={"text": self._stt(req.content)})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def _ocr(self, body: dict) -> httpx.Response:
        """Finds bright text on a dark frame. Left half of the picture = one phrase, right half = another."""
        import base64

        from PIL import Image
        self.ocr_calls += 1
        self.ocr_requests.append({"model": body.get("model"), "max_tokens": body.get("max_tokens")})
        if body.get("model") != "indic-ocr":
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if self.ocr_fail > 0:
            self.ocr_fail -= 1
            return httpx.Response(502, json={"detail": "a block exceeded max_tokens"})
        uri = body["messages"][0]["content"][0]["image_url"]["url"]
        im = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("L")
        a = np.asarray(im)
        h, w = a.shape
        blocks = []
        for _name, x0, x1, text in (("L", 0, w // 2, "Revenue grew"), ("R", w // 2, w, "Open source")):
            ys, xs = np.nonzero(a[:, x0:x1] > 200)
            if len(xs) > 150:
                blocks.append({"order": len(blocks), "label": "Text", "type": "Text", "conf": 0.93, "text": text,
                               "bbox_xyxy": [float(xs.min() + x0), float(ys.min()), float(xs.max() + x0), float(ys.max())]})
        if blocks:      # things that must never be translated
            blocks.append({"order": len(blocks), "label": "Page-footer", "type": "PageFooter", "conf": 0.9, "text": "confidential",
                           "bbox_xyxy": [5.0, float(h - 20), 90.0, float(h - 4)]})
            blocks.append({"order": len(blocks) + 1, "label": "Picture", "type": "Picture", "conf": 0.9, "text": "",
                           "bbox_xyxy": [200.0, 5.0, 300.0, 50.0]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "\n".join(b["text"] for b in blocks)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "blocks": blocks})

    def _stt(self, content: bytes) -> str:
        a = content.find(b"RIFF")
        b = content.rfind(b"\r\n--")
        try:
            x, sr = sf.read(io.BytesIO(content[a:b]), dtype="float32")
        except Exception:
            return ""
        if len(x) == 0 or float(np.sqrt(np.mean(x ** 2))) < 0.02:
            return ""  # silence or music: nothing said
        dur = len(x) / sr
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        peak = float(np.fft.rfftfreq(len(x), 1 / sr)[int(spec.argmax())])
        if self.table:
            f = min(self.table, key=lambda k: abs(k - peak))
            if abs(f - peak) < 6:
                text, nominal = self.table[f]
                if dur > 1.6 * nominal / 1.2:      # clip is about twice as long as it should be: spoken twice
                    return f"{text} {text}"
                if dur < 0.6 * nominal / 1.2 * 1.0 and dur < nominal * 0.5:  # cut short: only the first words
                    return " ".join(text.split()[: max(1, len(text.split()) // 3)])
                return text
        self.n_source += 1
        return self.stt_text(self.n_source)
