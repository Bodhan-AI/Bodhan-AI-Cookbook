"""Deterministic test fakes: FakeBodhanTransport (httpx.MockTransport), synthetic WAV/PDF/PNG generators, and ScriptedFakeLLM.

Zero non-Bodhan ASR, MT, TTS, or OCR systems are used anywhere here.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import math
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw


def make_sine_wav_bytes(
    duration_s: float = 1.5,
    *,
    sample_rate: int = 24000,
    freq_hz: float = 440.0,
    amplitude: int = 12000,
) -> bytes:
    """Generate a valid PCM16 mono WAV clip with a sine tone."""
    n_frames = max(1, int(sample_rate * duration_s))
    samples = [int(amplitude * math.sin(2.0 * math.pi * freq_hz * i / sample_rate)) for i in range(n_frames)]
    buf = io.BytesIO()
    with contextlib.closing(wave.open(buf, "wb")) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_frames}h", *samples))
    return buf.getvalue()


def make_segmented_wav(
    path: Path,
    segments: list[tuple[str, float]],
    *,
    sample_rate: int = 16000,
) -> Path:
    """Create a PCM16 mono WAV composed of ('tone'|'silence', duration_s) segments."""
    frames: list[int] = []
    for kind, dur in segments:
        n = int(sample_rate * dur)
        if kind == "silence":
            frames.extend([0] * n)
        else:
            frames.extend(int(12000 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)) for i in range(n))
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(wave.open(str(path), "wb")) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(frames)}h", *frames))
    return path


def make_test_image(path: Path, text: str = "Worksheet", size: tuple[int, int] = (640, 360)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), text, fill="black")
    img.save(path, format="PNG")
    return path


def make_test_pdf(path: Path, page_count: int = 3) -> Path:
    """Create a valid multi-page PDF via Pillow (no poppler/reportlab needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    for i in range(page_count):
        img = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), f"Synthetic Page {i + 1}", fill="black")
        pages.append(img)
    pages[0].save(path, format="PDF", save_all=True, append_images=pages[1:])
    return path


# Canned Indic strings returned by FakeBodhanTransport (strictly test fixtures for transport tests).
CANNED_BY_LANG: dict[str, str] = {
    "hi": "यह एक हिंदी परीक्षण वाक्य है।",
    "ta": "இது ஒரு தமிழ் சோதனை வாக்கியம் ஆகும்.",
    "te": "ఇది ఒక తెలుగు పరీక్ష వాక్యం.",
    "bn": "এটি একটি বাংলা পরীক্ষা বাক্য।",
    "mr": "हे एक मराठी चाचणी वाक्य आहे.",
    "en": "This is an English test sentence.",
}


@dataclass
class FakeBodhanState:
    calls: list[dict[str, Any]] = field(default_factory=list)
    transcribe_texts: list[str] = field(default_factory=list)
    transcribe_by_lang: dict[str, str] = field(default_factory=dict)
    ocr_pages: list[str] = field(default_factory=list)
    ocr_502_remaining: int = 0
    translate_truncate_once: bool = False
    fail_status_sequence: list[int] = field(default_factory=list)
    speak_duration_s: float = 1.5
    speak_repeat_loop: bool = False
    translate_map: dict[str, str] = field(default_factory=dict)

    def _next_forced_status(self) -> int | None:
        if self.fail_status_sequence:
            return self.fail_status_sequence.pop(0)
        return None


def make_fake_bodhan_transport(state: FakeBodhanState | None = None) -> tuple[httpx.MockTransport, FakeBodhanState]:
    """Build an httpx.MockTransport simulating all 5 Bodhan endpoints deterministically."""
    st = state if state is not None else FakeBodhanState()
    transcribe_idx = {"n": 0}
    ocr_idx = {"n": 0}
    trunc_used = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        st.calls.append({"method": request.method, "path": path})

        forced = st._next_forced_status()
        if forced is not None:
            headers = {"retry-after": "0.01"} if forced == 429 else {}
            return httpx.Response(
                forced,
                headers=headers,
                json={"error": {"code": f"forced_{forced}", "message": f"Forced HTTP {forced}", "request_id": "req_fake"}},
            )

        if path == "/v1/audio/transcriptions":
            # Parse multipart form lightly for language hint.
            body_text = request.content.decode("latin-1", errors="ignore")
            lang = None
            if 'name="language"' in body_text:
                after = body_text.split('name="language"', 1)[1]
                for ln in after.splitlines():
                    ln_s = ln.strip()
                    if ln_s and not ln_s.startswith("--"):
                        lang = ln_s
                        break
            if st.speak_repeat_loop:
                text = "இது தமிழ் பாடம் இது தமிழ் பாடம் இது தமிழ் பாடம் இது தமிழ் பாடம்"
            elif st.transcribe_texts:
                idx = min(transcribe_idx["n"], len(st.transcribe_texts) - 1)
                text = st.transcribe_texts[idx]
                transcribe_idx["n"] += 1
            elif lang and lang in st.transcribe_by_lang:
                text = st.transcribe_by_lang[lang]
            elif lang and lang in CANNED_BY_LANG:
                text = CANNED_BY_LANG[lang]
            else:
                text = CANNED_BY_LANG["hi"]
            return httpx.Response(200, json={"text": text})

        if path == "/translate":
            body = json.loads(request.content.decode("utf-8"))
            src = body["text"]
            tgt = body["target_language"]
            script = body.get("script", "native")
            if st.translate_truncate_once and not trunc_used["done"]:
                trunc_used["done"] = True
                return httpx.Response(
                    200,
                    json={
                        "translation": src[:20],
                        "target_language": tgt,
                        "script": script,
                        "truncated": True,
                        "usage": {"output_tokens": 20},
                    },
                )
            if tgt in st.translate_map:
                translated = st.translate_map[tgt]
            elif "|" in src and "---" in src:
                # Preserve Markdown table structure + digits while translating text cells into target script.
                marker = CANNED_BY_LANG.get(tgt, "Translated").split()[0]
                out_lines = []
                for ln in src.splitlines():
                    s = ln.strip()
                    if s.startswith("|") and s.endswith("|"):
                        cells = [c.strip() for c in s.strip("|").split("|")]
                        if all(set(c) <= set("-: ") for c in cells if c):
                            out_lines.append(ln)
                        else:
                            new_cells = [
                                c if any(ch.isdigit() for ch in c) else f"{marker} {c}" if tgt == "en" else f"{marker}"
                                for c in cells
                            ]
                            out_lines.append("| " + " | ".join(new_cells) + " |")
                    else:
                        out_lines.append(CANNED_BY_LANG.get(tgt, f"[{tgt}] {ln}"))
                translated = "\n".join(out_lines)
            elif tgt == "en":
                lines_in = [ln.strip() for ln in src.splitlines() if ln.strip()]
                if len(lines_in) > 1:
                    translated = "\n".join(
                        f"English translation of line {i + 1} ({len(ln)} chars): water conservation and agriculture in 2026."
                        for i, ln in enumerate(lines_in)
                    )
                else:
                    translated = f"English translation of segment ({len(src)} chars): the chapter explains water conservation and agriculture in 2026."
            else:
                base = CANNED_BY_LANG.get(tgt, f"[{tgt}]")
                # Keep digits from source so coverage check passes realistically.
                digits = "".join(ch for ch in src if ch.isdigit())
                translated = f"{base} {digits}".strip()
            return httpx.Response(
                200,
                json={
                    "translation": translated,
                    "target_language": tgt,
                    "script": script,
                    "truncated": False,
                    "usage": {"output_tokens": max(10, len(translated) // 2)},
                },
            )

        if path == "/transliterate":
            body = json.loads(request.content.decode("utf-8"))
            lang = body["language"]
            script = body.get("script", "roman")
            out = "namaste bharat" if script == "roman" else CANNED_BY_LANG.get(lang, body["text"])
            return httpx.Response(
                200,
                json={"transliteration": out, "language": lang, "script": script, "truncated": False},
            )

        if path == "/v1/audio/speech":
            body = json.loads(request.content.decode("utf-8"))
            txt = body.get("input", "")
            # Realistic duration proportional to text length (~12 chars/sec) unless overridden.
            dur = max(0.8, min(15.0, len(txt) / 12.0)) if st.speak_duration_s <= 0 else st.speak_duration_s
            wav_bytes = make_sine_wav_bytes(duration_s=dur, sample_rate=24000)
            return httpx.Response(200, content=wav_bytes, headers={"content-type": "audio/wav"})

        if path == "/v1/chat/completions":
            if st.ocr_502_remaining > 0:
                st.ocr_502_remaining -= 1
                return httpx.Response(
                    502,
                    json={"error": {"code": "bad_gateway", "message": "OCR block overflow", "request_id": "req_502"}},
                )
            if st.ocr_pages:
                idx = min(ocr_idx["n"], len(st.ocr_pages) - 1)
                md = st.ocr_pages[idx]
                ocr_idx["n"] += 1
            else:
                md = "# Chapter 1\n\nयह अध्याय जल संरक्षण और खेती के बारे में बताता है। 2026 में 45 गांवों ने भाग लिया।"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": md}}],
                    "blocks": [
                        {"order": 0, "label": "title", "bbox_xyxy": [10, 10, 400, 60], "conf": 0.96, "text": md[:40]},
                        {"order": 1, "label": "paragraph", "bbox_xyxy": [10, 70, 580, 300], "conf": 0.92, "text": md},
                    ],
                },
            )

        return httpx.Response(404, json={"error": {"code": "not_found", "message": f"Unknown path {path}"}})

    return httpx.MockTransport(handler), st


class ScriptedFakeLLM:
    """Deterministic fake LLM implementing the same `complete_json` and `complete_text` interface as LLMClient.

    Records every single message list passed to it so tests can assert the CaMeL invariant!
    """

    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self._responses = list(responses)
        self.recorded_calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        self.recorded_calls.append(
            {
                "role_tag": role_tag,
                "mode": "json",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        if not self._responses:
            raise RuntimeError(f"ScriptedFakeLLM ran out of scripted responses on {role_tag}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, str):
            return json.loads(nxt)
        return nxt

    async def complete_text(
        self,
        *,
        role_tag: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        self.recorded_calls.append(
            {
                "role_tag": role_tag,
                "mode": "text",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        if not self._responses:
            raise RuntimeError(f"ScriptedFakeLLM ran out of scripted responses on {role_tag}")
        nxt = self._responses.pop(0)
        return nxt if isinstance(nxt, str) else json.dumps(nxt)
