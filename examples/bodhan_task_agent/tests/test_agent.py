import json
from pathlib import Path

from bodhan_agent.runner import AgentRunner


class ScriptedBrain:
    """Local deterministic simulation of LLM tool selection, not a mocked successful API run."""
    def __init__(self):
        self.turn = 0
        self.plans = [
            ("inspect_files", {"folder": "inputs"}),
            ("save_english_text", {"path": "artifacts/summary.md", "text": "This is an English summary."}),
        ]

    def complete(self, messages):
        if self.turn < len(self.plans):
            name, args = self.plans[self.turn]
            self.turn += 1
            return {"content": None, "tool_calls": [{"id": f"c{self.turn}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
        return {"content": "Created the English summary at artifacts/summary.md."}


def test_end_to_end_agent_loop_with_fake_brain(tmp_path):
    source = tmp_path / "user_files"
    source.mkdir()
    (source / "note.txt").write_text("A school opens at nine.")
    result_dir = tmp_path / "run"
    r = AgentRunner("Summarize in English", str(source), str(result_dir),
                    brain=ScriptedBrain()).run()
    assert r["status"] == "completed"
    assert "artifacts/summary.md" in r["artifacts"]
    trace = (result_dir / "trace.jsonl").read_text()
    assert '"type": "tool"' in trace
    assert (result_dir / "report.json").is_file()


def test_missing_input_fails_honestly(tmp_path):
    r = AgentRunner("do something", str(tmp_path / "missing"),
                    str(tmp_path / "out"), brain=ScriptedBrain()).run()
    assert r["status"] == "failed"
    assert not r["artifacts"]


def test_generic_audio_requirement_prevents_false_claim(tmp_path):
    source = tmp_path / "s"
    source.mkdir()
    (source / "note.txt").write_text("Hello")
    # This scripted brain claims it completed work but only saves English text.
    run = AgentRunner("Read aloud this note as audio", str(source), str(tmp_path / "out"),
                      brain=ScriptedBrain()).run()
    assert run["status"] == "partial"
    assert not any(x.endswith(".wav") for x in run["artifacts"])


def test_generated_files_excludes_internal_audio_windows(tmp_path):
    r = AgentRunner("Task", str(tmp_path), str(tmp_path / "out"), brain=ScriptedBrain())
    hidden = r.out / "artifacts" / "sample_asr_123"
    hidden.mkdir(parents=True)
    (hidden / "normalized.wav").write_bytes(b"junk")
    assert not r.generated()
