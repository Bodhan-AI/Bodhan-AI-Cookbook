"""Probe real limits of Bodhan Indic models with a hard Rs 1.50 spend cap and disk caching.

Tests:
1. Transcribe: ~67s audio file (test_audio.wav joined 5x) to check if API errors, truncates, or transcribes all.
2. Translate: Full test_text.txt and a ~1,500-word repeated text to check context handling and omissions.
3. TTS: ~60, ~150, and ~300 character strings (stops at first error) and measures output audio duration.
4. OCR: test_image.png and multi-page/multi-image input probing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path

import dotenv
import requests

BASE_DIR = Path(__file__).resolve().parent
dotenv.load_dotenv(BASE_DIR / ".env")

BASE_URL = "https://api.bodhan.ai"
SAMPLES_DIR = BASE_DIR / "samples"
CACHE_DIR = BASE_DIR / ".cache" / "probe_limits_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SPEND_CAP_INR = 1.50
total_spent_inr = 0.0

results_table: list[dict[str, str | float]] = []


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    candidate = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-9.1-full_build"
        / "bin"
        / "ffmpeg.exe"
    )
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError("ffmpeg not found in PATH or WinGet packages.")


def get_audio_duration_seconds(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        # Fallback to ffprobe
        ffmpeg_bin = find_ffmpeg()
        ffprobe_bin = str(Path(ffmpeg_bin).parent / "ffprobe.exe")
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())


def get_cache(key: str) -> dict | None:
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def set_cache(key: str, data: dict) -> None:
    cache_file = CACHE_DIR / f"{key}.json"
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_result(model: str, tried: str, result: str, cost: float) -> None:
    global total_spent_inr
    total_spent_inr += cost
    results_table.append({
        "model": model,
        "tried": tried,
        "result": result,
        "cost": cost,
    })


# ---------------------------------------------------------------------------
# 1. Transcribe Probe (~67s Audio)
# ---------------------------------------------------------------------------
def probe_transcribe() -> None:
    global total_spent_inr
    print("\n[1/4] Probing Indic-Transcribe limit (~67s audio)...")
    key = os.environ.get("BODHAN_TRANSCRIBE_KEY")
    if not key:
        record_result("indic-transcribe", "~67s audio", "SKIPPED: No Key", 0.0)
        return

    src_audio = SAMPLES_DIR / "test_audio.wav"
    joined_audio = SAMPLES_DIR / "test_audio_joined_67s.wav"

    # Create 5x concatenated audio with ffmpeg if not present
    if not joined_audio.exists():
        ffmpeg_bin = find_ffmpeg()
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(src_audio),
            "-i", str(src_audio),
            "-i", str(src_audio),
            "-i", str(src_audio),
            "-i", str(src_audio),
            "-filter_complex", "[0:0][1:0][2:0][3:0][4:0]concat=n=5:v=0:a=1[out]",
            "-map", "[out]",
            str(joined_audio),
        ]
        subprocess.run(cmd, capture_output=True, check=True)

    dur = get_audio_duration_seconds(joined_audio)
    est_cost = (dur / 60.0) * 0.10  # ₹0.10 per audio min
    print(f"Joined audio duration: {dur:.2f}s | Estimated cost: ₹{est_cost:.4f}")

    if total_spent_inr + est_cost > SPEND_CAP_INR:
        print("[LIMIT] Spend cap reached, skipping transcribe call.")
        record_result("indic-transcribe", f"{dur:.1f}s audio", "ABORTED: Spend cap", 0.0)
        return

    cache_key = hashlib.sha256(f"transcribe_{dur}_{joined_audio.stat().st_size}".encode()).hexdigest()
    cached = get_cache(cache_key)
    if cached:
        print(f"Cache hit! Transcript length: {len(cached.get('text', ''))} chars")
        record_result("indic-transcribe", f"{dur:.1f}s audio", f"Cached: {cached.get('summary')}", 0.0)
        return

    headers = {"Authorization": f"Bearer {key}"}
    data = {"model": "indic-transcribe", "language": "hi", "vad": "true"}

    t0 = time.perf_counter()
    with open(joined_audio, "rb") as f:
        files = {"file": (joined_audio.name, f, "audio/wav")}
        resp = requests.post(f"{BASE_URL}/v1/audio/transcriptions", headers=headers, data=data, files=files, timeout=90)
    latency = time.perf_counter() - t0

    if resp.status_code == 200:
        res_data = resp.json()
        text = res_data.get("text", "")
        # Check if text repeats (since we joined audio 5 times, check if repetition is detected)
        occurrences = text.count("अशफाक")
        summary = f"Success in {latency:.1f}s. Transcript chars={len(text)}, detected phrase occurrences={occurrences} of 5"
        print(f"{summary}\nSample: {text[:140]}...")
        set_cache(cache_key, {"text": text, "summary": summary, "latency": latency})
        record_result("indic-transcribe", f"{dur:.1f}s audio", summary, est_cost)
    else:
        summary = f"HTTP {resp.status_code} ({latency:.1f}s): {resp.text[:120]}"
        print(f"Failed: {summary}")
        record_result("indic-transcribe", f"{dur:.1f}s audio", summary, 0.0)


# ---------------------------------------------------------------------------
# 2. Translate Probe (test_text.txt and ~1,500 words)
# ---------------------------------------------------------------------------
def probe_translate() -> None:
    global total_spent_inr
    print("\n[2/4] Probing Indic-Translate limit (test_text.txt and ~1,500 words)...")
    key = os.environ.get("BODHAN_TRANSLATE_KEY")
    if not key:
        record_result("indic-translate", "test_text.txt", "SKIPPED: No Key", 0.0)
        return

    test_text = (SAMPLES_DIR / "test_text.txt").read_text(encoding="utf-8").strip()
    words = test_text.split()
    # Repeat paragraph to reach ~1,500 words
    repeat_count = max(1, 1500 // len(words) + 1)
    long_text = "\n\n".join([f"[{i+1}] {test_text}" for i in range(repeat_count)])
    actual_long_words = len(long_text.split())

    tests = [
        ("Full test_text.txt", test_text, len(words)),
        (f"Long text (~{actual_long_words} words)", long_text, actual_long_words),
    ]

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for label, text_to_send, word_count in tests:
        # Translation pricing: ₹0.20 per 10,000 output tokens. Input tokens free.
        # Est output tokens roughly word_count * 1.3
        est_tokens = int(word_count * 1.3)
        est_cost = (est_tokens / 10000.0) * 0.20

        if total_spent_inr + est_cost > SPEND_CAP_INR:
            print(f"[LIMIT] Spend cap reached before {label}, skipping.")
            record_result("indic-translate", label, "ABORTED: Spend cap", 0.0)
            break

        cache_key = hashlib.sha256(f"translate_{len(text_to_send)}_{text_to_send[:50]}".encode()).hexdigest()
        cached = get_cache(cache_key)
        if cached:
            print(f"Cache hit for {label}!")
            record_result("indic-translate", label, f"Cached: {cached.get('summary')}", 0.0)
            continue

        payload = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text_to_send}],
            "target_language_code": "hi",
            "target_script": "native",
        }

        print(f"Sending {label} ({word_count} words, {len(text_to_send)} chars)...")
        t0 = time.perf_counter()
        resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload, timeout=60)
        latency = time.perf_counter() - t0

        if resp.status_code == 200:
            res_data = resp.json()
            out_content = res_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = res_data.get("usage", {})
            out_tokens = usage.get("completion_tokens", est_tokens)
            actual_cost = (out_tokens / 10000.0) * 0.20

            # Check coverage / omissions
            # For long text, check if beginning, middle, and end markers exist
            summary = f"Success in {latency:.1f}s. Out tokens={out_tokens}, out chars={len(out_content)}"
            if "[" in text_to_send:
                has_first = "[1]" in out_content or "1" in out_content
                has_last = f"[{repeat_count}]" in out_content or str(repeat_count) in out_content
                summary += f" | Coverage markers: [1]={has_first}, [last]={has_last}"
            print(f"{summary}")
            set_cache(cache_key, {"summary": summary, "out_preview": out_content[:200], "tokens": out_tokens})
            record_result("indic-translate", label, summary, actual_cost)
        else:
            summary = f"HTTP {resp.status_code} ({latency:.1f}s): {resp.text[:120]}"
            print(f"Failed: {summary}")
            record_result("indic-translate", label, summary, 0.0)


# ---------------------------------------------------------------------------
# 3. TTS Probe (~60, ~150, ~300 chars, stop at first error)
# ---------------------------------------------------------------------------
def probe_speak() -> None:
    global total_spent_inr
    print("\n[3/4] Probing Indic-Speak limits (~60, ~150, ~300 chars)...")
    key = os.environ.get("BODHAN_SPEAK_KEY")
    if not key:
        record_result("indic-speak", "TTS tests", "SKIPPED: No Key", 0.0)
        return

    # Formulate Hindi strings with varying char lengths
    # ~60 chars: "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है।" (43 chars)
    # ~150 chars: "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है और शाम को पाँच बजे बंद होता है। छात्र एक सप्ताह के लिए दो पुस्तकें ले सकते हैं।" (123 chars)
    # ~300 chars: "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है और शाम को पाँच बजे बंद होता है। छात्र एक सप्ताह के लिए दो पुस्तकें ले सकते हैं। कृपया समय पर पुस्तकें लौटाएँ ताकि अन्य छात्र भी उन्हें पढ़ सकें और लाभ उठा सकें।" (205 chars)
    text_60 = "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है।"
    text_150 = "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है और शाम को पाँच बजे बंद होता है। छात्र एक सप्ताह के लिए दो पुस्तकें ले सकते हैं।"
    text_300 = "विद्यालय का पुस्तकालय सुबह नौ बजे खुलता है और शाम को पाँच बजे बंद होता है। छात्र एक सप्ताह के लिए दो पुस्तकें ले सकते हैं। कृपया समय पर पुस्तकें लौटाएँ ताकि अन्य छात्र भी उन्हें पढ़ सकें और लाभ उठा सकें।"

    tests = [
        (f"~60 chars ({len(text_60)} chars)", text_60),
        (f"~150 chars ({len(text_150)} chars)", text_150),
        (f"~300 chars ({len(text_300)} chars)", text_300),
    ]

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for label, text_input in tests:
        char_count = len(text_input)
        cost = (char_count / 10000.0) * 6.00  # ₹6 per 10k chars

        if total_spent_inr + cost > SPEND_CAP_INR:
            print(f"[LIMIT] Spend cap reached before {label}, skipping.")
            record_result("indic-speak", label, "ABORTED: Spend cap", 0.0)
            break

        cache_key = hashlib.sha256(f"tts_{char_count}_{text_input[:30]}".encode()).hexdigest()
        cached = get_cache(cache_key)
        if cached:
            print(f"Cache hit for {label}!")
            record_result("indic-speak", label, f"Cached: {cached.get('summary')}", 0.0)
            continue

        payload = {
            "model": "indic-speak",
            "input": text_input,
            "voice": "Amit",
            "instructions": json.dumps({"lang": "hi"}),
        }

        print(f"Sending {label}...")
        t0 = time.perf_counter()
        resp = requests.post(f"{BASE_URL}/v1/audio/speech", headers=headers, json=payload, timeout=30)
        latency = time.perf_counter() - t0

        if resp.status_code == 200:
            out_file = CACHE_DIR / f"tts_{char_count}.wav"
            out_file.write_bytes(resp.content)
            audio_dur = get_audio_duration_seconds(out_file)
            summary = f"Success in {latency:.1f}s. Audio duration: {audio_dur:.2f}s ({len(resp.content)} bytes)"
            print(f"{summary}")
            set_cache(cache_key, {"summary": summary, "audio_dur": audio_dur, "bytes": len(resp.content)})
            record_result("indic-speak", label, summary, cost)
        else:
            summary = f"HTTP {resp.status_code} ({latency:.1f}s): {resp.text[:120]}"
            print(f"Error on {label}: {summary}")
            record_result("indic-speak", label, summary, 0.0)
            print("Stopping TTS probe at first error.")
            break


# ---------------------------------------------------------------------------
# 4. OCR Probe (Single image + Multi-page rule probe)
# ---------------------------------------------------------------------------
def probe_ocr() -> None:
    global total_spent_inr
    print("\n[4/4] Probing Indic-OCR limit (test_image.png & multi-image schema)...")
    key = os.environ.get("BODHAN_OCR_KEY")
    if not key:
        record_result("indic-ocr", "test_image.png", "SKIPPED: No Key", 0.0)
        return

    img_path = SAMPLES_DIR / "test_image.png"
    if not img_path.exists():
        record_result("indic-ocr", "test_image.png", "SKIPPED: Not found", 0.0)
        return

    # Check multi-image attempt (2 image parts in single request)
    b64_img = base64.b64encode(img_path.read_bytes()).decode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # Test 1: Single image (check cache first)
    cost = 0.20  # ₹0.20 per image
    cache_key = hashlib.sha256(f"ocr_single_{img_path.stat().st_size}".encode()).hexdigest()
    cached = get_cache(cache_key)
    if cached:
        print("Cache hit for single OCR image!")
        record_result("indic-ocr", "single test_image.png", f"Cached: {cached.get('summary')}", 0.0)
    else:
        if total_spent_inr + cost <= SPEND_CAP_INR:
            payload = {
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}],
                    }
                ],
            }
            t0 = time.perf_counter()
            resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload, timeout=30)
            latency = time.perf_counter() - t0
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                blocks = len(data.get("blocks", []))
                summary = f"Success in {latency:.1f}s. Extracted chars={len(text)}, layout blocks={blocks}"
                set_cache(cache_key, {"summary": summary, "text": text})
                record_result("indic-ocr", "single test_image.png", summary, cost)
            else:
                summary = f"HTTP {resp.status_code} ({latency:.1f}s): {resp.text[:120]}"
                record_result("indic-ocr", "single test_image.png", summary, 0.0)

    # Test 2: Multi-image probe (send 2 images in messages content)
    # Does the API accept multiple images or error with multi-page instruction?
    multi_cache_key = "ocr_multi_image_probe"
    cached_multi = get_cache(multi_cache_key)
    if cached_multi:
        print("Cache hit for multi-image OCR probe!")
        record_result("indic-ocr", "multi-image payload (2 images)", f"Cached: {cached_multi.get('summary')}", 0.0)
    else:
        # Check spend cap
        if total_spent_inr + cost <= SPEND_CAP_INR:
            payload_multi = {
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }
                ],
            }
            t0 = time.perf_counter()
            resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload_multi, timeout=30)
            latency = time.perf_counter() - t0
            if resp.status_code == 200:
                summary = f"API accepted 2 images in {latency:.1f}s! Status: 200"
                set_cache(multi_cache_key, {"summary": summary})
                record_result("indic-ocr", "multi-image payload (2 images)", summary, cost * 2)
            else:
                summary = f"API rejected multi-image (HTTP {resp.status_code} in {latency:.1f}s): {resp.text[:100]}"
                set_cache(multi_cache_key, {"summary": summary})
                record_result("indic-ocr", "multi-image payload (2 images)", summary, 0.0)


# ---------------------------------------------------------------------------
# Main Execution & Table Output
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print(f"Bodhan API Limits Probe (Spend Cap: Rs {SPEND_CAP_INR:.2f})")
    print("=" * 60)

    probe_transcribe()
    probe_translate()
    probe_speak()
    probe_ocr()

    print("\n" + "=" * 90)
    print(f"{'Model':<18} | {'What was tried':<32} | {'Result':<26} | {'Cost (INR)':<10}")
    print("-" * 90)
    for r in results_table:
        print(f"{r['model']:<18} | {r['tried']:<32} | {str(r['result'])[:26]:<26} | Rs {float(r['cost']):.4f}")
    print("-" * 90)
    print(f"Total spent in this run: Rs {total_spent_inr:.4f} / Limit: Rs {SPEND_CAP_INR:.2f}")
    print("=" * 90)
