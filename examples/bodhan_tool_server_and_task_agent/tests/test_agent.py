import asyncio
import tempfile
from pathlib import Path

from bodhan_agent.agent import main_async


class Args:
    pass


def test_missing_files_fail_honestly():
    out = tempfile.mkdtemp()
    args = Args()
    args.task = "Transcribe this file"
    args.files = str(Path(out) / "missing")
    args.out = str(Path(out) / "run")
    args.max_steps = 30
    args.max_cost_inr = 20
    asyncio.run(main_async(args))
    report = __import__("json").loads((Path(args.out) / "report.json").read_text())
    assert report["status"] == "failed"


def test_french_task_is_explicitly_unsupported():
    out = tempfile.mkdtemp()
    args = Args()
    args.task = "Translate this video into French"
    args.files = str(Path(out) / "video.mp4")
    Path(args.files).write_bytes(b"not used")
    args.out = str(Path(out) / "run")
    args.max_steps = 30
    args.max_cost_inr = 20
    asyncio.run(main_async(args))
    result = (Path(args.out) / "result.md").read_text()
    assert "French" not in result or (Path(args.out) / "report.json").exists()
