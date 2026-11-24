"""Protect VAD groups and construct coverage-preserving ASR input windows."""
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import wave

from .media import MediaError
from .vad import SAMPLE_RATE, timeline_regions


@dataclass(frozen=True)
class BoundarySettings:
    context_ms: int = 500
    target_core_ms: int = 25000
    max_window_ms: int = 30000
    minimum_pause_ms: int = 300

    def __post_init__(self):
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise MediaError('invalid_boundary_settings', 'Boundary durations must be integer milliseconds.')
        if not 0 < self.context_ms <= 2000 or not 0 < self.minimum_pause_ms <= 5000:
            raise MediaError('invalid_boundary_settings', 'Use positive context <=2000 ms and a positive pause <=5000 ms.')
        if not 0 < self.target_core_ms <= self.max_window_ms - 2*self.context_ms or not self.max_window_ms <= 30000:
            raise MediaError('invalid_boundary_settings', 'Core plus both context margins must fit a positive <=30-second window.')


def boundary_plan(frames: int, raw_regions: list[dict], settings: BoundarySettings | None = None) -> dict:
    settings = settings or BoundarySettings()
    if type(frames) is not int or not 0 < frames <= SAMPLE_RATE*900:
        raise MediaError('invalid_boundary_audio', 'Expected nonempty audio of at most 15 minutes.')
    try:
        raw, raw_gaps = timeline_regions([{'start':r['start_sample'],'end':r['end_sample']} for r in raw_regions], frames)
    except (KeyError,TypeError) as exc:
        raise MediaError('invalid_boundary_data','Invalid detector sample ranges.') from exc
    margin = settings.context_ms*SAMPLE_RATE//1000
    protected=[]
    for region in raw:
        start,end=max(0,region['start_sample']-margin),min(frames,region['end_sample']+margin)
        if protected and start <= protected[-1]['end']:
            protected[-1]['end']=max(protected[-1]['end'],end)
            protected[-1]['raw_ids'].append(region['id'])
        else:
            protected.append({'start':start,'end':end,'raw_ids':[region['id']]})
    speech,gaps=timeline_regions(protected,frames)
    for region, origin in zip(speech,protected):
        region['source_region_ids']=origin['raw_ids']
    max_frames=settings.max_window_ms*SAMPLE_RATE//1000
    target=settings.target_core_ms*SAMPLE_RATE//1000
    pause=settings.minimum_pause_ms*SAMPLE_RATE//1000
    windows=[]
    cursor=0
    while cursor < frames:
        upper=min(frames,cursor+max_frames-min(margin,cursor)-margin)
        preferred=min(frames,cursor+target)
        if upper==frames:
            cut,reason=frames,'end_of_recording'
        else:
            candidates=[]
            for gap in raw_gaps:
                if gap['end_sample']-gap['start_sample'] < pause:
                    continue
                left=max(cursor+SAMPLE_RATE, gap['start_sample']+pause//2)
                right=min(upper,gap['end_sample']-pause//2)
                if left<=right:
                    candidates.append(min(max(preferred,left),right))
            cut=min(candidates,key=lambda c:abs(c-preferred)) if candidates else preferred
            reason='vad_pause' if candidates else 'forced_with_overlap'
        begin,end=max(0,cursor-margin),min(frames,cut+margin)
        if not cursor<cut or end-begin>max_frames:
            raise MediaError('invalid_boundary_plan','Could not construct bounded advancing windows.')
        windows.append({'id':f'window-{len(windows)+1:04d}', 'start_sample':begin,'end_sample':end,
                        'start_seconds':begin/SAMPLE_RATE,'end_seconds':end/SAMPLE_RATE,
                        'core_start_sample':cursor,'core_end_sample':cut,
                        'core_start_seconds':cursor/SAMPLE_RATE,'core_end_seconds':cut/SAMPLE_RATE,
                        'cut_reason':reason,'overlap_requires_text_reconciliation':cursor>0,
                        'contains_vad_speech':any(r['start_sample']<cut and r['end_sample']>cursor for r in raw)})
        cursor=cut
    return {'schema_version':1,'sample_rate':SAMPLE_RATE,'total_samples':frames,'duration_seconds':frames/SAMPLE_RATE,
            'settings':asdict(settings),'speech_regions':speech,'non_speech_regions':gaps,'asr_windows':windows,
            'summary':{'raw_region_count':len(raw),'protected_group_count':len(speech),'window_count':len(windows),
                       'forced_cut_count':sum(w['cut_reason']=='forced_with_overlap' for w in windows),
                       'source_samples_preserved':sum(w['core_end_sample']-w['core_start_sample'] for w in windows),
                       'source_coverage_complete':True},
            'limitations':['Protected groups contain automatic safety context; they are not word-aligned speech boundaries.',
                          'Every source sample is retained in window cores, including VAD gaps; false-negative speech is not discarded.',
                          'Overlap text reconciliation and model transcription are not implemented; do not concatenate future window transcripts blindly.',
                          'Continuous speech may need forced cuts with overlap. Perfect sentence or word boundaries are not guaranteed.']}


def prepare_boundaries(run: Path, settings: BoundarySettings | None = None) -> dict:
    run=run.expanduser().resolve()
    manifest=run/'protected_regions.json'
    window_dir=run/'asr_windows'
    if manifest.exists() or window_dir.exists():
        raise MediaError('output_exists','Protected regions or ASR windows already exist; refusing to overwrite.')
    try:
        data=json.loads((run/'speech_regions.json').read_text())
        if data['sample_rate'] != SAMPLE_RATE:
            raise MediaError('invalid_boundary_audio','Expected prepared 16 kHz audio.')
        with wave.open(str(run/'source_audio.wav'),'rb') as source:
            if (source.getframerate(),source.getnchannels(),source.getsampwidth(),source.getnframes()) != (SAMPLE_RATE,1,2,data['total_samples']):
                raise MediaError('invalid_boundary_audio','Source WAV does not match the detector metadata.')
            plan=boundary_plan(data['total_samples'],data['speech_regions'],settings)
            window_dir.mkdir()
            for window in plan['asr_windows']:
                source.setpos(window['start_sample'])
                samples=window['end_sample']-window['start_sample']
                pcm=source.readframes(samples)
                if len(pcm)!=samples*2:
                    raise MediaError('invalid_boundary_audio','Source WAV is truncated; window export stopped.')
                path=window_dir/(window['id']+'.wav')
                with wave.open(str(path),'wb') as output:
                    output.setparams((1,2,SAMPLE_RATE,0,'NONE','not compressed'))
                    output.writeframes(pcm)
                window['path']=str(path.relative_to(run))
        manifest.write_text(json.dumps(plan,indent=2)+'\n')
        return plan
    except MediaError:
        raise
    except (OSError,ValueError,KeyError,TypeError,wave.Error,EOFError) as exc:
        raise MediaError('boundary_preparation_failed','Cannot prepare protected regions/windows from this detection run.') from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description='Build protected speech groups and lossless ASR windows; no model calls.')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--context-ms',type=int,default=500)
    args=parser.parse_args(argv)
    try:
        plan=prepare_boundaries(args.run,BoundarySettings(context_ms=args.context_ms))
    except MediaError as exc:
        print(json.dumps({'status':'failed','code':exc.code,'message':str(exc)}))
        return 1
    print(json.dumps({'status':'prepared','summary':plan['summary'],'hosted_api_calls':0}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
