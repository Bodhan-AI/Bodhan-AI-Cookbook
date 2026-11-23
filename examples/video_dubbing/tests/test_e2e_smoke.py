"""End-to-end smoke test on a 30 s clip through fully-mocked Bodhan clients.

Exercises the whole pipeline (ingest -> segment -> transcribe -> translate -> TTS/fit
-> QA -> assemble -> subtitles -> report) with no keys/credit and asserts the full
output tree + report. The sample video is generated at test time (no committed media).
"""

import asyncio
import json
from types import SimpleNamespace

from make_sample import make_sample

from bodhan_dub.config import load_settings
from bodhan_dub.pipeline.orchestrator import run


def _args(video: str, out: str) -> SimpleNamespace:
    return SimpleNamespace(
        input=video, langs="hi,ta", out=out, glossary=None, voices=None,
        code_mixed=False, mock=True, concurrency=4, asr_model="indic-transcribe",
        vad="energy", resume=False, log_level="WARNING",
    )


def test_end_to_end_mock(tmp_path):
    video = make_sample(str(tmp_path / "sample.mp4"), duration=30.0)
    out = tmp_path / "out"
    settings = load_settings(_args(video, str(out)))

    code = asyncio.run(run(settings))
    assert code == 0  # all languages succeeded

    assert (out / "report.json").is_file()
    assert (out / "transcript.en.json").is_file()
    for lang in ("hi", "ta"):
        assert (out / lang / f"video.{lang}.mp4").is_file()
        assert (out / lang / f"subtitles.{lang}.srt").is_file()
        assert (out / lang / f"segments.{lang}.json").is_file()

    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "ok"
    assert report["duration_s"] == 30.0
    for lang in ("hi", "ta"):
        qa = report["languages"][lang]["qa"]
        assert qa["segments_dubbed"] > 0
        assert qa["segments_failed"] == 0
        # every dubbed segment meets the sync + speed targets on this clean input
        assert qa["sync_within_300ms"] == qa["segments_dubbed"]
        assert qa["speed_within_band"] == qa["segments_dubbed"]

    # transcript carries real timestamps derived by the VAD (the ASR gives none)
    transcript = json.loads((out / "transcript.en.json").read_text())
    assert transcript["segments"]
    assert all("start" in s and "end" in s for s in transcript["segments"])


def test_language_failure_is_isolated(tmp_path, monkeypatch):
    """If one language's assembly blows up, others still finish and exit code is 1."""
    import bodhan_dub.pipeline.orchestrator as orch

    real_assemble = orch.assemble_language

    def flaky_assemble(bed, non_speech, dubs, video, out_mp4, *a, **k):
        if "/ta/" in out_mp4 or "\\ta\\" in out_mp4:
            raise RuntimeError("synthetic assembly failure")
        return real_assemble(bed, non_speech, dubs, video, out_mp4, *a, **k)

    monkeypatch.setattr(orch, "assemble_language", flaky_assemble)

    video = make_sample(str(tmp_path / "sample.mp4"), duration=12.0)
    out = tmp_path / "out"
    settings = load_settings(_args(video, str(out)))

    code = asyncio.run(run(settings))
    assert code == 1  # a language failed -> non-zero exit

    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["languages"]["ta"]["status"] == "failed"
    assert report["languages"]["hi"]["status"] == "ok"          # unaffected
    assert (out / "hi" / "video.hi.mp4").is_file()               # partial output written
