"""Credential-safe Bodhan connectivity diagnostic. Does not print secrets."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from bodhan_shared.config import load_dotenv
from bodhan_shared.gateway import Budget, Gateway
from bodhan_shared.store import Workspace
from bodhan_shared.errors import TaskError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-translation", action="store_true",
                        help="Send one tiny paid translation request to Bodhan")
    args = parser.parse_args()
    load_dotenv()
    workspace = Workspace(Path("outputs") / "connection_probe")
    gateway = Gateway(workspace, budget=Budget(0.25))
    raw = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")
    # Display only scheme, hostname and fixed path—not username, password, query or fragment.
    u = urlsplit(raw)
    print("Bodhan base host:", u.scheme + "://" + (u.hostname or "<none>"))
    print("Configured path:", u.path or "/")
    print("Effective translation route:", gateway.base_url + "/translate")
    for name in ("TRANSLATE", "TRANSCRIBE", "SPEAK", "OCR"):
        key = os.getenv(f"BODHAN_{name}_KEY", "")
        print(f"BODHAN_{name}_KEY:", "present" if key and not key.startswith("replace-") else "MISSING")
    llm = urlsplit(os.getenv("LLM_BASE_URL", ""))
    print("LLM host:", llm.hostname or "<none>")
    print("LLM model:", os.getenv("LLM_MODEL", "<none>"))
    if not args.probe_translation:
        print("No network requests sent. Add --probe-translation to test Bodhan directly.")
        return 0
    try:
        translated, cached = gateway.translate("The class begins at nine.", "bn")
    except TaskError as exc:
        print("Translation probe FAILED:", exc.code, str(exc))
        return 1
    print("Translation probe SUCCESS; output characters:", len(translated), "cached:", cached)
    if gateway.calls:
        print("Successful translation route:", gateway.calls[-1]["endpoint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
