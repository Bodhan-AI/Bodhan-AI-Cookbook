"""The real CLI entry point: argument parsing, input sandboxing (inputs read-only outside --out), exit codes, outputs."""
import json

import bodhan_agent.llm as llm_mod
from bodhan_agent.__main__ import main
from bodhan_mcp import client as client_mod
from test_agent import ScriptedLLM, last_result


def test_cli_end_to_end(fake, tmp_path, monkeypatch):
    inputs, out = tmp_path / "in", tmp_path / "run"
    inputs.mkdir()
    (inputs / "para.txt").write_text("Rain fills the rivers. Farmers wait for the monsoon.")
    fake.stt_text = "Rain fills the rivers. Farmers wait for the monsoon."
    steps = [
        ("submit_plan", {"steps": ["inspect", "speak"]}),
        ("synthesize_speech", {"language": "en", "text_file": str(inputs / "para.txt"), "output_path": "artifacts/read.mp3"}),
        ("finish", {"status": "completed", "summary": "Read the paragraph aloud.", "artifacts": [{"path": "artifacts/read.mp3"}]}),
    ]
    monkeypatch.setattr(llm_mod, "LLM", lambda: ScriptedLLM(steps))
    shared = client_mod.get_client()  # the fixture's client, wired to the fake API
    monkeypatch.setattr(client_mod, "BodhanClient", lambda cap_inr=None: shared)
    rc = main(["--task", "Read this aloud", "--files", str(inputs), "--out", str(out), "--max-cost-inr", "5"])
    assert rc == 0
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "completed" and (out / "artifacts" / "read.mp3").exists()
    assert (out / "result.md").exists() and (out / "trace.jsonl").read_text().count("\n") >= 4
    assert report["cost"]["cap_inr"] == 5.0


def test_cli_missing_input_still_produces_outputs_and_exit_code(fake, tmp_path, monkeypatch):
    out = tmp_path / "run"
    steps = [("finish", {"status": "impossible", "summary": "The file is missing."})]
    monkeypatch.setattr(llm_mod, "LLM", lambda: ScriptedLLM(steps))
    shared = client_mod.get_client()
    monkeypatch.setattr(client_mod, "BodhanClient", lambda cap_inr=None: shared)
    assert main(["--task", "Transcribe it", "--files", str(tmp_path / "nope.wav"), "--out", str(out)]) == 0
    assert json.loads((out / "report.json").read_text())["status"] == "impossible"
