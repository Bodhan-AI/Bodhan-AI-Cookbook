from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import validate
from openai import BadRequestError, RateLimitError

from bodhan_agent.runner import GroqRateLimitController, TaskRunner, _parse_duration
from bodhan_mcp.config import Settings
from bodhan_mcp.errors import ToolError


class FakeService:
    def __init__(self, output_dir: Path, input_root: Path) -> None:
        self.output_dir = output_dir
        self.input_roots = (input_root.resolve(), output_dir.resolve())
        self.ledger = None
        self.calls: list[str] = []
        self.call_arguments: list[tuple[str, tuple[object, ...]]] = []

    def transcribe_audio(
        self,
        file_path: str,
        language: str | None,
        window_seconds: float,
        include_segments: bool,
    ) -> dict:
        self.calls.append("transcribe_audio")
        self.call_arguments.append(
            ("transcribe_audio", (file_path, language, window_seconds, include_segments))
        )
        return {"ok": True, "tool": "transcribe_audio"}

    def translate_subtitles(self, segments_file_path: str, target_language: str) -> dict:
        self.calls.append("translate_subtitles")
        self.call_arguments.append(
            ("translate_subtitles", (segments_file_path, target_language))
        )
        return {"ok": True, "tool": "translate_subtitles"}

    def generate_speech(
        self,
        text: str | None,
        language: str,
        voice: str | None,
        style: str | None,
        text_file_path: str | None,
        output_format: str,
    ) -> dict:
        self.calls.append("generate_speech")
        self.call_arguments.append(
            ("generate_speech", (text, language, voice, style, text_file_path, output_format))
        )
        return {"ok": True, "tool": "generate_speech"}

    def ocr_document(self, file_path: str, table_format: str = "html") -> dict:
        self.calls.append("ocr_document")
        artifact = self.output_dir / "artifacts" / "ocr.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("English source text", encoding="utf-8")
        return {
            "ok": True, "tool": "ocr_document", "text": "English source text",
            "page_count": 1, "processed_pages": [1], "artifact_path": str(artifact), "cost_inr": 0.2,
            "input_fingerprint": "fake-ocr-input",
            "verification": {
                "name": "ocr_page_coverage",
                "status": "passed",
                "checks": {"expected_pages": 1, "processed_pages": [1]},
                "warnings": [],
            },
        }

    def translate_text(self, text: str | None, target_language: str, script: str, text_file_path: str | None) -> dict:
        self.calls.append("translate_text")
        self.call_arguments.append(
            ("translate_text", (text, target_language, script, text_file_path))
        )
        artifact = self.output_dir / "artifacts" / "translation.md"
        artifact.write_text("Bodhan translation", encoding="utf-8")
        return {
            "ok": True, "tool": "translate_text", "translated_text": "Bodhan translation",
            "target_language": target_language,
            "coverage": "all source chunks translated", "artifact_path": str(artifact), "cost_inr": 0.01,
        }


class FakeLLM:
    def __init__(self, output_dir: Path, input_file: Path) -> None:
        self.request_options: list[dict[str, object]] = []
        self.responses = [
            self._response("ocr_document", {"file_path": str(input_file)}),
            self._response(
                "translate_text",
                {"text_file_path": str(output_dir / "artifacts" / "ocr.md"), "target_language": "ta"},
            ),
            self._response(
                "finish_task",
                {"status": "completed", "summary": "The worksheet was translated.", "assumptions": []},
            ),
        ]
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _response(name: str, arguments: dict) -> SimpleNamespace:
        call = SimpleNamespace(
            id=f"call-{name}",
            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
        )
        message = SimpleNamespace(
            content=None,
            tool_calls=[call],
            model_dump=lambda exclude_none=True: {"role": "assistant", "tool_calls": []},
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _create(self, **kwargs: object) -> SimpleNamespace:
        self.request_options.append(kwargs)
        return self.responses.pop(0)


def test_agent_routes_media_options_to_shared_tools(tmp_path: Path) -> None:
    input_file = tmp_path / "input.mp3"
    input_file.write_bytes(b"placeholder")
    output_dir = tmp_path / "output"
    service = FakeService(output_dir, tmp_path)
    runner = TaskRunner(
        "Transcribe and make subtitles",
        tmp_path,
        output_dir,
        10,
        10,
        Settings(),
        llm_client=object(),
        service=service,
    )

    runner._execute(
        "transcribe_audio",
        {
            "file_path": str(input_file),
            "window_seconds": 10,
            "include_segments": True,
        },
    )
    runner._execute(
        "translate_subtitles",
        {"segments_file_path": str(output_dir / "segments.srt"), "target_language": "en"},
    )
    runner._execute(
        "generate_speech",
        {"text": "Hello", "language": "bn", "output_format": "mp3"},
    )

    assert service.call_arguments == [
        ("transcribe_audio", (str(input_file), None, 10.0, True)),
        ("translate_subtitles", (str(output_dir / "segments.srt"), "en")),
        ("generate_speech", ("Hello", "bn", None, None, None, "mp3")),
    ]


def test_translate_text_recovers_invalid_path_to_sole_input_during_run(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "test.txt"
    source.write_text("A short English story.", encoding="utf-8")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, source)
    llm.responses = [
        llm._response(
            "translate_text",
            {"text_file_path": str(inputs / "not-the-input.txt"), "target_language": "hi"},
        ),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "Translation completed.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "Translate this English text into Hindi",
        source,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.call_arguments[-1][1][3] == str(source.resolve())
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    recovery = next(event for event in trace if event["event"] == "input_path_recovered")
    assert recovery["strategy"] == "sole_text_input"
    assert recovery["selected_path"]["path_sha256"] == runner._safe_arguments(
        {"text_file_path": str(source.resolve())}
    )["text_file_path"]["path_sha256"]


@pytest.mark.parametrize("missing_path", [True, False])
def test_translate_text_recovers_missing_or_invalid_path_to_sole_ocr_artifact(
    tmp_path: Path, missing_path: bool
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "worksheet.webp"
    source.write_bytes(b"mock worksheet image")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, source)
    llm.responses = [
        llm._response("ocr_document", {"file_path": str(source)}),
        llm._response(
            "translate_text",
            {
                "text_file_path": (
                    None
                    if missing_path
                    else str(output / "invented" / "guessed.md")
                ),
                "target_language": "mr",
            },
        ),
        llm._response(
            "finish_task",
            {
                "status": "completed",
                "summary": "The worksheet was translated into Marathi.",
                "assumptions": [],
            },
        ),
    ]
    runner = TaskRunner(
        "OCR this worksheet and translate the extracted text into Marathi",
        source,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["ocr_document", "translate_text"]
    expected_ocr_artifact = output / "artifacts" / "ocr.md"
    assert service.call_arguments[-1][1][3] == str(expected_ocr_artifact.resolve())
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    recovery = next(event for event in trace if event["event"] == "input_path_recovered")
    assert recovery["strategy"] == "sole_ocr_artifact"


@pytest.mark.parametrize(
    ("transcript_languages", "target_language", "expected_recovery"),
    [
        ([("te", "Telugu", "probable")], "en", True),
        (
            [("te", "Telugu", "probable"), ("te", "Telugu", "probable")],
            "en",
            False,
        ),
        ([("hi", "Devanagari", "probable")], "en", False),
        ([("te", "Telugu", "unknown")], "en", False),
        ([("te", "Telugu", "probable")], "te", False),
    ],
)
def test_translate_text_recovers_only_unique_task_language_transcript(
    tmp_path: Path,
    transcript_languages: list[tuple[str, str, str]],
    target_language: str,
    expected_recovery: bool,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "notes-one.txt").write_text("First unrelated text.", encoding="utf-8")
    (inputs / "notes-two.txt").write_text("Second unrelated text.", encoding="utf-8")
    output = tmp_path / "run"
    runner = TaskRunner(
        "Transcribe every clip, identify Telugu transcripts, and translate those into English",
        inputs,
        output,
        10,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    transcript_paths = []
    for index, (language, script, detection_status) in enumerate(transcript_languages):
        artifact = output / "artifacts" / f"transcript-{index}.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("Transcript text.", encoding="utf-8")
        transcript_paths.append(artifact.resolve())
        runner.tool_events.append({
            "step": index + 1,
            "tool": "transcribe_audio",
            "result": {
                "ok": True,
                "artifact_path": str(artifact),
                "detected_language": {
                    "language": language,
                    "script": script,
                    "status": detection_status,
                },
            },
        })

    arguments = {
        "text_file_path": str(output / "invented" / "transcript.md"),
        "target_language": target_language,
    }

    recovered, recovery = runner._recover_text_file_path(
        arguments, runner._inventory(), "translate_text"
    )

    assert (recovery is not None) is expected_recovery
    if expected_recovery:
        assert recovered["text_file_path"] == str(transcript_paths[0])
        assert recovery is not None
        assert recovery["strategy"] == "sole_language_transcript"
    else:
        assert recovered == arguments


def test_agent_translates_the_unique_telugu_transcript_after_bad_path(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    english_clip = inputs / "english.mp3"
    telugu_clip = inputs / "telugu.mp3"
    english_clip.write_bytes(b"english audio fixture")
    telugu_clip.write_bytes(b"telugu audio fixture")
    output = tmp_path / "run"

    class DetectingTranscriptionService(FakeService):
        def transcribe_audio(
            self,
            file_path: str,
            language: str | None,
            window_seconds: float,
            include_segments: bool,
        ) -> dict:
            super().transcribe_audio(
                file_path, language, window_seconds, include_segments
            )
            clip = Path(file_path)
            artifact = self.output_dir / "artifacts" / f"transcript-{clip.stem}.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"Transcript from {clip.stem}.", encoding="utf-8")
            detected_language = (
                {"language": "te", "script": "Telugu", "status": "probable"}
                if clip.stem == "telugu"
                else {"language": None, "script": None, "status": "unknown"}
            )
            return {
                "ok": True,
                "tool": "transcribe_audio",
                "artifact_path": str(artifact),
                "detected_language": detected_language,
            }

    service = DetectingTranscriptionService(output, inputs)
    llm = FakeLLM(output, english_clip)
    llm.responses = [
        llm._response("transcribe_audio", {"file_path": str(english_clip)}),
        llm._response("transcribe_audio", {"file_path": str(telugu_clip)}),
        llm._response(
            "translate_text",
            {
                "text_file_path": str(output / "wrong" / "transcript.md"),
                "target_language": "en",
            },
        ),
        llm._response(
            "finish_task",
            {
                "status": "completed",
                "summary": "Translated the Telugu transcript into English.",
                "assumptions": [],
            },
        ),
    ]
    runner = TaskRunner(
        "Transcribe every clip, identify Telugu transcripts, and translate only "
        "those into English",
        inputs,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == [
        "transcribe_audio",
        "transcribe_audio",
        "translate_text",
    ]
    expected_transcript = output / "artifacts" / "transcript-telugu.md"
    assert service.call_arguments[-1][1][3] == str(expected_transcript.resolve())
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    recovery = next(event for event in trace if event["event"] == "input_path_recovered")
    assert recovery["strategy"] == "sole_language_transcript"


def test_translate_text_recovers_by_unique_basename(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "test.txt"
    source.write_text("A short English story.", encoding="utf-8")
    (inputs / "other.md").write_text("Another input.", encoding="utf-8")
    output = tmp_path / "run"
    runner = TaskRunner(
        "Translate this English text into Hindi",
        inputs,
        output,
        5,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    arguments = {
        "text_file_path": str(inputs / "wrong-folder" / "test.txt"),
        "target_language": "hi",
    }

    recovered, recovery = runner._recover_text_file_path(
        arguments, runner._inventory(), "translate_text"
    )

    assert recovery is not None
    assert recovery["strategy"] == "unique_basename"
    assert recovered["text_file_path"] == str(source.resolve())


def test_translate_text_does_not_guess_between_multiple_inputs(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    first = inputs / "first.txt"
    second = inputs / "second.md"
    first.write_text("First source.", encoding="utf-8")
    second.write_text("Second source.", encoding="utf-8")
    output = tmp_path / "run"
    runner = TaskRunner(
        "Translate this English text into Hindi",
        inputs,
        output,
        5,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    arguments = {
        "text_file_path": str(inputs / "unknown.txt"),
        "target_language": "hi",
    }

    recovered, recovery = runner._recover_text_file_path(
        arguments, runner._inventory(), "translate_text"
    )

    assert recovered == arguments
    assert recovery is None


def test_speech_recovers_to_sole_prior_translation_for_requested_language(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    output = tmp_path / "run"
    translated = output / "artifacts" / "translation.md"
    runner = TaskRunner(
        "Translate the summary into Tamil and create Tamil speech",
        inputs,
        output,
        5,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    translated.parent.mkdir(parents=True, exist_ok=True)
    translated.write_text("Tamil translation.", encoding="utf-8")
    runner.tool_events.append({
        "step": 1,
        "tool": "translate_text",
        "result": {
            "ok": True,
            "target_language": "ta",
            "artifact_path": str(translated),
        },
    })
    arguments = {
        "text_file_path": str(output / "wrong" / "not-the-translation.md"),
        "language": "ta",
    }

    recovered, recovery = runner._recover_text_file_path(
        arguments, runner._inventory(), "generate_speech"
    )

    assert recovery is not None
    assert recovery["strategy"] == "sole_translation_artifact"
    assert recovered["text_file_path"] == str(translated.resolve())


def test_speech_does_not_guess_between_prior_translations(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    output = tmp_path / "run"
    first = output / "artifacts" / "translation-first.md"
    second = output / "artifacts" / "translation-second.md"
    runner = TaskRunner(
        "Translate into Tamil and create Tamil speech",
        inputs,
        output,
        5,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("First translation.", encoding="utf-8")
    second.write_text("Second translation.", encoding="utf-8")
    for artifact in (first, second):
        runner.tool_events.append({
            "step": len(runner.tool_events) + 1,
            "tool": "translate_text",
            "result": {
                "ok": True,
                "target_language": "ta",
                "artifact_path": str(artifact),
            },
        })
    arguments = {"text_file_path": str(output / "unknown.md"), "language": "ta"}

    recovered, recovery = runner._recover_text_file_path(
        arguments, runner._inventory(), "generate_speech"
    )

    assert recovered == arguments
    assert recovery is None


def test_agent_recovers_bad_speech_path_to_successful_translation_artifact(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "source.txt"
    source.write_text("A short English summary.", encoding="utf-8")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, source)
    llm.responses = [
        llm._response(
            "translate_text",
            {"text_file_path": str(inputs / "wrong" / "source.txt"), "target_language": "ta"},
        ),
        llm._response(
            "generate_speech",
            {"text_file_path": str(output / "wrong" / "translation.md"), "language": "ta"},
        ),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "Tamil speech generated.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "Translate this English text into Tamil and create Tamil speech",
        source,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    runner.run()

    translated = output / "artifacts" / "translation.md"
    assert service.call_arguments[-1][0] == "generate_speech"
    assert service.call_arguments[-1][1][4] == str(translated)
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    recovery = next(
        event
        for event in trace
        if event["event"] == "input_path_recovered"
        and event["tool"] == "generate_speech"
    )
    assert recovery["strategy"] == "unique_basename"


def test_llm_retry_delay_prefers_provider_retry_hint() -> None:
    response = httpx.Response(
        429,
        headers={"x-ratelimit-reset-tokens": "10s"},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    error = RateLimitError(
        "Rate limit. Please try again in 67.5ms.",
        response=response,
        body={"error": "rate_limit_exceeded"},
    )

    assert TaskRunner._llm_retry_delay(error, 0) == pytest.approx(0.0675)


def test_groq_duration_parser_handles_provider_reset_formats() -> None:
    assert _parse_duration("2m59.56s") == pytest.approx(179.56)
    assert _parse_duration("67.5ms") == pytest.approx(0.0675)


def test_llm_retry_delay_honors_full_token_reset_instead_of_truncating() -> None:
    response = httpx.Response(
        429,
        headers={"x-ratelimit-reset-tokens": "45s"},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    error = RateLimitError(
        "Token rate limit exceeded.",
        response=response,
        body={"error": {"message": "Token rate limit exceeded."}},
    )

    assert TaskRunner._llm_retry_delay(error, 0) == pytest.approx(45)
    assert GroqRateLimitController.limit_dimension(error) == "tokens_per_minute"


def test_daily_rate_limit_is_not_retried_as_a_short_window_limit() -> None:
    response = httpx.Response(
        429,
        headers={
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "1h",
        },
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    error = RateLimitError(
        "Daily request limit exceeded.",
        response=response,
        body={"error": {"message": "Daily request limit exceeded."}},
    )

    assert GroqRateLimitController.is_daily_limit(error)
    assert GroqRateLimitController.limit_dimension(error) == "requests_per_day"


def test_short_window_request_limit_uses_retry_after_hint() -> None:
    response = httpx.Response(
        429,
        headers={"retry-after": "3"},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    error = RateLimitError(
        "Requests per minute limit exceeded.",
        response=response,
        body={"error": {"code": "rate_limit_exceeded", "message": "Requests per minute limit exceeded."}},
    )

    assert not GroqRateLimitController.is_daily_limit(error)
    assert GroqRateLimitController.limit_dimension(error) == "requests_per_minute"
    assert TaskRunner._llm_retry_delay(error, 0) == pytest.approx(3)


def test_groq_limiter_paces_estimated_tokens_over_a_rolling_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    sleeps: list[float] = []
    monkeypatch.setattr("bodhan_agent.runner.time.monotonic", lambda: now[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("bodhan_agent.runner.time.sleep", fake_sleep)
    limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=1000)
    limiter.record_request(700)

    waited = limiter.wait_turn(200)

    assert waited == pytest.approx(60.05)
    assert sleeps == [pytest.approx(60.05)]


def test_groq_limiter_does_not_charge_rejected_400_to_local_token_window() -> None:
    limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=8000)
    request_index = limiter.record_request(4256, 4000, 256)
    limiter.reconcile_failed_request(request_index, httpx.Headers(), 400)
    waits: list[float] = []

    class WaitProbe:
        def wait(self, seconds: float) -> bool:
            waits.append(seconds)
            return True

    limiter.wait_turn(4256, 4000, 256, WaitProbe())  # type: ignore[arg-type]

    assert waits == []
    assert limiter.requests_last_minute == 1


def test_groq_limiter_calibrates_estimates_from_reported_usage() -> None:
    limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=8000)
    messages = [{"role": "user", "content": "short OCR result"}]
    tools: list[dict[str, object]] = []
    estimate = limiter.estimate_request_input_tokens(messages, tools)
    request_index = limiter.record_request(estimate + 256, estimate, 256)

    limiter.observe(
        httpx.Headers(),
        SimpleNamespace(prompt_tokens=max(1, estimate // 2), completion_tokens=32),
        request_index,
    )

    assert limiter.estimate_request_input_tokens(messages, tools) < estimate


def test_groq_limiter_uses_only_the_interruptible_wait_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    waits: list[float] = []
    monkeypatch.setattr("bodhan_agent.runner.time.monotonic", lambda: now[0])

    class NotCancelled:
        def wait(self, seconds: float) -> bool:
            waits.append(seconds)
            now[0] += seconds
            return False

    monkeypatch.setattr(
        "bodhan_agent.runner.time.sleep",
        lambda _: pytest.fail("A cancellable wait must not sleep a second time."),
    )
    limiter = GroqRateLimitController(requests_per_minute=1, tokens_per_minute=None)
    limiter.record_request(1)

    waited = limiter.wait_turn(1, cancel_event=NotCancelled())  # type: ignore[arg-type]

    assert len(waits) == 1
    assert waited == pytest.approx(waits[0])


def test_groq_limiter_rechecks_after_each_rolling_window_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    sleeps: list[float] = []
    monkeypatch.setattr("bodhan_agent.runner.time.monotonic", lambda: now[0])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("bodhan_agent.runner.time.sleep", fake_sleep)
    limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=1000)
    limiter.record_request(300)
    now[0] = 20
    limiter.record_request(300)
    now[0] = 40
    limiter.record_request(300)

    waited = limiter.wait_turn(300)

    assert waited == pytest.approx(40.05)
    assert sleeps == [pytest.approx(20.05), pytest.approx(20)]


def test_groq_limiter_uses_live_headers_and_stops_on_daily_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr("bodhan_agent.runner.time.monotonic", lambda: now[0])
    monkeypatch.setattr("bodhan_agent.runner.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=8000)
    request_index = limiter.record_request(500)

    state = limiter.observe(
        httpx.Headers({
            "x-ratelimit-remaining-tokens": "100",
            "x-ratelimit-reset-tokens": "10s",
            "x-ratelimit-remaining-requests": "0",
        }),
        SimpleNamespace(prompt_tokens=20, completion_tokens=5),
        request_index,
    )

    assert state == {"remaining_tokens": 100, "remaining_daily_requests": 0}
    with pytest.raises(RuntimeError, match="daily requests"):
        limiter.wait_turn(100)

    window_limiter = GroqRateLimitController(requests_per_minute=30, tokens_per_minute=8000)
    window_limiter.observe(
        httpx.Headers({
            "x-ratelimit-remaining-tokens": "100",
            "x-ratelimit-reset-tokens": "10s",
            "x-ratelimit-remaining-requests": "100",
        })
    )
    assert window_limiter.wait_turn(1000, 750, 250) == pytest.approx(10.05)


def test_agent_executes_a_multistep_tool_workflow(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, worksheet)
    runner = TaskRunner(
        "OCR and translate this worksheet into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["ocr_document", "translate_text"]
    assert all(options["tool_choice"] == "required" for options in llm.request_options)
    assert all(options["parallel_tool_calls"] is False for options in llm.request_options)
    assert all("reasoning_effort" not in options for options in llm.request_options)
    assert report["steps"] == 2
    assert (output / "result.md").is_file()
    assert (output / "trace.jsonl").is_file()
    assert (output / "report.json").is_file()
    assert (output / "artifacts").is_dir()
    report_data = json.loads((output / "report.json").read_text(encoding="utf-8"))
    report_schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "report.schema.json").read_text(encoding="utf-8")
    )
    validate(report_data, report_schema)
    assert report_data["run_id"]
    assert report_data["task"] == "OCR and translate this worksheet into Tamil"
    assert report_data["actual_cost_inr"] is None
    assert report_data["estimated_cost_inr"] == report_data["cost_inr"]
    assert report_data["api_call_count"] == 3
    assert report_data["tool_invocation_count"] == 2
    assert report_data["provider_submission_count"] == 0
    assert report_data["llm_request_count"] == 3
    assert report_data["cache_hit_count"] == 0
    assert report_data["estimated_charges_inr"] == report_data["estimated_cost_inr"]
    assert report_data["started_at"] and report_data["finished_at"]
    trace_events = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(event["run_id"] == report_data["run_id"] for event in trace_events)
    assert all(event["event_id"] for event in trace_events)
    assert any(event["event"] == "plan_decision" for event in trace_events)


def test_summary_task_reprompts_when_translation_is_only_the_title(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "chapter.pdf"
    source.write_bytes(b"test PDF placeholder")
    output = tmp_path / "run"

    class LongTextOCRService(FakeService):
        def ocr_document(self, file_path: str, table_format: str = "html") -> dict:
            result = super().ocr_document(file_path, table_format)
            result["text"] = "Aarav helps the lost teacher during a storm. " * 30
            return result

    service = LongTextOCRService(output, inputs)
    llm = FakeLLM(output, source)
    summary = (
        "Aarav guides a frightened teacher through a storm to safety using his lantern. "
        "The villagers later repair their lights and learn to share them with neighbors."
    )
    llm.responses = [
        llm._response("ocr_document", {"file_path": str(source)}),
        llm._response(
            "translate_text",
            {"text": "The Little Lantern", "target_language": "ta"},
        ),
        llm._response(
            "translate_text",
            {"text": summary, "target_language": "ta"},
        ),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "Summary translated.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "OCR and summarize this chapter, then translate the summary into Tamil",
        source,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    runner.run()

    assert service.calls == ["ocr_document", "translate_text"]
    assert service.call_arguments[-1][1][0] == summary
    saved_summary = (output / "artifacts" / "english-summary.md").read_text(
        encoding="utf-8"
    )
    assert saved_summary.strip() == summary
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rejected = next(
        event
        for event in trace
        if event["event"] == "tool_result"
        and event.get("summary", {}).get("error", {}).get("code") == "summary_too_short"
    )
    assert rejected["ok"] is False
    assert service.calls.count("translate_text") == 1
    correction_prompt = json.dumps(llm.request_options[2]["messages"])
    assert "main content, key events, and conclusion" in correction_prompt


def test_summary_task_stops_if_planner_repeats_title_only(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "chapter.pdf"
    source.write_bytes(b"test PDF placeholder")
    output = tmp_path / "run"

    class LongTextOCRService(FakeService):
        def ocr_document(self, file_path: str, table_format: str = "html") -> dict:
            result = super().ocr_document(file_path, table_format)
            result["text"] = "Aarav helps the lost teacher during a storm. " * 30
            return result

    service = LongTextOCRService(output, inputs)
    llm = FakeLLM(output, source)
    llm.responses = [
        llm._response("ocr_document", {"file_path": str(source)}),
        llm._response(
            "translate_text",
            {"text": "The Little Lantern", "target_language": "ta"},
        ),
        llm._response(
            "translate_text",
            {"text": "The Little Lantern", "target_language": "ta"},
        ),
    ]
    runner = TaskRunner(
        "OCR and summarize this chapter, then translate the summary into Tamil",
        source,
        output,
        5,
        5.0,
        Settings(
            llm_api_key="not-used-in-test",
            llm_base_url="https://test-provider.invalid/v1",
        ),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "partial"
    assert service.calls == ["ocr_document"]
    assert not (output / "artifacts" / "english-summary.md").exists()
    trace = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event["event"] == "stop" and event["reason"] == "summary_too_short"
        for event in trace
    )


def test_agent_reuses_exact_transcription_artifact_for_translation(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    audio = inputs / "clip.mp3"
    audio.write_bytes(b"mock audio")
    output = tmp_path / "run"
    transcript_artifact = output / "artifacts" / "transcript.md"

    class TranscriptionArtifactService(FakeService):
        def transcribe_audio(
            self,
            file_path: str,
            language: str | None,
            window_seconds: float,
            include_segments: bool,
        ) -> dict:
            del file_path, language, window_seconds, include_segments
            self.calls.append("transcribe_audio")
            transcript_artifact.parent.mkdir(parents=True, exist_ok=True)
            transcript_artifact.write_text("తెలుగు transcript", encoding="utf-8")
            return {
                "ok": True,
                "tool": "transcribe_audio",
                "transcript": "తెలుగు transcript",
                "artifact_path": str(transcript_artifact),
                "cost_inr": 0.01,
            }

    service = TranscriptionArtifactService(output, inputs)
    llm = FakeLLM(output, audio)
    llm.responses = [
        llm._response("transcribe_audio", {"file_path": str(audio)}),
        llm._response(
            "translate_text",
            {"text_file_path": str(transcript_artifact), "target_language": "en"},
        ),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "The clip was transcribed and translated.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "Transcribe this Telugu clip and translate it into English",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["transcribe_audio", "translate_text"]
    assert service.call_arguments[-1] == ("translate_text", (None, "en", "native", str(transcript_artifact)))
    second_request_messages = llm.request_options[1]["messages"]
    progress_message = next(
        message for message in second_request_messages
        if message.get("role") == "user" and "untrusted_tool_progress" in message.get("content", "")
    )
    progress = json.loads(progress_message["content"])
    assert progress["untrusted_tool_progress"][0]["result"]["artifact_path"] == str(transcript_artifact)


def test_agent_retries_groq_rate_limit_before_continuing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    scripted_llm = FakeLLM(output, worksheet)

    class RateLimitedOnce:
        def __init__(self) -> None:
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                response = httpx.Response(
                    429,
                    headers={"retry-after": "0"},
                    request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                )
                raise RateLimitError("rate limited", response=response, body={"error": "try again"})
            return scripted_llm._create(**kwargs)

    llm = RateLimitedOnce()
    monkeypatch.setattr("bodhan_agent.runner.time.sleep", lambda _: None)
    runner = TaskRunner(
        "OCR and translate this worksheet into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert llm.calls == 4
    trace = (output / "trace.jsonl").read_text(encoding="utf-8")
    assert '"event": "llm_retry"' in trace
    assert '"reason": "rate_limited"' in trace


def test_agent_retries_malformed_groq_tool_call_without_logging_generated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    scripted_llm = FakeLLM(output, worksheet)

    class MalformedToolCallOnce:
        def __init__(self) -> None:
            self.calls = 0
            self.received_messages: list[list[dict[str, object]]] = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            self.received_messages.append(list(kwargs["messages"]))
            if self.calls == 1:
                response = httpx.Response(
                    400,
                    request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                )
                raise BadRequestError(
                    "Failed to parse tool call arguments as JSON",
                    response=response,
                    body={"error": {"code": "tool_use_failed", "failed_generation": "sensitive source text"}},
                )
            return scripted_llm._create(**kwargs)

    llm = MalformedToolCallOnce()
    monkeypatch.setattr("bodhan_agent.runner.time.sleep", lambda _: None)
    runner = TaskRunner(
        "OCR and translate this worksheet into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert llm.calls == 4
    assert "provider rejected the previous tool call" in llm.received_messages[1][-1]["content"]
    trace = (output / "trace.jsonl").read_text(encoding="utf-8")
    assert '"reason": "malformed_tool_call"' in trace
    assert "sensitive source text" not in trace


def test_agent_reuses_ocr_result_for_same_file_even_if_format_changes(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, worksheet)
    llm.responses = [
        llm._response("ocr_document", {"file_path": str(worksheet), "table_format": "markdown"}),
        llm._response("ocr_document", {"file_path": str(worksheet), "table_format": "html"}),
        llm._response(
            "translate_text",
            {"text_file_path": str(output / "artifacts" / "ocr.md"), "target_language": "ta"},
        ),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "The worksheet was translated.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "OCR and translate this worksheet into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["ocr_document", "translate_text"]
    trace = (output / "trace.jsonl").read_text(encoding="utf-8")
    assert trace.count('"event": "tool_result"') == 3
    assert "duplicate page processing was skipped" in trace
    report_data = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert len(report_data["verification"]) == 1
    assert report_data["tool_invocation_count"] == 3
    assert report_data["cache_hit_count"] == 1
    assert report_data["operation_costs"][1]["cache_hit"] is True
    assert report_data["operation_costs"][1]["provider_submissions"] is None


def test_ocr_only_task_finalizes_after_verified_output(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    scan = inputs / "scan.png"
    scan.write_bytes(b"mock image file")
    output = tmp_path / "run"
    class CountingOcrService(FakeService):
        _request_count = 0
        _retry_count = 0

        def ocr_document(self, file_path: str, table_format: str = "html") -> dict:
            self._request_count += 1
            return super().ocr_document(file_path, table_format)

    service = CountingOcrService(output, inputs)
    llm = FakeLLM(output, scan)
    llm.responses = [llm._response("ocr_document", {"file_path": str(scan)})]
    runner = TaskRunner(
        "Extract the text from this scanned image",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["ocr_document"]
    assert len(llm.request_options) == 1
    assert report["llm_request_count"] == 1
    assert report["bodhan_provider_submission_count"] == 1
    assert report["tool_invocation_count"] == 1
    assert report["cache_hit_count"] == 0
    assert report["total_retry_count"] == 0
    assert report["api_call_count"] == 2
    assert report["provider_submission_count"] == 1
    assert report["verification"][0]["name"] == "ocr_page_coverage"
    assert "artifacts/ocr.md" in report["artifacts"]
    assert (output / report["artifacts"][0]).is_file()
    trace_events = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finished = next(event for event in trace_events if event["event"] == "run_finished")
    for key in (
        "llm_request_count",
        "bodhan_provider_submission_count",
        "tool_invocation_count",
        "cache_hit_count",
        "total_retry_count",
    ):
        assert finished[key] == report[key]
    assert finished["provider_submission_count"] == 1
    assert finished["api_call_count"] == 2


def test_agent_recovers_from_http_400_after_successful_ocr_without_repeating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    scripted_llm = FakeLLM(output, worksheet)

    class BadRequestAfterOCR:
        def __init__(self) -> None:
            self.calls = 0
            self.retry_messages: list[dict[str, object]] = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 2:
                response = httpx.Response(
                    400,
                    request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                )
                raise BadRequestError(
                    "Failed to parse tool call arguments as JSON",
                    response=response,
                    body={"error": {"code": "tool_use_failed", "failed_generation": "sensitive"}},
                )
            if self.calls == 3:
                self.retry_messages = list(kwargs["messages"])
            return scripted_llm._create(**kwargs)

    llm = BadRequestAfterOCR()
    monkeypatch.setattr("bodhan_agent.runner.time.sleep", lambda _: None)
    runner = TaskRunner(
        "OCR and translate this worksheet into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    assert report["status"] == "completed"
    assert service.calls == ["ocr_document", "translate_text"]
    assert llm.calls == 4
    assert "Do not repeat a successful tool call" in llm.retry_messages[-1]["content"]


def test_agent_saves_english_summary_used_for_translation(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    llm = FakeLLM(output, worksheet)
    summary = "A concise English summary of the source."
    llm.responses = [
        llm._response("translate_text", {"text": summary, "target_language": "ta"}),
        llm._response(
            "finish_task",
            {"status": "completed", "summary": "The summary was translated.", "assumptions": []},
        ),
    ]
    runner = TaskRunner(
        "Write an English summary and translate it into Tamil",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()

    summary_path = output / "artifacts" / "english-summary.md"
    assert report["status"] == "completed"
    assert summary_path.read_text(encoding="utf-8") == summary + "\n"
    assert "artifacts/english-summary.md" in report["artifacts"]


def test_bounded_ocr_context_keeps_complete_short_page_text() -> None:
    page_text = "A" * 1882

    bounded = TaskRunner._bounded_tool_result(
        {"tool": "ocr_document", "page_results": [{"page": 1, "text": page_text}]}
    )

    assert bounded["page_results"] == [{"page": 1, "text": page_text}]
    assert "content_truncated_for_context" not in bounded


def test_bounded_transcript_context_uses_artifact_reference() -> None:
    transcript = "spoken content " * 300

    bounded = TaskRunner._bounded_tool_result(
        {
            "tool": "transcribe_audio",
            "transcript": transcript,
            "artifact_path": "C:/run/artifacts/transcript.txt",
            "segments": [{"start": 0, "end": 1, "text": transcript}],
        },
        limit=100,
    )

    assert "transcript" not in bounded
    assert "segments" not in bounded
    assert bounded["transcript_preview"] == transcript[:100]
    assert bounded["full_content_artifact"] == "C:/run/artifacts/transcript.txt"
    assert bounded["content_truncated_for_context"] is True


def test_llm_failure_after_a_completed_operation_is_reported_as_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.png"
    worksheet.write_bytes(b"mock image file")
    output = tmp_path / "run"
    service = FakeService(output, inputs)
    scripted_llm = FakeLLM(output, worksheet)

    class LLMFailsAfterFirstTool:
        def __init__(self) -> None:
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                return scripted_llm._create(**kwargs)
            response = httpx.Response(
                429,
                headers={"retry-after": "0"},
                request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
            )
            raise RateLimitError("rate limited", response=response, body={"error": "try again"})

    llm = LLMFailsAfterFirstTool()
    monkeypatch.setattr("bodhan_agent.runner.time.sleep", lambda _: None)
    runner = TaskRunner(
        "OCR this worksheet and summarize the content",
        inputs,
        output,
        5,
        5.0,
        Settings(llm_api_key="not-used-in-test", llm_base_url="https://test-provider.invalid/v1"),
        llm_client=llm,
        service=service,
    )

    report = runner.run()
    report_data = json.loads((output / "report.json").read_text(encoding="utf-8"))

    assert report["status"] == "partial"
    assert report_data["partial_completion"]["completed_steps"] == 1
    assert report_data["failed_operations"] == []
    assert report_data["api_call_count"] == 5
    assert "OCR this worksheet and summarize the content" in (output / "result.md").read_text(encoding="utf-8")
    assert Path(report_data["artifacts"][0]).name == "ocr.md"


def test_runner_refuses_to_overwrite_an_existing_run(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    worksheet = inputs / "worksheet.txt"
    worksheet.write_text("source", encoding="utf-8")
    output = tmp_path / "run"
    runner = TaskRunner(
        "Translate this document",
        inputs,
        output,
        5,
        5.0,
        Settings(),
        llm_client=object(),
        service=FakeService(output, inputs),
    )
    assert runner.trace_path.is_file()

    with pytest.raises(ToolError, match="already contains a run"):
        TaskRunner(
            "Translate this document",
            inputs,
            output,
            5,
            5.0,
            Settings(),
            llm_client=object(),
            service=FakeService(output, inputs),
        )
