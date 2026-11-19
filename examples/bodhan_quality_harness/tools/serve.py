#!/usr/bin/env python3
"""Start the Bodhan Quality Control Center interactive web application."""

import sys
from pathlib import Path

# Add src to pythonpath
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bodhan_eval.server import run_server

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Start the Bodhan Quality Control Center Web UI")
    p.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    p.add_argument("--report", type=Path, default=Path("report"), help="Path to report output directory")
    args = p.parse_args()
    sys.exit(run_server(port=args.port, report_dir=args.report))
