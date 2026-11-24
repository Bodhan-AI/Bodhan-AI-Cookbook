"""Fill missing per-group recognition evidence after fitting or retry recovery."""
import json
from decimal import Decimal
from pathlib import Path
import shutil

from .request_pacing import PacedTransport,RequestPacer
from .transcription import TranscriptionClient,RetrySettings,load_key,http_transport,atomic_json
from .verification import inspect_synthesis,estimate,prepare,verify


def finish_checks(manifest,out,env_file,budget,ffmpeg):
    source=Path(manifest['source_run'])/'verification/verification_report.json'
    original=json.loads(source.read_text()) if source.exists() else {'languages':{}}
    pending=[];selected={'languages':{}};out=Path(out)
    for lang,data in manifest['languages'].items():
        old={u['id']:u for u in original['languages'].get(lang,{}).get('units',[])}
        for item in data['units']:
            if item['status']!='fitted':continue
            check=item.get('qa',old.get(item['id'],{}))
            if item.get('phrase_generation') and check.get('audio_sha256')!=item.get('audio_sha256'):check={}
            if check.get('cer') is not None:item['qa']=check;continue
            target=out/'synthesis'/lang/(item['id']+'.wav');target.parent.mkdir(parents=True,exist_ok=True)
            if Path(item['audio_path']).resolve()!=target.resolve():shutil.copyfile(item['audio_path'],target)
            selected['languages'].setdefault(lang,{'units':[]})['units'].append({'id':item['id'],'status':'synthesized','text':item['translation'],
                'result':{'audio':item['fit']['original_audio']}})
            # The speech reference is the raw generated audio, before tempo/loudness.
            from .synthesis import inspect_wav
            selected['languages'][lang]['units'][-1]['result']['audio']=inspect_wav(target.read_bytes())
            pending.append(item)
    if not pending:return manifest
    atomic_json(out/'synthesis/synthesis_report.json',selected)
    rows=inspect_synthesis(out/'synthesis/synthesis_report.json')
    allowance=Decimal(estimate(rows)['one_attempt_cost_inr'])+Decimal('.00001')
    requests=prepare(rows,out/'verification',ffmpeg)
    token=budget.reserve('missing_fit_content_verification',allowance)
    client=TranscriptionClient(load_key(env_file),out/'.asr_cache',RetrySettings(attempts=1,concurrency=1),
        transport=PacedTransport(http_transport,RequestPacer(10)),allowed_languages=set(selected['languages']))
    checked=verify(rows,requests,out/'verification',client,allowance,workers=1)
    budget.settle(token,checked['estimated_new_cost_inr'])
    for item in pending:
        item['qa']=next(u for u in checked['languages'][item['language']]['units'] if u['id']==item['id'])
        item['qa']['audio_sha256']=item['audio_sha256'] if item.get('audio_sha256') else next(r['source_audio_sha256'] for r in rows if r['language']==item['language'] and r['id']==item['id'])
    return manifest
