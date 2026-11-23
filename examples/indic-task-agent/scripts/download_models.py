#!/usr/bin/env python3
"""
Model download script for Bodhan Indic models.
Zero external dependencies required to start: reads .env with pure python
and auto-installs huggingface_hub if missing.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

# Pure Python .env loader (no python-dotenv required)
def load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val

load_env()
token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")

if not token:
    print("\n[ERROR] No HF_TOKEN found in environment or .env file!")
    print("Please set HF_TOKEN=hf_... in your .env file before running.\n")
    sys.exit(1)

# Ensure huggingface_hub is available
try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("[INFO] 'huggingface_hub' not found. Installing now...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub"])
    from huggingface_hub import snapshot_download

MODELS = {
    "transcribe": {
        "repo_id": "bodhan-ai/indic-transcribe-core",
        "local_dir": "./models/indic-transcribe-core",
        "description": "Indic-Transcribe Core (ASR ~1.2B params)"
    },
    "translate": {
        "repo_id": "bodhan-ai/indic-translate",
        "local_dir": "./models/indic-translate",
        "description": "Indic-Translate (Translation ~8B params)"
    },
    "speak": {
        "repo_id": "bodhan-ai/indic-speak",
        "local_dir": "./models/indic-speak",
        "description": "Indic-Speak (TTS ~3B params)"
    },
    "ocr": {
        "repo_id": "bodhan-ai/indic-ocr",
        "local_dir": "./models/indic-ocr",
        "description": "Indic-OCR (Layout & OCR ~0.8B params)"
    }
}

def download_model(key: str, info: dict):
    print(f"\n========================================================")
    print(f"Downloading: {info['description']}")
    print(f"Repository:  {info['repo_id']}")
    print(f"Destination: {info['local_dir']}")
    print(f"========================================================\n")
    
    root_dir = Path(__file__).resolve().parent.parent
    dest_path = (root_dir / info["local_dir"]).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    
    snapshot_download(
        repo_id=info["repo_id"],
        local_dir=str(dest_path),
        token=token,
        local_dir_use_symlinks=False,
        resume_download=True
    )
    print(f"\n[SUCCESS] Completed download for {info['repo_id']} -> {dest_path}\n")

def main():
    parser = argparse.ArgumentParser(description="Download Bodhan model weights from Hugging Face")
    parser.add_argument(
        "--model",
        choices=["all", "transcribe", "translate", "speak", "ocr"],
        default="all",
        help="Specify which model to download (default: all)"
    )
    args = parser.parse_args()

    if args.model == "all":
        for k, v in MODELS.items():
            download_model(k, v)
    else:
        download_model(args.model, MODELS[args.model])

    print("\n[ALL DONE] Model downloads finished. Check ./models directory.\n")

if __name__ == "__main__":
    main()
