"""CLI entrypoint for the Bodhan Task Agent strictly matching SPEC §8.1."""

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

from bodhan_agent.loop import run_task


def main():
    parser = argparse.ArgumentParser(description="Bodhan CLI task agent")
    parser.add_argument("--task", type=str, required=True, help="Task description in English")
    parser.add_argument("--files", type=str, required=True, help="Directory containing input media files")
    parser.add_argument("--out", type=str, required=True, help="Directory to write output artifacts")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum execution steps (default: 30)")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum cost budget in INR (default: 20)")

    args = parser.parse_args()

    res = asyncio.run(
        run_task(
            task=args.task,
            files_dir=Path(args.files),
            out_dir=Path(args.out),
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )
    )

    sys.stderr.write(f"Agent finished with status='{res.get('status')}', cost=₹{res.get('cost_inr', 0.0):.4f}\n")
    if res.get("status") in ("success", "partial"):
        sys.exit(0)
    elif res.get("status") in ("impossible", "budget_exhausted"):
        sys.exit(0)  # Honest termination is normal exit per SPEC
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
