from tools.latency_report import (backend_line, backends, dub_report, parse, percentile, report, retries,
                                  retry_report)

LOG = """\
2026-11-25 10:00:01 INFO    app.pipeline.incoming: in #1 2.4s audio | asr 0.80s mt 0.40s | e2e 1.25s | नमस्ते -> Hello
2026-11-25 10:00:05 INFO    app.pipeline.incoming: in #2 3.0s audio | asr 1.20s mt 0.50s | e2e 1.80s | a | b -> x
2026-11-25 10:00:09 INFO    app.pipeline.outgoing: out #1 1.9s audio | asr 0.70s mt 0.30s tts 0.90s | e2e 1.95s | hi -> नमस्ते
2026-11-25 10:00:10 WARNING app.bodhan.client: transcribe failed; retry 1/6 in 0.5s
"""


def test_parse_reads_both_directions():
    data = parse(LOG.splitlines())
    assert data["in"]["asr"] == [0.8, 1.2]
    assert data["in"]["e2e"] == [1.25, 1.8]
    assert "tts" not in data["in"]
    assert data["out"]["tts"] == [0.9]


def test_report_mentions_each_direction():
    text = report(parse(LOG.splitlines()))
    assert "Subtitles (they speak): 2 utterances" in text
    assert "Interpreting (you speak): 1 utterances" in text


def test_partial_stage_is_read_when_present():
    line = ("2026-11-26 10:00:01 INFO    app.pipeline.incoming: in #2 2.0s audio | asr 0.22s mt 0.22s "
            "| e2e 0.44s | partial 0.23s | continues #1 | a b -> x")
    data = parse([line])
    assert data["in"]["partial"] == [0.23]
    assert data["in"]["e2e"] == [0.44]


def test_backend_is_reported_and_mixed_runs_flagged():
    hosted = "2026-11-26 20:51:20 INFO    app: Speech recognition: Bodhan hosted Indic-Transcribe"
    local = "2026-11-26 20:48:49 INFO    app: Speech recognition: local Indic-Transcribe from models/x"
    assert backend_line(backends([hosted, hosted])) == "Speech recognition: hosted"
    assert backend_line(backends([local])) == "Speech recognition: local"
    assert "MIXED" in backend_line(backends([hosted, local]))
    assert "not logged" in backend_line(backends(["nothing"]))


def test_dub_report():
    log = [
        "INFO app.pipeline.dubbing: dub #1 | speech 3.03s | heard 3.5s after they finished | 1 part(s) | Hi",
        "INFO app.pipeline.dubbing: dub #2 | speech 6.44s | heard 6.9s after they finished | 2 part(s) | Bye",
        "INFO app.pipeline.dubbing: Dub of #3 skipped: 16.7s behind by its turn",
        "INFO app.pipeline.dubbing: Dub of #4 skipped: keeping speech requests for your interpreting",
    ]
    text = dub_report(log)
    assert "2 lines spoken" in text and "median 5.2s" in text
    assert "1 rate limit" in text and "1 too far behind" in text
    assert dub_report(["nothing"]) == ""


def test_empty_log():
    assert "No utterance lines" in report(parse(["nothing here"]))


RETRY_LOG = """\
2026-11-26 10:00:10 WARNING app.bodhan.client: speak failed ([429 rate_limit_exceeded_error] Maximum rate limit reached for the model); retry 1/6 in 46.0s
2026-11-26 10:00:56 WARNING app.bodhan.client: speak failed ([429 rate_limit_exceeded_error] Maximum rate limit reached for the model); retry 2/6 in 12.0s
2026-11-26 10:01:00 WARNING app.bodhan.client: transcribe failed ([0 network_error] ReadTimeout('')); retry 1/6 in 0.4s
"""


def test_retries_counted_per_endpoint_and_status():
    found = retries(RETRY_LOG.splitlines())
    assert found[("speak", "429 rate_limit_exceeded_error")] == 2
    assert found[("transcribe", "0 network_error")] == 1


def test_retry_report_flags_rate_limited_endpoint():
    text = retry_report(retries(RETRY_LOG.splitlines()))
    assert "BODHAN_SPEAK_RPM" in text
    assert "BODHAN_TRANSCRIBE_RPM" not in text
    assert "none" in retry_report(retries(LOG.splitlines()))


def test_percentile():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert percentile([4.0], 90) == 4.0
