"""Manual smoke check for real Bodhan Indic-Transcribe API call (Standing Rule F2).

Run manually with real API keys:
    python scripts/smoke/transcribe_once.py --audio path/to/sample.wav
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Windows sandbox isolation guard: patch only if loopback TCP socketpair is blocked (e.g. AppContainer sandbox)
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

from bodhan_mcp.clients.bodhan import BodhanClient  # noqa: E402


async def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Manual transcribe smoke check")
    parser.add_argument("--audio", type=str, required=True, help="Path to WAV audio file")
    parser.add_argument("--lang", type=str, default=None, help="Optional language hint")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        sys.stderr.write(f"Error: audio file not found: {audio_path}\n")
        sys.exit(1)

    audio_bytes = audio_path.read_bytes()
    client = BodhanClient()

    print(f"Sending {len(audio_bytes)} bytes to Bodhan transcribe...")
    try:
        result = await client.transcribe(
            audio_bytes=audio_bytes,
            filename=audio_path.name,
            language_hint=args.lang,
            duration_s=5.0,
        )
        print("Success! Response received:")
        print(f"Data: {result.get('data')}")
        print(f"Cost INR: {result.get('cost_inr')}")
    except Exception as e:
        sys.stderr.write(f"Transcribe call failed: {e}\n")
        sys.exit(1)
    finally:
        await client.client.close()


if __name__ == "__main__":
    asyncio.run(main())
