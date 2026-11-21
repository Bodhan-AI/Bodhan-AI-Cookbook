import json
import wave
from pathlib import Path
from types import SimpleNamespace

from evals.task_agent import run as eval_runner


def _fake_case_process(monkeypatch, tmp_path, *, valid_wav=True, dry_run_only=False):
    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        case_root = Path(command[command.index("--out") + 1])
        run_dir = case_root / "fake-run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        wav_path = artifacts / "spoken.wav"
        if valid_wav:
            with wave.open(str(wav_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 80)
        else:
            wav_path.write_bytes(b"not a wav")
        (run_dir / "result.md").write_text("Here is a verified transcript.", encoding="utf-8")
        tool_event = {
            "type": "tool_call", "tool": "synthesize_speech", "is_error": False,
            "arguments": {"dry_run": dry_run_only},
        }
        (run_dir / "report.json").write_text(json.dumps({
            "status": "completed",
            "bodhan_cost_inr": 0.0,
            "steps": 1,
            "tool_events": [tool_event],
            "artifacts": [{"path": "artifacts/spoken.wav", "size_bytes": wav_path.stat().st_size}],
        }), encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    case = {
        "case_id": "artifact-check",
        "task": "Create speech and transcript.",
        "inputs": [],
        "required_tools": ["synthesize_speech"],
        "required_artifact_extensions": [".wav"],
        "minimum_text_artifact_characters": 0,
    }
    return eval_runner.run_case(case, tmp_path / "results", 1)


def test_eval_runner_checks_generated_audio_is_valid(monkeypatch, tmp_path):
    result = _fake_case_process(monkeypatch, tmp_path, valid_wav=True)
    assert result["passed"] is True
    assert any(check["name"] == "valid_artifact.wav" and check["passed"] for check in result["deterministic_checks"])


def test_eval_runner_rejects_invalid_audio_even_if_tool_claimed_success(monkeypatch, tmp_path):
    result = _fake_case_process(monkeypatch, tmp_path, valid_wav=False)
    assert result["passed"] is False
    assert any(check["name"] == "valid_artifact.wav" and not check["passed"] for check in result["deterministic_checks"])


def test_eval_runner_does_not_count_paid_dry_run_as_execution(monkeypatch, tmp_path):
    result = _fake_case_process(monkeypatch, tmp_path, valid_wav=True, dry_run_only=True)
    assert result["attempted_tool_coverage"] == 1.0
    assert result["execution_coverage"] == 0.0
    assert result["passed"] is False


def test_eval_runner_checks_requested_unicode_in_artifact_not_markdown(tmp_path, monkeypatch):
    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        run_dir = Path(command[command.index("--out") + 1]) / "fake-run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        result_artifact = artifacts / "transliteration.txt"
        result_artifact.write_text("आपका स्वागत है", encoding="utf-8")
        (run_dir / "result.md").write_text("The final response contains garbled text.", encoding="utf-8")
        (run_dir / "report.json").write_text(json.dumps({
            "status": "completed", "bodhan_cost_inr": 0.0, "steps": 1,
            "tool_events": [{"type": "tool_call", "tool": "transliterate_text", "is_error": False, "arguments": {}}],
            "artifacts": [{"path": "artifacts/transliteration.txt", "size_bytes": result_artifact.stat().st_size}],
        }), encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(eval_runner.subprocess, "run", fake_run)
    result = eval_runner.run_case({
        "case_id": "unicode-artifact-check", "task": "Transliterate this.", "inputs": [],
        "required_tools": ["transliterate_text"], "artifact_must_contain_unicode_script": "devanagari",
        "minimum_text_artifact_count": 1,
    }, tmp_path / "results", 1)
    assert result["passed"] is True
    assert all(check["passed"] for check in result["deterministic_checks"])
