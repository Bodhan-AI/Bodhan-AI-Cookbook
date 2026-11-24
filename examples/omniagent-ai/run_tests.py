
"""Run all OmniAgent AI offline regression tests."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TEST_FILES = [
    "tests/test_audio.py",
    "tests/test_chunked_transcription.py",
    "tests/test_transcription_mcp.py",
    "tests/test_agent_chunk_routing.py",
    "tests/test_planner_retry.py",
]


def main():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    passed = 0

    print("OmniAgent AI - Offline Regression Tests")
    print("=" * 50)

    for test_file in TEST_FILES:
        print(f"\nRunning {test_file}...", flush=True)

        result = subprocess.run(
            [sys.executable, str(ROOT / test_file)],
            cwd=ROOT,
            env=env,
        )

        if result.returncode != 0:
            print(f"\nFAILED: {test_file}")
            return 1

        passed += 1

    print("\n" + "=" * 50)
    print(f"PASS: {passed}/{len(TEST_FILES)} suites passed!")
    print("All offline regression tests passed!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
