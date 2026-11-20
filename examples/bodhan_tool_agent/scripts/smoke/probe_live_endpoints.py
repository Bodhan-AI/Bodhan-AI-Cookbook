import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Windows socketpair guard
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
                def fileno(self): return -1
                def setblocking(self, b): pass
                def close(self): pass
            def _dummy_make_self_pipe(self):
                self._ssock = _DummySock()
                self._csock = _DummySock()
                self._internal_fds = 0
            asyncio.proactor_events.BaseProactorEventLoop._make_self_pipe = _dummy_make_self_pipe
            asyncio.proactor_events.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None
            asyncio.proactor_events.BaseProactorEventLoop._write_to_self = lambda self: None
    except Exception:
        pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from bodhan_mcp.clients.bodhan import BodhanClient

async def probe():
    client = BodhanClient()
    print("Testing 1: indic-transcribe...")
    sample_wav = Path("demo/inputs/task_2/hindi_speech.wav")
    try:
        t_res = await client.transcribe(sample_wav.read_bytes(), filename="sample.wav", language_hint="hi", duration_s=4.0)
        print("Transcribe OK:", t_res.get("data", {}).get("text") or t_res.get("data"))
        print("Transcribe cost:", t_res.get("cost_inr"))
    except Exception as e:
        print("Transcribe Error:", e)

    print("\nTesting 2: indic-translate...")
    try:
        tr_res = await client.translate("Hello, how are you?", source_lang="en", target_lang="hi")
        content = tr_res.get("data", {}).get("choices", [{}])[0].get("message", {}).get("content")
        print("Translate OK:", content)
        print("Translate cost:", tr_res.get("cost_inr"))
    except Exception as e:
        print("Translate Error:", e)

    print("\nTesting 3: indic-speak...")
    try:
        sp_res = await client.speak("नमस्ते", language="hi", voice_id="hi_female_1")
        print("Speak OK! Bytes:", len(sp_res.get("audio_bytes", b"")))
        print("Speak cost:", sp_res.get("cost_inr"))
    except Exception as e:
        print("Speak Error:", e)

    print("\nTesting 4: indic-ocr...")
    sample_img = Path("demo/inputs/task_3/worksheet_table.png")
    try:
        ocr_res = await client.ocr(sample_img.read_bytes(), filename="table.png")
        print("OCR OK! Markdown preview:", str(ocr_res.get("data"))[:100])
        print("OCR cost:", ocr_res.get("cost_inr"))
    except Exception as e:
        print("OCR Error:", e)

if __name__ == "__main__":
    asyncio.run(probe())
