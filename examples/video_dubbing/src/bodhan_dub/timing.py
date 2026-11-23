"""Estimated source placement from provenance and VAD; never word alignment."""
import math

from .media import MediaError


def subtract_gaps(start,end,gaps):
    intervals=[];cursor=start
    for a,b in sorted(gaps):
        if b<=cursor or a>=end:continue
        if a>cursor:intervals.append((cursor,min(a,end)))
        cursor=max(cursor,b)
    if cursor<end:intervals.append((cursor,end))
    return [(a,b) for a,b in intervals if b-a>1e-6]


def position(intervals,fraction):
    if not intervals:raise MediaError('no_source_speech','Source interval contains no detected speech.')
    lengths=[b-a for a,b in intervals];target=max(0,min(1,fraction))*sum(lengths)
    for (a,b),length in zip(intervals,lengths):
        if target<=length:return a+target
        target-=length
    return intervals[-1][1]


def source_timeline(units,windows,detector,long_pause=1.):
    duration=detector['total_samples']/detector['sample_rate']
    if not math.isfinite(duration) or not 0<duration<=900:raise MediaError('invalid_source_timeline','Invalid source duration.')
    speech=[(r['start_sample']/detector['sample_rate'],r['end_sample']/detector['sample_rate']) for r in detector['speech_regions']]
    gaps=[(r['start_sample']/detector['sample_rate'],r['end_sample']/detector['sample_rate']) for r in detector['non_speech_regions']
          if (r['end_sample']-r['start_sample'])/detector['sample_rate']>=long_pause]
    lookup={w['id']:w for w in windows};rows=[]
    def point(span,end=False):
        w=lookup[span['window_id']];tokens=w['tokens'];index=span['end'] if end else span['start']
        if type(index) is not int or not 0<=span['start']<span['end']<=len(tokens):raise ValueError
        weights=[max(1,len(t)) for t in tokens]
        intervals=[(max(a,w['start_seconds']),min(b,w['end_seconds'])) for a,b in speech if a<w['end_seconds'] and b>w['start_seconds']]
        # Words are distributed over detected speech in their ASR window.
        # This is an explicit approximation, not forced alignment.
        if not intervals:intervals=[(w['start_seconds'],w['end_seconds'])]
        return position(intervals,sum(weights[:index])/sum(weights))
    try:
        for u in units:
            spans=u['source_spans'];start=point(spans[0]);end=point(spans[-1],True)
            if not 0<=start<end<=duration:raise ValueError
            rows.append({'id':u['id'],'source_text':u['text'],'start_seconds':start,'end_seconds':end,
                         'source_window_envelope':u['source_window_envelope'],'timing_resolution':'estimated_from_source_tokens_and_vad',
                         'sync_within_300ms_verified':False,'source_spans':spans})
        for a,b in zip(rows,rows[1:]):
            left=lookup[a['source_spans'][-1]['window_id']]
            right=lookup[b['source_spans'][0]['window_id']]
            if left['id']!=right['id']:
                candidates=[(x,y) for x,y in gaps if left['start_seconds']<=x<=left['end_seconds']
                            and right['start_seconds']<=y<=right['end_seconds']
                            and a['start_seconds']<x<y<b['end_seconds']]
                if candidates:
                    x,y=min(candidates,key=lambda g:abs((g[0]+g[1])-(a['end_seconds']+b['start_seconds'])))
                    a['end_seconds']=x;b['start_seconds']=y
                    a['end_boundary_note']='Snapped to observed long VAD gap between distinct source-window spans.'
                    b['start_boundary_note']='Snapped to observed long VAD gap between distinct source-window spans.'
            if b['start_seconds']<a['start_seconds'] or b['end_seconds']<=a['end_seconds']:raise ValueError
            if a['end_seconds']>b['start_seconds']:
                boundary=(a['end_seconds']+b['start_seconds'])/2
                a['end_seconds']=boundary;b['start_seconds']=boundary
        for r in rows:
            r['speech_slots']=[{'start_seconds':a,'end_seconds':b} for a,b in subtract_gaps(r['start_seconds'],r['end_seconds'],gaps)]
            r['available_seconds']=sum(s['end_seconds']-s['start_seconds'] for s in r['speech_slots'])
        if len({r['id'] for r in rows})!=len(rows):raise ValueError
    except (KeyError,TypeError,ValueError,ZeroDivisionError,IndexError) as exc:
        raise MediaError('invalid_source_timeline','Source references cannot produce an ordered estimated timeline.') from exc
    return {'duration_seconds':duration,'units':rows,'protected_gaps':[{'start_seconds':a,'end_seconds':b} for a,b in gaps],
            'method':'Character-weighted token interpolation over VAD activity, reconciled into non-overlapping unit bounds.',
            'limitations':['Estimated timing only; no word timestamps or acoustic forced alignment. The ±300 ms sync target is not verified.',
                          'Long VAD gaps are protected, but false-negative speech or inaccurate word positions remain possible.']}


def fit_speed(audio_seconds,available_seconds,maximum=1.2):
    if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>0 for x in [audio_seconds,available_seconds]):
        raise MediaError('invalid_fit_duration','Audio and slot durations must be positive and finite.')
    needed=max(1.,audio_seconds/available_seconds)
    if needed>maximum+1e-6:return {'status':'cannot_fit','required_speed':needed,'speed':None}
    return {'status':'fits','speed':min(maximum,needed),'required_speed':needed}
