"""Process one call from the terminal and write the same JSON/CSV files the app exports.

    python cli.py process call.mp3 --out ./output --language hi --ticket-id TKT-1 --agent-name Agent-07

Exit status: 0 completed, 2 partly done (some pieces failed), 1 failed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from app import audio
from app.analysis import build_analyzer
from app.bodhan_client import BodhanClient
from app.config import ALLOWED_EXTENSIONS, get_settings
from app.diarization import build_diarizer
from app.exporters import export
from app.jobs import new_call_id
from app.languages import is_supported
from app.models import CallStatus, Progress
from app.pipeline import CallInput, PipelineError, process_call


def _print_progress(p: Progress) -> None:
    print(f"\r  {p.message:<40}", end="" if p.stage.value != "done" else "\n", file=sys.stderr, flush=True)


async def _process(args: argparse.Namespace) -> int:
    settings = get_settings()
    src: Path = args.audio
    if not src.is_file():
        print(f"error: {src} does not exist", file=sys.stderr)
        return 1
    if args.language and not is_supported(args.language):
        print(f"error: language '{args.language}' is not supported", file=sys.stderr)
        return 1

    audio.validate_upload(
        src, src.name, allowed_extensions=ALLOWED_EXTENSIONS, max_bytes=settings.max_file_mb * 1_000_000
    )
    probed = audio.probe(src, settings.ffprobe_path)
    if probed.duration is not None:
        audio.check_duration(probed.duration, settings.max_duration_seconds)

    client = BodhanClient(
        settings.transcribe_key,
        settings.translate_key,
        base_url=settings.bodhan_base_url,
        timeout=settings.bodhan_timeout_seconds,
        max_retries=settings.bodhan_max_retries,
        concurrency=settings.bodhan_concurrency,
        rpm=settings.bodhan_rpm,
    )
    diarizer = build_diarizer(settings)
    analyzer = build_analyzer(settings)
    workdir = Path(tempfile.mkdtemp(prefix="scs_cli_"))
    call = CallInput(
        call_id=new_call_id(),
        audio_path=src,
        source_file=src.name,
        language=args.language,
        ticket_id=args.ticket_id,
        agent_name=args.agent_name,
    )
    print(f"Processing {src.name} (call_id {call.call_id})", file=sys.stderr)
    try:
        record = await process_call(
            call,
            client=client,
            settings=settings,
            workdir=workdir,
            diarizer=diarizer,
            analyzer=analyzer,
            on_progress=_print_progress,
        )
    except PipelineError as exc:
        print(f"\nerror: {exc.message}", file=sys.stderr)
        if exc.record is None:
            return 1
        record = exc.record
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        await client.aclose()
        if diarizer is not None:
            await diarizer.aclose()
        if analyzer is not None:
            await analyzer.aclose()

    args.out.mkdir(parents=True, exist_ok=True)
    for fmt in ("json", "calls_csv", "segments_csv"):
        body, _media, name = export(record, fmt)
        path = args.out / name.replace(record.call_id, src.stem, 1)
        path.write_bytes(body)
        print(f"  wrote {path}", file=sys.stderr)
    if record.analysis is not None:
        print(f"  summary: {record.analysis.summary}", file=sys.stderr)
        print(f"  sentiment: {record.analysis.overall_sentiment.value} ({record.analysis.sentiment_score:+.2f})",
              file=sys.stderr)  # fmt: skip
    for warning in record.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    return {CallStatus.completed: 0, CallStatus.partial: 2}.get(record.status, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Support Call Summarizer (Bodhan Speech-to-Text + Translation, Sarvam summary)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process", help="transcribe and translate one call")
    p.add_argument("audio", type=Path, help="call recording (" + ", ".join(ALLOWED_EXTENSIONS) + ")")
    p.add_argument("--out", type=Path, default=Path("../outputs"), help="folder for the JSON and CSV files")
    p.add_argument("--language", help="language code of the call, e.g. hi, ta (recommended)")
    p.add_argument("--ticket-id")
    p.add_argument("--agent-name")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(_process(args))
    except audio.AudioError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
