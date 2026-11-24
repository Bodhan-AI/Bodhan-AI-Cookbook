"""Independent Bodhan back-transcription and per-unit character error rate."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from decimal import Decimal,InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import unicodedata
import wave

from .media import MediaError,tool_path
from .request_pacing import RequestPacer,PacedTransport
from .synthesis import inspect_wav
from .transcription import (ASR_LANGUAGES,TranscriptionClient,RetrySettings,atomic_json,
                            audio_input,http_transport,load_key)


def normalize(text):
    """NFKC/casefold, Unicode decimal digits, and letters/marks/numbers only."""
    value=unicodedata.normalize('NFKC',text).casefold()
    return ''.join(str(unicodedata.decimal(c)) if unicodedata.category(c)=='Nd' else c
                   for c in value if unicodedata.category(c)[0] in {'L','M','N'})


def character_error_rate(reference,hypothesis):
    a,b=normalize(reference),normalize(hypothesis)
    if not a:raise MediaError('empty_verification_reference','Reference must contain lexical characters.')
    previous=list(range(len(b)+1))
    for index,left in enumerate(a,1):
        current=[index]
        for j,right in enumerate(b,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(left!=right)))
        previous=current
    return {'cer':previous[-1]/len(a),'edit_distance':previous[-1],
            'reference_characters':len(a),'hypothesis_characters':len(b),
            'normalization':'NFKC, casefold, Unicode decimal digits; retain letters/marks/numbers, remove spaces/punctuation.'}


def merge_chunks(chunks):
    """Merge without consulting the reference or asking an LLM to rewrite it."""
    words=[];issues=[];previous=None
    for chunk in chunks:
        raw=chunk['text'].split();tokens=[normalize(w) for w in raw]
        keep=[i for i,t in enumerate(tokens) if t]
        raw=[raw[i] for i in keep];tokens=[tokens[i] for i in keep]
        if not tokens:
            issues.append('empty_recognition_chunk');previous=chunk;continue
        letters=[c for c in ''.join(tokens) if c.isalpha()]
        if len(letters)>=20 and max(letters.count(c) for c in set(letters))/len(letters)>.8:
            issues.append('degenerate_recognition_chunk');previous=chunk;continue
        cut=0
        if words and previous and chunk['start_sample']<previous['end_sample']:
            left=[normalize(w) for w in words]
            for count in range(min(12,len(left),len(tokens)),0,-1):
                if left[-count:]!=tokens[:count]:continue
                phrase=tokens[:count]
                if count>=2 and len(set(phrase))>=2:
                    cut=count;break
                if count==1 and len(phrase[0])>=4:
                    cut=1;issues.append('single_word_overlap_anchor');break
            if not cut:issues.append('overlap_not_resolved_conservatively')
        words.extend(raw[cut:]);previous=chunk
    return ' '.join(words),sorted(set(issues))


def window_ranges(frames,rate=16000):
    if type(frames) is not int or frames<=0 or rate!=16000:
        raise MediaError('invalid_verification_audio','Expected nonempty prepared verification audio.')
    cursor=0;rows=[]
    while cursor<frames:
        left=min(rate,cursor)
        cut=frames if frames-cursor+left<=15*rate else cursor+13*rate
        start,end=max(0,cursor-rate),min(frames,cut+rate)
        if not cursor<cut or end-start>15*rate:raise MediaError('invalid_verification_plan','Cannot construct bounded verification windows.')
        rows.append({'start_sample':start,'end_sample':end,'core_start_sample':cursor,'core_end_sample':cut})
        cursor=cut
    return rows


def inspect_synthesis(path):
    try:
        report=json.loads(Path(path).read_text());rows=[]
        for lang,data in report['languages'].items():
            if lang not in ASR_LANGUAGES:raise ValueError
            seen=set()
            for unit in data['units']:
                ident=unit['id']
                if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident) or ident in seen:raise ValueError
                seen.add(ident)
                if unit['status']!='synthesized':
                    rows.append({'language':lang,'id':ident,'status':'source_failed','reason':'Speech synthesis failed or was not attempted.'});continue
                text=unit['text']
                if not isinstance(text,str) or len(text)>10000 or not normalize(text):raise ValueError
                audio=Path(path).resolve().parent/lang/(ident+'.wav')
                if audio.resolve().parent!=(Path(path).resolve().parent/lang).resolve():raise ValueError
                blob=audio.read_bytes();info=inspect_wav(blob)
                if info['sha256']!=unit['result']['audio']['sha256']:raise ValueError
                rows.append({'language':lang,'id':ident,'reference_text':text,'audio_path':str(audio),
                             'source_audio_sha256':info['sha256'],'audio_frames':info['frames'],'status':'ready'})
        if not rows:raise ValueError
        return rows
    except MediaError:raise
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise MediaError('invalid_verification_input','Expected unchanged generated audio and its synthesis report.') from exc


def estimate(rows):
    seconds=Decimal(0);calls=0
    for row in rows:
        if row['status']!='ready':continue
        frames=round(row['audio_frames']*16000/24000)
        spans=window_ranges(frames)
        calls+=len(spans)
        seconds+=sum((Decimal(w['end_sample']-w['start_sample'])/16000 for w in spans),Decimal(0))
    return {'requests':calls,'submitted_audio_seconds_with_overlap':str(seconds),
            'one_attempt_cost_inr':str(seconds/60*Decimal('.10'))}


def prepare(rows,out,ffmpeg):
    out=Path(out);requests=[]
    for row in rows:
        if row['status']!='ready':continue
        if hashlib.sha256(Path(row['audio_path']).read_bytes()).hexdigest()!=row['source_audio_sha256']:
            raise MediaError('verification_audio_changed','Source speech changed after preflight.')
        folder=out/'audio'/row['language']/row['id'];folder.mkdir(parents=True,exist_ok=True)
        converted=folder/'analysis-16000.wav'
        identity=folder/'source.json'
        if converted.exists():
            saved=json.loads(identity.read_text()) if identity.exists() else {}
            if saved.get('source_sha256')!=row['source_audio_sha256'] or saved.get('analysis_sha256')!=hashlib.sha256(converted.read_bytes()).hexdigest():
                raise MediaError('verification_audio_changed','Existing analysis audio belongs to a different source.')
        else:
            result=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',row['audio_path'],'-map','0:a:0','-vn',
                                   '-ar','16000','-ac','1','-c:a','pcm_s16le',str(converted)],capture_output=True,timeout=60,check=False)
            if result.returncode!=0:raise MediaError('verification_preparation_failed','Cannot prepare ASR audio; no hosted calls started.')
            atomic_json(identity,{'source_sha256':row['source_audio_sha256'],'analysis_sha256':hashlib.sha256(converted.read_bytes()).hexdigest()})
        with wave.open(str(converted),'rb') as audio:
            if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth())!=(16000,1,2):
                raise MediaError('verification_preparation_failed','Analysis audio has an unexpected format.')
            frames=audio.getnframes();pcm=audio.readframes(frames)
        if len(pcm)!=frames*2:raise MediaError('verification_preparation_failed','Analysis WAV is truncated.')
        if abs(frames/16000-row['audio_frames']/24000)>.001:
            raise MediaError('verification_preparation_failed','Resampling changed audio duration unexpectedly.')
        cores=[]
        for index,span in enumerate(window_ranges(frames),1):
            path=folder/f'window-{index:02d}.wav'
            segment=pcm[span['start_sample']*2:span['end_sample']*2]
            if not path.exists():
                with wave.open(str(path),'wb') as target:
                    target.setparams((1,2,16000,0,'NONE','not compressed'));target.writeframes(segment)
            _,info=audio_input(path)
            with wave.open(str(path),'rb') as actual:
                if actual.readframes(actual.getnframes())!=segment:
                    raise MediaError('verification_audio_changed','Request WAV no longer matches the source interval.')
            cores.append(segment[(span['core_start_sample']-span['start_sample'])*2:(span['core_end_sample']-span['start_sample'])*2])
            requests.append({'id':row['language']+'/'+row['id']+f'/{index:02d}','language':row['language'],'unit_id':row['id'],
                             'file':str(path),'input_sha256':info['sha256'],'audio_seconds':info['duration_seconds'],**span})
        if b''.join(cores)!=pcm:raise MediaError('verification_preparation_failed','Verification ownership cores do not reconstruct the source.')
    return requests


def assess(rows,requests,threshold):
    languages={}
    for row in rows:
        target=languages.setdefault(row['language'],{'units':[]})
        entry={'id':row['id'],'reference_text':row.get('reference_text'),'cer_threshold':threshold}
        chunks=sorted([r for r in requests if r['language']==row['language'] and r['unit_id']==row['id']],key=lambda r:r['start_sample'])
        if row['status']!='ready':
            entry.update(status='failed',reason=row['reason'],cer=None)
        elif not chunks or any(r.get('status')!='transcribed' for r in chunks):
            entry.update(status='failed',reason='Some verification requests failed; no complete per-unit metric.',cer=None)
        else:
            hypothesis,issues=merge_chunks([{**r,'text':r['result']['text']} for r in chunks])
            metrics=character_error_rate(row['reference_text'],hypothesis)
            severe=any(i in {'empty_recognition_chunk','degenerate_recognition_chunk'} for i in issues)
            entry.update(**metrics,hypothesis_text=hypothesis,merge_issues=issues,
                         status='pass' if metrics['cer']<=threshold and not severe else 'needs_recovery',
                         metric_note='ASR and overlap merging can introduce differences; a high CER is not proof of a TTS error.')
        target['units'].append(entry)
    for data in languages.values():
        data['status']='pass' if all(u['status']=='pass' for u in data['units']) else 'needs_recovery'
    return languages


def verify(rows,requests,out,client,budget,threshold=.2,workers=2):
    if type(threshold) not in {int,float} or not math.isfinite(threshold) or not 0<=threshold<=1:
        raise MediaError('invalid_verification_threshold','CER threshold must be finite and between zero and one.')
    if type(workers) is not int or not 1<=workers<=4:raise MediaError('invalid_verification_workers','Use one to four workers.')
    planned=sum((Decimal(r['end_sample']-r['start_sample'])/16000/60*Decimal('.10') for r in requests),Decimal(0))
    if not budget.is_finite() or budget<0 or planned>budget:
        raise MediaError('verification_budget_exceeded','Planned single-attempt verification exceeds the explicit budget; no requests sent.')
    out=Path(out);path=out/'verification_report.json'
    # Resume successes from the content cache and keep previously failed
    # requests failed. A second paid attempt requires a separate deliberate run.
    previous={}
    if path.exists():
        old=json.loads(path.read_text())
        previous={r['id']:r for r in old['requests']}
    for request in requests:
        if request['id'] in previous and previous[request['id']].get('input_sha256')!=request['input_sha256']:
            raise MediaError('verification_input_changed','Verification input differs from the saved run; no requests sent.')
    fatal=threading.Event()
    report={'status':'verifying','requests':requests,'languages':{},'planned_cost_inr':str(planned),
            'budget_inr':str(budget),'one_attempt_per_request':True,'dubbing_complete':False,
            'limitations':['CER is a character comparison, not proof of semantic accuracy, number-word equivalence or speaker naturalness.',
                          'ASR differences and imperfect overlap merging can increase CER. No reference text is sent as an ASR hint.',
                          'Metric normalization removes punctuation/space; no LLM rewrites native-language verification text.']}
    def work(request):
        old=previous.get(request['id'])
        if fatal.is_set():return {**request,'status':'failed','error':{'code':'not_attempted','message':'Shared ASR credential, credit or endpoint failed.'},'new_estimated_cost_inr':'0'}
        if old and old.get('input_sha256')!=request['input_sha256']:
            raise MediaError('verification_input_changed','Verification audio changed since the saved run.')
        if old and old.get('status')=='failed':return {**request,'status':'failed','error':old['error'],'new_estimated_cost_inr':'0','prior_attempt_preserved':True,'prior_estimated_cost_inr':old.get('new_estimated_cost_inr','0'),'prior_attempts':old.get('attempts',[])}
        try:
            result=client.transcribe(Path(request['file']),language=request['language'],expected_sha256=request['input_sha256'])
            return {**request,'status':'transcribed','result':result,'new_estimated_cost_inr':result['new_estimated_cost_inr']}
        except MediaError as exc:
            if exc.code in {'invalid_asr_key','asr_forbidden','asr_endpoint_missing','asr_credit_exhausted'}:fatal.set()
            return {**request,'status':'failed','error':{'code':exc.code,'message':str(exc)},'attempts':getattr(exc,'attempts',[]),
                    'new_estimated_cost_inr':getattr(exc,'estimated_new_cost_if_billed_inr','0')}
    by_id={r['id']:index for index,r in enumerate(requests)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(work,r) for r in requests]
        for count,future in enumerate(as_completed(futures),1):
            result=future.result();requests[by_id[result['id']]]=result
            report['completed_requests']=count
            atomic_json(path,report)
            if count%10==0:print(json.dumps({'event':'verification_progress','completed':count,'total':len(requests)}),flush=True)
    report['languages']=assess(rows,requests,threshold)
    report['status']='pass' if all(x['status']=='pass' for x in report['languages'].values()) else 'needs_recovery'
    report['content_check_complete']=all(r.get('status')=='transcribed' for r in requests)
    report['estimated_new_cost_inr']=str(sum((Decimal(r['new_estimated_cost_inr']) for r in requests),Decimal(0)))
    report['rate_control']={'workers':workers,'attempts_per_request':1,'scope':'Shared client pacer; cached results cost no new request.'}
    atomic_json(path,report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Estimate or explicitly run Bodhan back-transcription QA.')
    parser.add_argument('--synthesis',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--budget-inr',default='1.53')
    parser.add_argument('--cer-threshold',type=float,default=.2)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--requests-per-minute',type=float,default=10)
    args=parser.parse_args(argv)
    try:
        rows=inspect_synthesis(args.synthesis);cost=estimate(rows)
        try:budget=Decimal(args.budget_inr)
        except InvalidOperation as exc:raise MediaError('invalid_verification_budget','Budget must be a decimal rupee amount.') from exc
        if not budget.is_finite() or budget<0:raise MediaError('invalid_verification_budget','Budget must be finite and nonnegative.')
        if not math.isfinite(args.cer_threshold) or not 0<=args.cer_threshold<=1 or not 1<=args.workers<=4:
            raise MediaError('invalid_verification_settings','Use a finite CER threshold between zero and one, and one to four workers.')
        if not args.execute:
            print(json.dumps({'status':'dry_run','hosted_calls':0,'estimate':cost}));return 0
        if args.out is None:raise MediaError('missing_verification_output','Supply --out for verification.')
        ffmpeg=tool_path('ffmpeg');requests=prepare(rows,args.out,ffmpeg)
        budget=Decimal(args.budget_inr)
        planned=sum((Decimal(r['end_sample']-r['start_sample'])/16000/60*Decimal('.10') for r in requests),Decimal(0))
        if not budget.is_finite() or planned>budget:raise MediaError('verification_budget_exceeded','Planned requests exceed the approved budget.')
        pacer=RequestPacer(args.requests_per_minute)
        client=TranscriptionClient(load_key(args.env_file),args.out/'.asr_cache',RetrySettings(attempts=1,timeout_seconds=90,concurrency=args.workers),
                                   transport=PacedTransport(http_transport,pacer),allowed_languages={r['language'] for r in rows if r['status']=='ready'})
        report=verify(rows,requests,args.out,client,budget,args.cer_threshold,args.workers)
        print(json.dumps({'status':report['status'],'estimated_new_cost_inr':report['estimated_new_cost_inr'],'requests':len(requests)}))
        return int(report['status']!='pass')
    except (MediaError,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_verification_input'),
                          'message':str(exc) if isinstance(exc,MediaError) else 'Cannot process verification inputs.'}));return 1


if __name__=='__main__':raise SystemExit(main())
