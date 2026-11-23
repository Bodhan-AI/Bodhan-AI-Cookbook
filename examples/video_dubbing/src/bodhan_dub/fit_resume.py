"""Continue unresolved fits, reusing successful audio and retrying known throttles."""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import shutil

from .audio_output import pcm_read
from .fit_recovery import Budget,run_fit
from .media import MediaError,tool_path
from .speech_repair import assemble
from .synthesis import SpeechClient,SpeechSettings,RATE,load_key,synthesize_run
from .transcription import atomic_json


def chain_cost(root,seen=None):
    """Sum actual journals, including retries after a manifest was written."""
    root=Path(root).resolve();seen=set() if seen is None else seen
    if root in seen:raise MediaError('fit_budget_cycle','Recovery provenance contains a cycle.')
    seen.add(root)
    data=json.loads((root/'fit_manifest.json').read_text())
    own=Decimal(json.loads((root/'budget.json').read_text())['conservative_spent_inr'])
    return own+(chain_cost(data['previous_fit'],seen) if data.get('previous_fit') else Decimal(0))


def retry_throttled(root,budget,env_file):
    root=Path(root);manifest=json.loads((root/'fit_manifest.json').read_text())
    for lang,data in manifest['languages'].items():
        for item in data['units']:
            if item['status']!='recovery_failed':continue
            for report_path in sorted(root.glob('round-*/parts/synthesis_report.json'),reverse=True):
                report=json.loads(report_path.read_text());rows=report['languages'].get(lang,{}).get('units',[])
                parent_path=report_path.parent.parent/'assembled/synthesis_report.json'
                parents=json.loads(parent_path.read_text())['languages'].get(lang,{}).get('units',[])
                parent=next((u for u in parents if u['id']==item['id']),None)
                if parent is None:continue
                selected=[u for u in rows if u['id'] in parent['children']]
                failed=[u for u in selected if u['status']!='synthesized']
                if not failed or any(not u.get('attempts') or u['attempts'][-1].get('http_status') not in {429,500,502,503,504} for u in failed):break
                destination=root/('transient-'+lang+'-'+item['id'])
                if (destination/'assembled/synthesis_report.json').exists():break
                # Only known transient failures may be retried. Unknown/timeouts stay blocked.
                client=SpeechClient(load_key(env_file),root/'.speech_cache',tool_path('ffmpeg'),SpeechSettings(attempts=2,workers=1,requests_per_minute=5))
                cap=sum((Decimal(len(u['text']))*RATE*2 for u in failed),Decimal(0));token=budget.reserve('known-transient-speech-'+lang+'-'+item['id'],cap)
                jobs=[{'id':u['id'],'text':u['text'],'voice':item['voice'],'language':lang,'source_window_envelope':item['source_window_envelope']} for u in selected]
                repaired=synthesize_run(jobs,destination/'parts',client);budget.settle(token,repaired['estimated_new_cost_inr'])
                plan={'parents':[{'id':item['id'],'text':parent['text'],'children':parent['children'],'language':lang,
                                  'voice':item['voice'],'source_window_envelope':item['source_window_envelope']}]}
                assemble(plan,repaired,destination/'assembled')
                print(json.dumps({'event':'known_transient_retry','language':lang,'unit_id':item['id'],'status':repaired['status']}),flush=True)
                break


def continuation_source(root,out):
    root=Path(root).resolve();out=Path(out).resolve()
    if out.exists():raise MediaError('fit_continuation_exists','Choose a fresh continuation source.')
    manifest=json.loads((root/'fit_manifest.json').read_text());source=Path(manifest['source_run']);derived=out/'run';derived.mkdir(parents=True)
    for name in ['speech_regions.json','source_audio.wav']:shutil.copyfile(source.parent/name,out/name)
    for name in ['speech_units.en.json','corrections.final.json']:shutil.copyfile(source/name,derived/name)
    if (source/'translation_policy.json').exists():shutil.copyfile(source/'translation_policy.json',derived/'translation_policy.json')
    native=json.loads((source/'synthesis/synthesis_report.json').read_text());qa_path=source/'verification/verification_report.json'
    original_qa=json.loads(qa_path.read_text()) if qa_path.exists() else {'languages':{}}
    new_speech={'status':'synthesized','languages':{},'new_estimated_cost_inr':'0'};new_qa={'languages':{}}
    candidates=[]
    for path in root.glob('*/assembled/synthesis_report.json'):
        data=json.loads(path.read_text())
        for lang,values in data['languages'].items():
            trans_path=path.parent.parent/f'translation.{lang}.json'
            if not trans_path.exists() and path.parent.parent.name.startswith('transient-'):
                # A transient retry uses the original approved translation unchanged.
                for old in sorted(root.glob('round-*/translation.'+lang+'.json'),reverse=True):
                    if all(u['id'] in json.loads(old.read_text()).get('units',{}) for u in values['units']):trans_path=old;break
            if not trans_path.exists():continue
            translated=json.loads(trans_path.read_text()).get('units',{})
            for row in values['units']:
                if row['status']!='synthesized' or row['id'] not in translated:continue
                audio=path.parent/lang/(row['id']+'.wav');_,info=pcm_read(audio)
                if info['sha256']!=row['result']['audio']['sha256']:raise MediaError('fit_candidate_changed','A recovery candidate audio changed.')
                candidates.append({'language':lang,'id':row['id'],'audio':audio,'result':row['result'],
                    'translation':row['text'],'english':translated[row['id']]['source_text'],'duration':info['duration_seconds']})
    for lang,data in manifest['languages'].items():
        old_trans=json.loads((source/'translations'/f'translation.{lang}.json').read_text());rows=[];speech=[];qa=[]
        old_targets={u['id']:u for u in old_trans['units']};old_native={u['id']:u for u in native['languages'][lang]['units']}
        checks={u['id']:u for u in original_qa['languages'].get(lang,{}).get('units',[])}
        for item in data['units']:
            ident=item['id'];options=[c for c in candidates if c['id']==ident and c['language']==lang]
            if item['status']=='fitted':
                chosen={'audio':Path(item['audio_path']),'translation':item['translation'],'english':item['english'],
                        'result':{**old_native[ident]['result'],'audio':pcm_read(item['audio_path'])[1]}}
                check=item.get('qa',checks.get(ident,{'status':'not_available'}))
            elif options:
                chosen=min(options,key=lambda c:c['duration']);check={'status':'not_available','reason':'Candidate has not yet passed physical fit; QA remains separate.'}
            else:
                chosen={'audio':source/'synthesis'/lang/(ident+'.wav'),'translation':old_targets[ident]['translation'],
                        'english':old_targets[ident]['source_text'],'result':old_native[ident]['result']};check=checks.get(ident,{'status':'not_available'})
            target=derived/'synthesis'/lang/(ident+'.wav');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(chosen['audio'],target)
            row={**old_targets[ident],'translation':chosen['translation'],'source_text':chosen['english'],'adapted_english':chosen['english'],
                 'continuation_note':'Existing provider output reused; no translation is written locally.'};rows.append(row)
            speech.append({'id':ident,'status':'synthesized','text':chosen['translation'],'source_window_envelope':item['source_window_envelope'],
                'result':{**chosen['result'],'voice':item['voice'],'audio_path':str(target),'estimated_new_cost_inr':'0'}})
            qa.append({**check,'id':ident})
        atomic_json(derived/'translations'/f'translation.{lang}.json',{**old_trans,'units':rows})
        new_speech['languages'][lang]={'status':'synthesized','units':speech};new_qa['languages'][lang]={'units':qa}
    atomic_json(derived/'synthesis/synthesis_report.json',new_speech)
    atomic_json(derived/'verification/verification_report.json',new_qa)
    atomic_json(out/'continuation_provenance.json',{'previous_fit':str(root),'new_hosted_calls_for_copying':0,'successful_audio_reused':True})
    return derived


def main(argv=None):
    p=argparse.ArgumentParser(description='Continue only unresolved physical fits within the original spending ceiling.')
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--source-out',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--env-file',type=Path);p.add_argument('--budget-inr',default='10')
    p.add_argument('--execute',action='store_true');args=p.parse_args(argv)
    if not args.execute:print(json.dumps({'status':'dry_run','hosted_calls':0}));return 0
    try:
        previous=json.loads((args.previous/'fit_manifest.json').read_text())
        ledger=json.loads((args.previous/'budget.json').read_text())
        external=chain_cost(args.previous)-Decimal(ledger['conservative_spent_inr'])
        budget=Budget(args.previous/'budget.json',Decimal(ledger['limit_inr']));retry_throttled(args.previous,budget,args.env_file)
        source=continuation_source(args.previous,args.source_out)
        remaining=Decimal(args.budget_inr)-external-budget.spent
        result=run_fit(source,args.out,args.env_file,remaining,max_rounds=2)
        result['previous_fit']=str(args.previous.resolve());result['combined_estimated_cost_inr']=str(external+budget.spent+Decimal(result['new_estimated_cost_inr']))
        atomic_json(args.out/'fit_manifest.json',result)
        print(json.dumps({'status':result['status'],'all_units_fitted':result['all_units_fitted'],'combined_estimated_cost_inr':result['combined_estimated_cost_inr']}))
        return int(not result['all_units_fitted'])
    except (MediaError,OSError,ValueError,KeyError,TypeError,ArithmeticError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','fit_continuation_failed'),
            'message':str(exc) if isinstance(exc,MediaError) else 'Cannot continue fit recovery; inspect saved stage journals.'}));return 1


if __name__=='__main__':raise SystemExit(main())
