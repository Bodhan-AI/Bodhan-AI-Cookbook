"""Small Bodhan-only experiment for native-script technical terminology."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .media import MediaError,tool_path
from .transcription import atomic_json,TranscriptionClient,RetrySettings,load_key as asr_key
from .translation import TranslationClient,TranslationSettings,load_key as translation_key
from .synthesis import SpeechClient,SpeechSettings,RATE,load_key as speech_key,synthesize_run
from .verification import inspect_synthesis,prepare,estimate,verify


def substitute_term(source,original,translated):
    if not all(isinstance(x,str) and x.strip() for x in [source,original,translated]) or source.count(original)!=1:
        raise MediaError('invalid_terminology_source','Expected one exact occurrence of the selected term.')
    if len(translated)>80 or re.search('[A-Za-z]',translated) or not any('\u0b80'<=c<='\u0bff' for c in translated):
        raise MediaError('term_not_native_script','Bodhan did not return a bounded Tamil-script term; no synthesis started.')
    candidate=source.replace(original,translated,1)
    if len(candidate)>140:raise MediaError('terminology_sample_too_long','Probe exceeds its short speech-input bound.')
    return candidate


def run(source_plan,out,env_file,budget=Decimal('.15')):
    if not budget.is_finite() or not Decimal('.15')<=budget<=Decimal('1'):
        raise MediaError('invalid_probe_budget','Use an explicit finite probe budget from 0.15 to 1 rupee.')
    source=json.loads(Path(source_plan).read_text())
    job=next(j for j in source['jobs'] if j['id']=='unit-0006-part-01' and j['language']=='ta')
    text=job['text'];original='ADJACENT ANGLES'
    if text.count(original)!=1:raise MediaError('invalid_terminology_source','Selected saved passage changed.')
    out=Path(out);identity={'source_text':text,'original_term':original,'voice':job['voice'],'source_id':job['id']}
    path=out/'probe_plan.json'
    if path.exists() and json.loads(path.read_text())!=identity:raise MediaError('probe_source_changed','Existing probe has a different source.')
    atomic_json(path,identity)
    translation_units=[{'id':'adjacent-angles','text':'adjacent angles'}]
    client=TranslationClient(translation_key(env_file),out/'.translation_cache',TranslationSettings(attempts=1,max_output_tokens=256))
    result=client.translate(translation_units,'ta')
    atomic_json(out/'term_translation.json',result)
    if result['errors'] or 'adjacent-angles' not in result['units']:raise MediaError('term_translation_failed','Term translation failed; no synthesis started.')
    term=result['units']['adjacent-angles']['translation']
    candidate=substitute_term(text,original,term)
    translation_cost=Decimal(result['known_new_cost_inr'] or result['conservative_new_cost_upper_bound_inr'])
    speech_cost=Decimal(len(candidate))*RATE
    if translation_cost+speech_cost+Decimal('.06')>budget:
        raise MediaError('probe_budget_exceeded','Insufficient probe budget for speech and verification; no speech started.')
    atomic_json(out/'substitution.json',{'source_text':text,'replacement_term':term,'candidate_text':candidate,
        'term_source':'Bodhan Indic-Translate','source_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'note':'Exact selected span replacement only. No Python or general LLM writes Tamil wording.'})
    speech=SpeechClient(speech_key(env_file),out/'.speech_cache',tool_path('ffmpeg'),SpeechSettings(attempts=1,workers=1))
    jobs=[{**job,'id':'terminology-sample','text':candidate}]
    synthesized=synthesize_run(jobs,out/'synthesis',speech)
    speech_cost=Decimal(synthesized['estimated_new_cost_inr'])
    if synthesized['status']!='synthesized':raise MediaError('probe_synthesis_failed','Sample synthesis failed; no verification started.')
    rows=inspect_synthesis(out/'synthesis/synthesis_report.json')
    remaining=budget-translation_cost-speech_cost
    if Decimal(estimate(rows)['one_attempt_cost_inr'])>remaining:
        raise MediaError('probe_budget_exceeded','Generated sample exceeds remaining QA budget; no ASR started.')
    requests=prepare(rows,out/'verification',tool_path('ffmpeg'))
    asr=TranscriptionClient(asr_key(env_file),out/'.asr_cache',RetrySettings(attempts=1,concurrency=1),allowed_languages={'ta'})
    checked=verify(rows,requests,out/'verification',asr,remaining,workers=1)
    unit=checked['languages']['ta']['units'][0]
    total=translation_cost+speech_cost+Decimal(checked['estimated_new_cost_inr'])
    report={'status':unit['status'],'reference_text':candidate,'hypothesis_text':unit.get('hypothesis_text'),'cer':unit['cer'],
        'replacement_term':term,'translation_cost_inr':str(translation_cost),'speech_cost_inr':str(speech_cost),
        'verification_cost_inr':checked['estimated_new_cost_inr'],'total_new_cost_inr':str(total),
        'scope':'One native-script terminology sample only; original clips and translations unchanged.',
        'limitations':['Low CER does not establish naturalness or semantic correctness. This does not implement full glossary handling.']}
    atomic_json(out/'probe_report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicit one-sample Bodhan terminology experiment; no default live calls.')
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--budget-inr',default='.15')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'status':'dry_run','hosted_calls':0,'scope':'One term translation, one speech sample, and budget-bounded QA.'}));return 0
    try:
        report=run(args.source_plan,args.out,args.env_file,Decimal(args.budget_inr))
        print(json.dumps({'status':report['status'],'cer':report['cer'],'new_cost_inr':report['total_new_cost_inr']}));return int(report['status']!='pass')
    except (MediaError,OSError,ValueError,KeyError,TypeError,StopIteration,ArithmeticError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_terminology_probe'),
            'message':str(exc) if isinstance(exc,MediaError) else 'Cannot process terminology probe.'}));return 1


if __name__=='__main__':raise SystemExit(main())
