"""Pytest configuration and sandbox/Windows loopback compatibility patch."""

from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import asyncio.proactor_events as pe
    import socket

    needs_patch = False
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.bind(("127.0.0.1", 0))
        test_port = test_sock.getsockname()[1]
        conn_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn_sock.settimeout(0.05)
        try:
            conn_sock.connect(("127.0.0.1", test_port))
        except (OSError, TimeoutError):
            needs_patch = True
        finally:
            conn_sock.close()
            test_sock.close()
    except Exception:
        needs_patch = True

    if needs_patch:
        class _PipeSocket:
            def __init__(self, fd: int) -> None:
                self._fd: int | None = fd

            def fileno(self) -> int:
                return self._fd if self._fd is not None else -1

            def setblocking(self, _blocking: bool) -> None:
                pass

            def close(self) -> None:
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None

            def send(self, data: bytes) -> int:
                if self._fd is not None:
                    return os.write(self._fd, data)
                return 0

        def _sandbox_safe_make_self_pipe(self: pe.BaseProactorEventLoop) -> None:
            r, w = os.pipe()
            self._ssock = _PipeSocket(r)  # type: ignore[assignment]
            self._csock = _PipeSocket(w)  # type: ignore[assignment]
            self._internal_fds += 1

        pe.BaseProactorEventLoop._make_self_pipe = _sandbox_safe_make_self_pipe
        pe.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None
