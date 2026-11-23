"""Assignment CLI plus explicit offline preparation modes."""

import argparse
import json
import math
from pathlib import Path
import sys
import time

from .media import MediaError, describe, extract_audio, inspect_video, tool_path
from .vad import VADSettings, detect_speech


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Bodhan video dubbing, subtitles and audited recovery.')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--langs', required=True, help='Comma-separated production target language codes.')
    parser.add_argument('--out', required=True, type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare-only', action='store_true', help='Inspect input and extract audio; makes no model calls.')
    modes.add_argument('--detect-speech-only', action='store_true', help='Extract audio and run local Silero VAD; no hosted API calls.')
    parser.add_argument('--vad-threshold', type=float, default=0.5)
    parser.add_argument('--vad-min-speech-ms', type=int, default=150)
    parser.add_argument('--vad-min-silence-ms', type=int, default=300)
    parser.add_argument('--vad-pad-ms', type=int, default=50)
    parser.add_argument('--allow-short-input', action='store_true', help='Allow sub-minute smoke-test fixtures.')
    parser.add_argument('--ffmpeg', help='Explicit ffmpeg executable path.')
    parser.add_argument('--ffprobe', help='Explicit ffprobe executable path.')
    parser.add_argument('--media-timeout', type=float, default=120, help='Local media subprocess timeout in seconds.')
    parser.add_argument('--env-file',type=Path,help='Explicit credentials file; otherwise use environment variables.')
    parser.add_argument('--glossary',type=Path,help='YAML English term to language/preferred-term mapping.')
    parser.add_argument('--voices',type=Path,help='YAML language to documented voice name mapping.')
    parser.add_argument('--code-mixed',action='store_true',help='Use Bodhan documented code-mixed translation mode.')
    parser.add_argument('--max-cost-inr',default='50',help='Conservative total paid-call ceiling; default 50 rupees.')
    parser.add_argument('--from-run',type=Path,help='Render saved stage artifacts offline; requires --fit-manifest.')
    parser.add_argument('--fit-manifest',type=Path,help='Validated fit manifest for --from-run.')
    parser.add_argument('--cache-dir',type=Path,help='Optional persistent content cache shared across fresh output folders; never overwrites reports.')
    args = parser.parse_args(argv)
    if not math.isfinite(args.media_timeout) or args.media_timeout <= 0:
        parser.error('--media-timeout must be a positive finite number.')
    try:
        vad_settings = VADSettings(args.vad_threshold, args.vad_min_speech_ms, args.vad_min_silence_ms, args.vad_pad_ms)
    except MediaError as exc:
        parser.error(str(exc))
    languages = [code.strip() for code in args.langs.split(',') if code.strip()]
    if not languages:
        parser.error('--langs must include at least one target code.')
    if not (args.prepare_only or args.detect_speech_only):
        from .pipeline import run_pipeline
        from decimal import Decimal,InvalidOperation
        try:
            amount=Decimal(args.max_cost_inr)
            if not amount.is_finite() or amount<=0:raise InvalidOperation
            result=run_pipeline(args)
        except (InvalidOperation,MediaError,OSError) as exc:
            print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_run_configuration'),
                'message':str(exc) if isinstance(exc,MediaError) else 'Check budget, paths and permissions.'}))
            return 1
        print(json.dumps({'status':result['status'],'report':str(args.out.resolve()/'report.json'),'dubbing_complete':result['dubbing_complete']}))
        return int(not result['dubbing_complete'])
    out = args.out.expanduser().resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error('Choose a new or empty output directory; existing outputs will not be overwritten.')
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        parser.error('Cannot create output directory. Check its path and permissions.')
    report = {'status': 'preparing', 'completed_stages': [], 'requested_languages': languages, 'model_calls': 0, 'local_vad_runs': 0,
              'cost_inr': 0, 'dubbing_complete': False, 'limitations': ['Preparation/detection only: no hosted model calls, transcription, translation, synthesis, or subtitles are performed in this mode.',
              'First playable video stream and first audio stream selected; multitrack choice is not implemented.']}
    start = time.monotonic()
    exit_code = 0
    try:
        ffprobe, ffmpeg = tool_path('ffprobe', args.ffprobe), tool_path('ffmpeg', args.ffmpeg)
        info = inspect_video(args.input, ffprobe, args.media_timeout, args.allow_short_input)
        report['media'] = describe(info)
        report['completed_stages'].append('inspect')
        report['audio'] = extract_audio(info, out / 'source_audio.wav', ffmpeg, args.media_timeout)
        report['completed_stages'].append('extract_audio')
        report['status'] = 'prepared'
        if args.detect_speech_only:
            detection = detect_speech(out / 'source_audio.wav', vad_settings)
            (out / 'speech_regions.json').write_text(json.dumps(detection, indent=2) + '\n', encoding='utf-8')
            report['speech_detection'] = {'path': str(out / 'speech_regions.json'), **detection['summary'], 'warnings': detection['warnings'], 'detector': detection['detector']}
            report['completed_stages'].append('detect_speech')
            report['local_vad_runs'] = 1
            report['status'] = 'speech_detected'
    except MediaError as exc:
        report.update(status='failed', error={'code': exc.code, 'message': str(exc)})
        exit_code = 1
    except OSError:
        report.update(status='failed', error={'code': 'filesystem_error', 'message': 'Cannot access or write media files; check paths and permissions.'})
        exit_code = 1
    report['latency_seconds'] = round(time.monotonic() - start, 3)
    try:
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    except OSError:
        print('Could not write report; check output permissions.', file=sys.stderr)
        return 1
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'), 'dubbing_complete': False}))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
