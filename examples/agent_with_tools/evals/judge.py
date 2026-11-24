"""DeepEval metrics, judged by the same Groq model (EVAL_JUDGE_MODEL, default LLM_MODEL) through its OpenAI-compatible API.

Nothing here calls OpenAI or Bodhan. Metrics:
  * ToolCorrectness  (deterministic): right paid tools, in the expected order.
  * ArgumentCorrectness (LLM judge): are the tool arguments sensible for the task?
  * TaskCompletion (LLM judge): did the run achieve what the task asked for?
Each LLM metric costs a few Groq calls per case, so they are optional (run_evals --judge / EVAL_JUDGE=1).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # no usage data leaves the machine
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")

from deepeval.metrics import ArgumentCorrectnessMetric, TaskCompletionMetric, ToolCorrectnessMetric  # noqa: E402
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import LLMTestCase, ToolCall  # noqa: E402
from openai import AsyncOpenAI, OpenAI, RateLimitError  # noqa: E402

from bodhan_agent.config import load_llm_settings  # noqa: E402
from evals.harness import CaseRun  # noqa: E402
from evals.stubs import PAID_TOOLS  # noqa: E402


class GroqJudge(DeepEvalBaseLLM):
    """DeepEval judge on any OpenAI-compatible endpoint (here Groq). Structured output via JSON mode."""

    def __init__(self, model: str | None = None):
        s = load_llm_settings()
        self.model_name = model or os.environ.get("EVAL_JUDGE_MODEL") or s.model
        self.client = OpenAI(base_url=s.base_url, api_key=s.api_key, max_retries=6)
        self.aclient = AsyncOpenAI(base_url=s.base_url, api_key=s.api_key, max_retries=6)

    def load_model(self):
        return self.client

    def get_model_name(self) -> str:
        return self.model_name

    def _request(self, prompt: str, schema) -> dict:
        body = {"model": self.model_name, "temperature": 0, "messages": [{"role": "user", "content": prompt + ("\n\nReturn ONLY a valid JSON object." if schema else "")}]}
        if schema:
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _parse(text: str, schema):
        if schema is None:
            return text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return schema.model_validate(json.loads(m.group(0) if m else text))

    def generate(self, prompt: str, schema=None):
        for attempt in range(4):
            try:
                text = self.client.chat.completions.create(**self._request(prompt, schema)).choices[0].message.content or ""
                return self._parse(text, schema)
            except RateLimitError:
                time.sleep(10 * (attempt + 1))
            except ValueError:  # malformed JSON: ask again once or twice
                if attempt >= 2:
                    raise
        raise RuntimeError("judge model kept hitting the rate limit")

    async def a_generate(self, prompt: str, schema=None):
        for attempt in range(4):
            try:
                text = (await self.aclient.chat.completions.create(**self._request(prompt, schema))).choices[0].message.content or ""
                return self._parse(text, schema)
            except RateLimitError:
                await asyncio.sleep(10 * (attempt + 1))
            except ValueError:
                if attempt >= 2:
                    raise
        raise RuntimeError("judge model kept hitting the rate limit")


def _tool_call(name: str, args: dict, output: str | None = None) -> ToolCall:
    return ToolCall(name=name, input_parameters=args, output=output)


def describe_inputs(case: dict) -> str:
    """What the input files contain, in words. The judge never sees the files, so without this it misreads calls that
    refer to them (e.g. it scored 'Read this paragraph aloud' as missing the paragraph)."""
    stubs = case.get("stubs", {}).get("transcribe_audio", {})
    lines = []
    for spec in case.get("inputs", []):
        name = spec["name"]
        if "text" in spec:
            lines.append(f"- {name} (text file) contains: {spec['text']!r}")
        elif "copy" in spec and spec["copy"] == "audio":
            lang = stubs.get("by_file", {}).get(name) or stubs.get("default_language")
            lines.append(f"- {name} (audio clip)" + (f", spoken language: {lang}" if lang else ", spoken language not stated"))
        elif spec.get("copy") == "pdf":
            lines.append(f"- {name} (scanned PDF, 8 pages)")
        elif "copy" in spec:
            lines.append(f"- {name} (image of a printed page or worksheet)")
        elif spec.get("fake_video"):
            lines.append(f"- {name} (video file)")
    lines += [f"- {p} (does not exist)" for p in case.get("extra_paths", [])]
    return "\n".join(lines)


def build_test_case(case: dict, run: CaseRun, expect: dict) -> LLMTestCase:
    paid = [c for c in run.calls if c.name in PAID_TOOLS]
    expected = [ToolCall(name=n) for n in expect.get("tool_sequence", [])]
    if not expected and "tool_counts" in expect:  # no order given: expected = the paid tools with their counts
        expected = [ToolCall(name=n) for n, k in expect["tool_counts"].items() if n in PAID_TOOLS for _ in range(k)]
    files = describe_inputs(case)
    return LLMTestCase(
        input=f"{case['task']}\n\n" + (f"Input files provided with the task:\n{files}" if files else "No input files were provided."),
        actual_output=run.answer or "(no answer)",
        tools_called=[_tool_call(c.name, c.args, c.result[:500]) for c in paid],
        expected_tools=expected,
    )


def deepeval_scores(case: dict, run: CaseRun, expect: dict, judge: GroqJudge | None, llm_metrics: bool = True) -> list[dict]:
    """[{name, value, comment}] for the DeepEval metrics. Safe to call: a metric that errors yields value None."""
    tc = build_test_case(case, run, expect)
    metrics: list = []
    if tc.expected_tools:
        metrics.append(("de_tool_correctness", ToolCorrectnessMetric(threshold=1.0, should_consider_ordering=bool(expect.get("tool_sequence")), model=judge, async_mode=False, include_reason=True)))
    if llm_metrics and judge is not None and "completed" in expect.get("status", ["completed"]):  # a correct refusal is not a 'completed' task
        if tc.tools_called:
            metrics.append(("de_argument_correctness", ArgumentCorrectnessMetric(threshold=0.7, model=judge, async_mode=False, include_reason=True)))
        metrics.append(("de_task_completion", TaskCompletionMetric(threshold=0.7, model=judge, async_mode=False, include_reason=True)))
    out = []
    for name, metric in metrics:
        try:
            metric.measure(tc, _show_indicator=False)
            out.append({"name": name, "value": float(metric.score), "comment": (metric.reason or "")[:500]})
        except Exception as exc:  # judge hiccups (rate limit, bad JSON) must not fail the deterministic part
            out.append({"name": name, "value": None, "comment": f"metric error: {type(exc).__name__}: {str(exc)[:200]}"})
    return out
