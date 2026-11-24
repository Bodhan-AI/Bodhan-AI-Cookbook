"""Bounded recovery of unusable English ASR without rewriting source words."""
from decimal import Decimal
from pathlib import Path
import wave

from .media import MediaError
from .reconciliation import quality_check
from .transcription import SAMPLE_RATE,audio_input,atomic_json,inspect_windows
from .corrections import BodhanBoundaryRecovery
from .transcription import RATE


def boundary_recovery(run,client,budget):
    adapter=BodhanBoundaryRecovery(run,client,Path(run)/'boundary_recovery')
    def invoke(request):
        duration=Decimal(str(request['end_seconds']))-Decimal(str(request['start_seconds']))
        token=budget.reserve('source_boundary_recovery',duration/60*RATE*client.settings.attempts)
        try:result=adapter(request)
        except MediaError as exc:
            charge=getattr(exc,'estimated_new_cost_if_billed_inr',None)
            if charge is not None:budget.settle(token,charge)
            raise
        budget.settle(token,result['evidence']['result']['new_estimated_cost_inr'])
        return result
    return invoke


def recover_source(rows,run,client,budget,maximum_windows=3):
    run=Path(run);paths={w['id']:w for w in inspect_windows(run)[0]}
    bad=[]
    for row in rows:
        try:quality_check(row['text'])
        except MediaError:bad.append(row['id'])
    if len(bad)>maximum_windows:
        raise MediaError('source_recovery_limit','Too many unusable source windows; no unbounded recovery calls sent.')
    result=[];events=[];folder=run/'source_recovery'
    for row in rows:
        if row['id'] not in bad:result.append(row);continue
        original=paths[row['id']];folder.mkdir(parents=True,exist_ok=True)
        with wave.open(original['file'],'rb') as source:
            frames=source.getnframes();pcm=source.readframes(frames)
        if frames<SAMPLE_RATE*2:
            raise MediaError('source_window_unusable','Unusable source speech is too short for safe split recovery.')
        midpoint=frames//2;context=min(SAMPLE_RATE//2,frames//8)
        slices=[(0,midpoint+context),(midpoint-context,frames)]
        event={'parent_window':row['id'],'reason':'English source recognition failed quality validation. Original response retained.','children':[]}
        events.append(event);atomic_json(folder/'report.json',{'events':events})
        for number,(a,b) in enumerate(slices,1):
            identifier=row['id']+'-recovery-'+str(number);path=folder/(identifier+'.wav')
            with wave.open(str(path),'wb') as target:
                target.setparams((1,2,SAMPLE_RATE,0,'NONE','not compressed'));target.writeframes(pcm[a*2:b*2])
            _,info=audio_input(path)
            token=budget.reserve('source_quality_recovery_'+identifier,Decimal(info['estimated_cost_inr'])*client.settings.attempts)
            try:
                recognized=client.transcribe(path,expected_sha256=info['sha256'])
            except MediaError as exc:
                charge=getattr(exc,'estimated_new_cost_if_billed_inr',None)
                if charge is not None:budget.settle(token,charge)
                event['failure']=exc.code;atomic_json(folder/'report.json',{'events':events});raise
            budget.settle(token,recognized['new_estimated_cost_inr'])
            child={'id':identifier,'text':recognized['text'],'start_seconds':row['start_seconds']+a/SAMPLE_RATE,
                'end_seconds':row['start_seconds']+b/SAMPLE_RATE}
            event['children'].append({**child,'audio_sha256':info['sha256'],'cache_hit':recognized.get('cache_hit',False)})
            atomic_json(folder/'report.json',{'events':events})
            try:quality_check(child['text'])
            except MediaError as exc:
                event['failure']='source_recovery_unusable';atomic_json(folder/'report.json',{'events':events})
                raise MediaError('source_recovery_unusable','Short source recovery still returned unusable text; no invented replacement used.') from exc
            result.append(child)
        event['status']='recovered';atomic_json(folder/'report.json',{'events':events})
    return result,events
