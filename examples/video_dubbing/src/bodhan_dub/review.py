"""Generate an offline listening-review page from existing detection artifacts."""

import argparse
from importlib.resources import files
import json
from pathlib import Path
import wave

from .media import MediaError, run_media, tool_path
from .vad import SAMPLE_RATE, timeline_regions


def create_review(run: Path, protected: bool = False) -> Path:
    run = run.expanduser().resolve()
    target = run / ('review_protected.html' if protected else 'review.html')
    if target.exists():
        raise MediaError('output_exists', 'review.html already exists; keep it or remove it explicitly before regenerating.')
    try:
        data_path = run / ('protected_regions.json' if protected else 'speech_regions.json')
        if data_path.stat().st_size > 2_000_000:
            raise MediaError('invalid_review_data', 'Detection metadata is too large for this local review tool.')
        data = json.loads(data_path.read_text(encoding='utf-8'))
        frames = data['total_samples']
        if type(frames) is not int or not 0 < frames <= SAMPLE_RATE * 900 or data['sample_rate'] != SAMPLE_RATE:
            raise MediaError('invalid_review_data', 'Expected positive <=15-minute 16 kHz detection data.')
        # Derive display timing from integer offsets, not possibly edited seconds.
        spans = [{'start': row['start_sample'], 'end': row['end_sample']} for row in data['speech_regions']]
        speech, gaps = timeline_regions(spans, frames)
        if protected:
            for canonical, original in zip(speech, data['speech_regions']):
                canonical['source_region_ids'] = original.get('source_region_ids', [])
        with wave.open(str(run / 'source_audio.wav'), 'rb') as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) != (SAMPLE_RATE, 1, 2, frames):
                raise MediaError('invalid_review_audio', 'Audio format/duration does not match detection data.')
    except MediaError:
        raise
    except (OSError, ValueError, KeyError, TypeError, wave.Error, EOFError) as exc:
        raise MediaError('invalid_review_data', 'Expected readable speech_regions.json and matching source_audio.wav in the run folder.') from exc
    playback = run / 'review_audio.mp3'
    if playback.exists() and not protected:
        raise MediaError('output_exists', 'Playback MP3 already exists; no media was overwritten.')
    if not playback.exists():
        run_media([tool_path('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
               '-i', str(run / 'source_audio.wav'), '-c:a', 'libmp3lame', '-q:a', '4', '-ar', '24000', str(playback)], 120)
    payload = {'mode': 'protected' if protected else 'raw', 'protectionContextMs': data.get('settings', {}).get('context_ms', 500) if protected else 0, 'duration': frames / SAMPLE_RATE, 'speech': speech, 'gaps': gaps,
               'speechSeconds': sum(r['end_sample']-r['start_sample'] for r in speech)/SAMPLE_RATE}
    # JSON is placed in a script element. Escaping '<' prevents closing that
    # element through metadata text; the UI uses textContent for dynamic text.
    encoded = json.dumps(payload, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = files('bodhan_dub.templates').joinpath('review.html').read_text(encoding='utf-8')
    page = template.replace('__REVIEW_DATA__', encoded)
    try:
        with target.open('x', encoding='utf-8') as output:
            output.write(page)
    except FileExistsError as exc:
        raise MediaError('output_exists', 'Review already exists; no file was overwritten.') from exc
    except OSError as exc:
        raise MediaError('review_write_failed', 'Cannot write the review page; check folder permissions.') from exc
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Create a local clickable audio review; no model calls.')
    parser.add_argument('--run', type=Path, required=True, help='Existing detection-output folder.')
    parser.add_argument('--protected', action='store_true', help='Review automatic protected groups, preserving raw detection data.')
    args = parser.parse_args(argv)
    try:
        path = create_review(args.run, args.protected)
    except MediaError as exc:
        print(json.dumps({'status': 'failed', 'code': exc.code, 'message': str(exc)}))
        return 1
    print(json.dumps({'status': 'created', 'page': str(path), 'hosted_api_calls': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
