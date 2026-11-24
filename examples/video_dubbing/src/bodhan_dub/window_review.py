"""Review the exact exported WAV files that will become ASR requests."""
import argparse
from importlib.resources import files
import json
from pathlib import Path
import wave

from .media import MediaError
from .vad import SAMPLE_RATE


def create_window_review(run: Path) -> Path:
    run=run.expanduser().resolve()
    target=run/'review_windows.html'
    if target.exists():
        raise MediaError('output_exists','Window review already exists; refusing to overwrite.')
    try:
        plan=json.loads((run/'protected_regions.json').read_text())
        frames=plan['total_samples']
        if type(frames) is not int or not 0 < frames <= SAMPLE_RATE*900 or plan['sample_rate']!=SAMPLE_RATE:
            raise ValueError('Invalid recording length or sample rate')
        rows=[]
        cursor=0
        for index,window in enumerate(plan['asr_windows'],1):
            start,end,core_start,core_end=[window[k] for k in ['start_sample','end_sample','core_start_sample','core_end_sample']]
            if any(type(n) is not int for n in [start,end,core_start,core_end]) or not 0 <= start <= core_start < core_end <= end <= frames or core_start!=cursor or end-start>SAMPLE_RATE*30:
                raise ValueError('Invalid or over-limit ASR window')
            relative=Path('asr_windows')/f'window-{index:04d}.wav'
            path=run/relative
            if path.resolve().parent!=(run/'asr_windows').resolve():
                raise ValueError('Window escaped its expected folder')
            with wave.open(str(path),'rb') as audio:
                if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getnframes())!=(SAMPLE_RATE,1,2,end-start):
                    raise ValueError('Actual window WAV does not match its manifest')
            rows.append({'id':f'window-{index:04d}','file':relative.as_posix(),'start':start/SAMPLE_RATE,'end':end/SAMPLE_RATE,
                         'duration':(end-start)/SAMPLE_RATE,'coreStart':core_start/SAMPLE_RATE,'coreEnd':core_end/SAMPLE_RATE,
                         'cutReason':window['cut_reason']})
            cursor=core_end
        if not rows or cursor!=frames:
            raise ValueError('Window cores do not cover the recording')
        payload={'duration':frames/SAMPLE_RATE,'windows':rows}
    except (OSError,ValueError,TypeError,KeyError,wave.Error,EOFError) as exc:
        raise MediaError('invalid_window_review','Expected complete bounded window metadata and matching WAV files.') from exc
    encoded=json.dumps(payload,ensure_ascii=True).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=files('bodhan_dub.templates').joinpath('window_review.html').read_text().replace('__WINDOW_DATA__',encoded)
    with target.open('x',encoding='utf-8') as output:
        output.write(html)
    return target


def main(argv=None):
    parser=argparse.ArgumentParser(description='Generate exact ASR-window audio review; no API calls.')
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        page=create_window_review(args.run)
    except (MediaError,OSError) as exc:
        print(json.dumps({'status':'failed','message':str(exc)}))
        return 1
    print(json.dumps({'status':'created','page':str(page),'hosted_api_calls':0}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
