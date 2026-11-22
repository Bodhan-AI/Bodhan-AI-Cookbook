"""CLI:  python -m bodhan_agent --task "..." --files inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    f = Path(".env")
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_manifest(paths: list[str]) -> list[dict]:
    """Describe the inputs for the planner. Sets the tools' extra read roots first, so the sandbox lets inspect_media see them."""
    from bodhan_mcp import tools

    resolved = [Path(r).expanduser().resolve() for r in paths]
    roots = [p if p.is_dir() else p.parent for p in resolved if p.exists()]
    os.environ["BODHAN_EXTRA_ROOTS"] = os.pathsep.join(str(r) for r in dict.fromkeys(roots))
    manifest = []
    for raw, p in zip(paths, resolved):
        if not p.exists():
            manifest.append({"path": raw, "exists": False})
            continue
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())):
            info = tools.inspect_media(str(f))
            if info.get("ok"):
                manifest.append({k: v for k, v in info.items() if k != "ok"} | {"exists": True})
            else:
                manifest.append({"path": str(f), "exists": True, "inspect_error": info["error"]["message"]})
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bodhan_agent", description="Plan and run a multi-step language task with Bodhan tools.")
    ap.add_argument("--task", required=True)
    ap.add_argument("--files", nargs="*", default=[], help="input files or folders")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--max-cost-inr", type=float, default=20.0)
    ap.add_argument("--call-timeout", type=float, default=180.0, help="seconds allowed per tool call")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    _load_dotenv()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    out = Path(a.out).resolve()
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    (out / "trace.jsonl").write_text("")

    # the tools may read the inputs and write only inside --out
    os.environ["BODHAN_WORKDIR"] = str(out)
    os.environ["BODHAN_STRICT_PROVENANCE"] = "1"
    manifest = build_manifest(a.files)

    from bodhan_mcp.client import BodhanClient, set_client

    set_client(BodhanClient(cap_inr=a.max_cost_inr))
    from .agent import Agent
    from .llm import LLM

    agent = Agent(LLM(), out, a.max_steps, a.max_cost_inr, a.call_timeout)
    report = agent.run(a.task, manifest)
    print(f"[{report['status']}] cost Rs {report['cost']['total_inr']:.2f} · {report['steps_used']} steps · see {out}/result.md")
    return 0 if report["status"] in ("completed", "completed_with_warnings", "impossible") else 1


if __name__ == "__main__":
    raise SystemExit(main())
