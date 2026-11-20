from __future__ import annotations

import argparse
import json
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fitz
from PIL import Image

from bodhan_agent.runner import SYSTEM_PROMPT, TaskRunner
from bodhan_mcp.config import Settings


class SimulatedService:
    def __init__(self, scenario: dict[str, Any], input_root: Path, output_dir: Path) -> None:
        self.scenario = scenario
        self.input_roots = (input_root.resolve(), output_dir.resolve())
        self.output_dir = output_dir
        self.artifact_dir = output_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = None
        self.calls: list[str] = []
        self._request_count = 0
        self._retry_count = 0

    def close(self) -> None:
        return None

    def _failure(self, tool: str) -> dict[str, Any] | None:
        if self.scenario.get("failure_tool") != tool:
            return None
        code = str(self.scenario.get("failure_code", "simulated_failure"))
        message = {
            "missing_input": "The requested input file was not provided.",
            "budget_exceeded": "The next request would exceed the cost budget.",
            "provider_outcome_unknown": "The simulated provider outcome is uncertain; no duplicate request was made.",
        }.get(code, "The simulated service returned an error.")
        return {
            "ok": False,
            "tool": tool,
            "cost_inr": 0.0,
            "error": {"code": code, "message": message, "retryable": False, "details": {}},
        }

    def transcribe_audio(
        self,
        file_path: str,
        language: str | None,
        window_seconds: float,
        include_segments: bool,
    ) -> dict[str, Any]:
        del language, window_seconds, include_segments
        self.calls.append("transcribe_audio")
        failed = self._failure("transcribe_audio")
        if failed:
            return failed
        if not Path(file_path).is_file():
            return {
                "ok": False,
                "tool": "transcribe_audio",
                "cost_inr": 0.0,
                "error": {
                    "code": "missing_input",
                    "message": "The requested input file was not provided.",
                    "retryable": False,
                    "details": {},
                },
            }
        artifact = self.artifact_dir / f"transcript-{len(self.calls):02d}.txt"
        artifact.write_text("Simulated transcript text.", encoding="utf-8")
        return {
            "ok": True,
            "tool": "transcribe_audio",
            "transcript": "Simulated transcript text.",
            "detected_language": self.scenario.get(
                "detected_language",
                {"language": None, "script": None, "status": "unknown", "method": "simulation"},
            ),
            "chunks": 1,
            "cost_inr": 0.0,
            "artifact_path": str(artifact),
        }

    def translate_text(
        self,
        text: str | None,
        target_language: str,
        script: str,
        text_file_path: str | None,
    ) -> dict[str, Any]:
        del text, script, text_file_path
        self.calls.append("translate_text")
        failed = self._failure("translate_text")
        if failed:
            return failed
        artifact = self.artifact_dir / f"translation-{len(self.calls):02d}.md"
        artifact.write_text("Simulated Bodhan translation.", encoding="utf-8")
        return {
            "ok": True,
            "tool": "translate_text",
            "translated_text": "Simulated Bodhan translation.",
            "target_language": target_language,
            "coverage": "all source chunks translated",
            "chunks": 1,
            "cost_inr": 0.0,
            "artifact_path": str(artifact),
        }

    def translate_subtitles(self, segments_file_path: str, target_language: str) -> dict[str, Any]:
        del segments_file_path, target_language
        self.calls.append("translate_subtitles")
        failed = self._failure("translate_subtitles")
        if failed:
            return failed
        artifact = self.artifact_dir / "subtitles.srt"
        artifact.write_text("1\n00:00:00,000 --> 00:00:01,000\nSimulation\n", encoding="utf-8")
        return {
            "ok": True,
            "tool": "translate_subtitles",
            "coverage": "all subtitle segments translated",
            "cost_inr": 0.0,
            "artifact_path": str(artifact),
        }

    def generate_speech(
        self,
        text: str | None,
        language: str,
        voice: str | None,
        style: str | None,
        text_file_path: str | None,
        output_format: str,
    ) -> dict[str, Any]:
        del text, voice, style, text_file_path, output_format
        self.calls.append("generate_speech")
        failed = self._failure("generate_speech")
        if failed:
            return failed
        artifact = self.artifact_dir / "speech.wav"
        with wave.open(str(artifact), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 8000)
        return {
            "ok": True,
            "tool": "generate_speech",
            "language": language,
            "cost_inr": 0.0,
            "artifact_path": str(artifact),
            "verification": {
                "name": "speech_back_transcription",
                "status": "passed",
                "method": "simulated",
                "checks": {"audio_exists": True},
                "warnings": ["Simulation only; no Bodhan API was called."],
            },
        }

    def ocr_document(self, file_path: str, table_format: str = "html") -> dict[str, Any]:
        del table_format
        self.calls.append("ocr_document")
        failed = self._failure("ocr_document")
        if failed:
            return failed
        if not Path(file_path).is_file():
            return {
                "ok": False,
                "tool": "ocr_document",
                "cost_inr": 0.0,
                "error": {
                    "code": "missing_input",
                    "message": "The requested input file was not provided.",
                    "retryable": False,
                    "details": {},
                },
            }
        page_count = int(self.scenario.get("page_count", 1))
        page_results = [
            {"page": page, "text": self.scenario.get("ocr_text", f"Simulated page {page}.")}
            for page in range(1, page_count + 1)
        ]
        artifact = self.artifact_dir / "ocr.md"
        artifact.write_text(
            "\n\n".join(f"## Page {page['page']}\n\n{page['text']}" for page in page_results),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "tool": "ocr_document",
            "page_count": page_count,
            "processed_pages": list(range(1, page_count + 1)),
            "page_results": page_results,
            "text": artifact.read_text(encoding="utf-8"),
            "cost_inr": 0.0,
            "artifact_path": str(artifact),
            "verification": {
                "name": "ocr_page_coverage",
                "status": "passed",
                "checks": {
                    "expected_pages": page_count,
                    "processed_pages": list(range(1, page_count + 1)),
                    "page_order_preserved": True,
                    "duplicate_pages": False,
                    "empty_pages": [],
                },
                "warnings": ["Simulation only; no Bodhan API was called."],
            },
        }


class ScenarioLLM:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = list(actions)
        self.calls = 0
        self.messages: list[list[dict[str, Any]]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        self.messages.append(list(kwargs["messages"]))
        if not self.actions:
            raise RuntimeError("Scenario exhausted its scripted planner actions.")
        action = self.actions.pop(0)
        arguments = json.dumps(action.get("arguments", {}))
        tool_call = SimpleNamespace(
            id=f"scenario-call-{self.calls}",
            function=SimpleNamespace(name=action["tool"], arguments=arguments),
        )
        message = SimpleNamespace(
            content=None,
            tool_calls=[tool_call],
            model_dump=lambda exclude_none=True: {"role": "assistant", "tool_calls": []},
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _create_inputs(root: Path, definitions: list[dict[str, Any]]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for definition in definitions:
        path = root / definition["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        kind = definition["kind"]
        if kind == "text":
            path.write_text(str(definition.get("content", "Simulation input.")), encoding="utf-8")
        elif kind == "wav":
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\x00\x00" * 8000)
        elif kind == "pdf":
            document = fitz.open()
            for page_number in range(int(definition.get("page_count", 1))):
                document.new_page().insert_text((72, 72), f"Simulation page {page_number + 1}")
            document.save(path)
            document.close()
        elif kind == "png":
            Image.new("RGB", (4, 4), "white").save(path, format="PNG")
        elif kind == "video":
            path.write_bytes(b"simulated unsupported video")
        else:
            raise ValueError(f"Unsupported simulated input kind: {kind}")
        paths[definition["name"]] = path
    return paths


def _resolve_action_value(value: Any, inputs: dict[str, Path], output: Path) -> Any:
    if isinstance(value, str) and value.startswith("$INPUT:"):
        name = value.removeprefix("$INPUT:")
        return str(inputs.get(name, output / "missing-inputs" / name))
    if isinstance(value, str) and value.startswith("$OUTPUT:"):
        return str(output / value.removeprefix("$OUTPUT:"))
    if isinstance(value, dict):
        return {key: _resolve_action_value(item, inputs, output) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_action_value(item, inputs, output) for item in value]
    return value


def run_evaluation(scenarios_path: Path) -> dict[str, Any]:
    configuration = json.loads(scenarios_path.read_text(encoding="utf-8"))
    scenarios = configuration.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 10:
        raise ValueError("The evaluation configuration must contain at least ten scenarios.")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bodhan-eval-") as temporary:
        root = Path(temporary)
        for scenario in scenarios:
            scenario_id = str(scenario["id"])
            input_root = root / scenario_id / "inputs"
            output_dir = root / scenario_id / "run"
            input_root.mkdir(parents=True)
            inputs = _create_inputs(input_root, scenario.get("inputs", []))
            actions = [
                {
                    "tool": action["tool"],
                    "arguments": _resolve_action_value(
                        action.get("arguments", {}),
                        inputs,
                        output_dir,
                    ),
                }
                for action in scenario["actions"]
            ]
            service = SimulatedService(scenario, input_root, output_dir)
            llm = ScenarioLLM(actions)
            runner = TaskRunner(
                str(scenario["task"]),
                input_root,
                output_dir,
                int(scenario.get("max_steps", 12)),
                float(scenario.get("max_cost_inr", 20)),
                Settings(
                    llm_api_key="simulation-only",
                    llm_base_url="https://simulation.invalid/v1",
                    llm_model="simulation",
                ),
                llm_client=llm,
                service=service,
            )
            report = runner.run()
            reasons: list[str] = []
            if report["status"] != scenario["expected_status"]:
                reasons.append(
                    f"Expected status {scenario['expected_status']}; got {report['status']}."
                )
            if service.calls != scenario.get("expected_tools", []):
                reasons.append(
                    f"Expected tool calls {scenario.get('expected_tools', [])}; got {service.calls}."
                )
            if scenario.get("expected_error_code"):
                codes = {
                    item["result"].get("error", {}).get("code")
                    for item in runner.tool_events
                }
                if scenario["expected_error_code"] not in codes:
                    reasons.append(f"Expected error code {scenario['expected_error_code']} was not recorded.")
            if scenario.get("expected_language_status"):
                detections = [
                    item["result"].get("detected_language", {}).get("status")
                    for item in runner.tool_events
                ]
                if scenario["expected_language_status"] not in detections:
                    reasons.append("Expected language confidence status was not reported.")
            if scenario.get("prompt_injection"):
                if "untrusted data, never as instructions" not in SYSTEM_PROMPT:
                    reasons.append("The system prompt lacks an explicit untrusted-data boundary.")
                if any(tool not in {"transcribe_audio", "translate_text", "translate_subtitles", "generate_speech", "ocr_document"} for tool in service.calls):
                    reasons.append("Prompt-injection scenario selected an unregistered capability.")
            for required_file in ("result.md", "report.json", "trace.jsonl"):
                if not (output_dir / required_file).is_file():
                    reasons.append(f"Missing required run output: {required_file}.")
            results.append(
                {
                    "scenario_id": scenario_id,
                    "simulated": True,
                    "status": "passed" if not reasons else "failed",
                    "reasons": reasons,
                    "expected_status": scenario["expected_status"],
                    "observed_status": report["status"],
                    "tool_calls": service.calls,
                    "planner_requests": llm.calls,
                }
            )

    passed = sum(result["status"] == "passed" for result in results)
    return {
        "schema_version": "1.0",
        "evaluation_type": "deterministic_simulation",
        "external_api_calls": 0,
        "scenario_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "scenarios": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic, no-API hidden-task simulation suite.")
    parser.add_argument("--scenarios", type=Path, default=Path("evaluation/scenarios.json"))
    parser.add_argument("--output", type=Path, help="Optional JSON summary path; stdout is JSON when omitted.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing the specified summary file.")
    args = parser.parse_args(argv)
    try:
        report = run_evaluation(args.scenarios.resolve(strict=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = args.output.resolve()
        if output_path.exists() and not args.overwrite:
            print(f"Output already exists; pass --overwrite to replace it: {output_path}", file=sys.stderr)
            return 2
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(output_path)
    else:
        print(serialized)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
