"""Live smoke test for all four Bodhan Indic models and the LLM planner.

Run with real credentials in .env:
    python scripts/smoke/test_all_live_models.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Windows sandbox isolation guard
if sys.platform == "win32":
    try:
        import socket

        def _is_loopback_blocked() -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                port = s.getsockname()[1]
                c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                c.settimeout(0.1)
                c.connect(("127.0.0.1", port))
                conn, _ = s.accept()
                conn.close()
                c.close()
                s.close()
                return False
            except Exception:
                return True

        if _is_loopback_blocked():
            import asyncio.proactor_events

            class _DummySock:
                def fileno(self):
                    return -1

                def setblocking(self, b):
                    pass

                def close(self):
                    pass

            def _dummy_make_self_pipe(self):
                self._ssock = _DummySock()
                self._csock = _DummySock()
                self._internal_fds = 0

            asyncio.proactor_events.BaseProactorEventLoop._make_self_pipe = _dummy_make_self_pipe
            asyncio.proactor_events.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None
            asyncio.proactor_events.BaseProactorEventLoop._write_to_self = lambda self: None
    except Exception:
        pass

from dotenv import load_dotenv

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from openai import AsyncOpenAI  # noqa: E402

from bodhan_mcp.clients.bodhan import BodhanClient  # noqa: E402


async def test_live_models():
    load_dotenv()
    print("=" * 65)
    print("BODHAN INDIC LIVE MODELS & LLM SMOKE TEST")
    print("=" * 65)

    client = BodhanClient()

    # 1. Transcribe (indic-transcribe)
    print("\n[1/5] Testing Indic-Transcribe...")
    sample_wav = Path("demo/inputs/task_2/hindi_speech.wav")
    if sample_wav.exists():
        try:
            trans_res = await client.transcribe(
                audio_bytes=sample_wav.read_bytes(),
                filename=sample_wav.name,
                language_hint="hi",
                duration_s=4.0,
            )
            print(f"  ✓ Success! Transcript: {trans_res.get('data', {}).get('text')}")
            print(f"  ✓ Cost: ₹{trans_res.get('cost_inr', 0.0):.4f}")
        except Exception as e:
            print(f"  ✗ Transcribe failed: {e}")
    else:
        print("  - Skipped: sample WAV not found.")

    # 2. Translate (indic-translate)
    print("\n[2/5] Testing Indic-Translate...")
    try:
        trans_res = await client.translate(
            text="Good morning, welcome to India.",
            source_lang="en",
            target_lang="hi",
        )
        content = trans_res.get("data", {}).get("choices", [{}])[0].get("message", {}).get("content")
        print(f"  ✓ Success! Translation: {content}")
        print(f"  ✓ Cost: ₹{trans_res.get('cost_inr', 0.0):.4f}")
    except Exception as e:
        print(f"  ✗ Translate failed: {e}")

    # 3. Speak (indic-speak)
    print("\n[3/5] Testing Indic-Speak...")
    try:
        speak_res = await client.speak(
            text="नमस्ते, आपका स्वागत है।",
            language="hi",
            voice_id="hi_female_1",
        )
        audio_len = len(speak_res.get("audio_bytes", b""))
        print(f"  ✓ Success! Audio bytes received: {audio_len}")
        print(f"  ✓ Cost: ₹{speak_res.get('cost_inr', 0.0):.4f}")
    except Exception as e:
        print(f"  ✗ Speak failed: {e}")

    # 4. OCR (indic-ocr)
    print("\n[4/5] Testing Indic-OCR...")
    sample_img = Path("demo/inputs/task_3/worksheet_table.png")
    if sample_img.exists():
        try:
            ocr_res = await client.ocr(
                image_bytes=sample_img.read_bytes(),
                filename=sample_img.name,
            )
            data = ocr_res.get("data", {})
            print(f"  ✓ Success! OCR output: {str(data)[:80]}...")
            print(f"  ✓ Cost: ₹{ocr_res.get('cost_inr', 0.0):.4f}")
        except Exception as e:
            print(f"  ✗ OCR failed: {e}")
    else:
        print("  - Skipped: sample image not found.")

    await client.client.close()

    # 5. LLM Planner (Groq)
    print("\n[5/5] Testing Groq LLM Planner...")
    llm_base = os.getenv("LLM_BASE_URL")
    llm_key = os.getenv("LLM_API_KEY")
    llm_model = os.getenv("LLM_MODEL")

    if llm_base and llm_key and llm_model:
        try:
            oai = AsyncOpenAI(base_url=llm_base, api_key=llm_key)
            resp = await oai.chat.completions.create(
                model=llm_model,
                messages=[{"role": "user", "content": "Respond with: Ready"}],
                max_tokens=10,
            )
            print(f"  ✓ Success! LLM response: {resp.choices[0].message.content.strip()}")
            await oai.close()
        except Exception as e:
            print(f"  ✗ LLM call failed: {e}")
    else:
        print("  - Skipped: LLM credentials incomplete in .env.")

    print("\n" + "=" * 65)
    print("LIVE SMOKE TEST COMPLETE")
    print("=" * 65)


def main():
    asyncio.run(test_live_models())


if __name__ == "__main__":
    main()
