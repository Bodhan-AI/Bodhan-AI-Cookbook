"""Bodhan MCP server entrypoint for stdio communication."""

import logging
import sys

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

from bodhan_mcp import config
from bodhan_mcp.server import mcp

# Configure stderr logging strictly (Constraint C7: zero stdout pollution outside JSON-RPC)
logging.basicConfig(
    stream=sys.stderr,
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="[%(asctime)s] [%(levelname)s] [bodhan_mcp] %(message)s",
)

if __name__ == "__main__":
    mcp.run(transport="stdio")
