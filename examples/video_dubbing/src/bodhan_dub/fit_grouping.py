"""Repair connected source-group boundaries without changing spoken words."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil

import numpy as np

from .audio_output import pcm_read,pcm_write
from .media import MediaError
from .reconciliation import WORD
from .synthesis import inspect_wav
from .timing import source_timeline
from .transcription import atomic_json
from .verification import character_error_rate


def connected_groups(units,timeline):
    times={u['id']:u for u in timeline['units']};result=[];events=[]
    commands={'write','draw','pause','compare','yes','no','right','thanks'}
    for original in units:
        current=deepcopy(original);current['source_unit_ids']=[original['id']]
        if result:
            previous=result[-1];a=times[previous['source_unit_ids'][-1]];b=times[original['id']]
            first=WORD.findall(current['text'])[0]
            tail=re.split(r'[.!?]',previous['text'].rstrip(' .!?'))[-1];tail_words=WORD.findall(tail)
            fragment=(len(tail_words)==1 and tail_words[0].casefold() not in commands and first.isupper())
            continuation=first.casefold() in {'that','which','and','but','because'}
            span=current['source_window_envelope']['end_seconds']-previous['source_window_envelope']['start_seconds']
            if (fragment or continuation) and b['start_seconds']-a['end_seconds']<1 and span<=60:
                previous['text']=previous['text'].rstrip(' .!?')+' '+current['text']
                previous['source_unit_ids'].append(original['id'])
                previous['source_spans'].extend(current['source_spans'])
                previous['sentence_ids']=previous.get('sentence_ids',[])+current.get('sentence_ids',[])
                previous['source_window_envelope']['end_seconds']=current['source_window_envelope']['end_seconds']
                previous['id']=previous['source_unit_ids'][0]+'-'+original['id'].removeprefix('unit-')
                events.append({'source_unit_ids':list(previous['source_unit_ids']),'reason':'short_lexical_fragment' if fragment else 'grammatical_continuation'})
                continue
        result.append(current)
    before=[w.casefold() for u in units for w in WORD.findall(u['text'])]
    after=[w.casefold() for u in result for w in WORD.findall(u['text'])]
    if before!=after:raise MediaError('fit_grouping_changed_source','Fit grouping changed source words or their order.')
    return result,events


def prepare_fit_source(run,out,repair=None):
    run=Path(run).resolve();out=Path(out).resolve()
    if out.exists():raise MediaError('fit_grouping_output_exists','Choose a fresh derived fitting-source directory.')
    source=json.loads((run/'speech_units.en.json').read_text());corrected=json.loads((run/'corrections.final.json').read_text())
    detector=json.loads((run.parent/'speech_regions.json').read_text())
    timeline=source_timeline(source['units'],corrected['source_windows'],detector)
    units,events=connected_groups(source['units'],timeline)
    generated=json.loads((run/'synthesis/synthesis_report.json').read_text())
    override=json.loads(Path(repair).read_text()) if repair else {'languages':{}}
    qa_path=run/'verification/verification_report.json';qa=json.loads(qa_path.read_text()) if qa_path.exists() else {'languages':{}}
    repair_qa_path=Path(repair).resolve().parent.parent/'repair_quality_report.json' if repair else None
    repair_qa=json.loads(repair_qa_path.read_text()) if repair_qa_path and repair_qa_path.exists() else {'parents':[]}
    derived=out/'run';derived.mkdir(parents=True)
    shutil.copyfile(run.parent/'source_audio.wav',out/'source_audio.wav')
    shutil.copyfile(run.parent/'speech_regions.json',out/'speech_regions.json')
    shutil.copyfile(run/'corrections.final.json',derived/'corrections.final.json')
    atomic_json(derived/'speech_units.en.json',{**source,'units':units,'fit_grouping_events':events,'source_run':str(run)})
    final_speech={'languages':{},'status':'synthesized','new_estimated_cost_inr':'0','content_verified':False}
    final_qa={'languages':{},'scope':'Reuse of existing recognition evidence; no new hosted calls.'}
    for lang,data in generated['languages'].items():
        old_trans=json.loads((run/'translations'/f'translation.{lang}.json').read_text());translations={u['id']:u for u in old_trans['units']}
        originals={u['id']:u for u in data['units']};overrides={u['id']:u for u in override['languages'].get(lang,{}).get('units',[])}
        checks={u['id']:u for u in qa['languages'].get(lang,{}).get('units',[])}
        if lang=='ta':checks.update({u['id']:u for u in repair_qa['parents']})
        target_rows=[];speech_rows=[];qa_rows=[]
        for unit in units:
            ids=unit['source_unit_ids'];text=' '.join(translations[i]['translation'] for i in ids)
            target_rows.append({'id':unit['id'],'status':'translated','source_text':unit['text'],'translation':text,
                'source_window_envelope':unit['source_window_envelope'],'source_unit_ids':ids,'model':'indic-translate',
                'note':'Existing Bodhan outputs concatenated without rewriting target words.'})
            pcms=[];voices=set()
            for ident in ids:
                row=overrides.get(ident,originals[ident])
                file=Path(repair).resolve().parent/lang/(ident+'.wav') if ident in overrides else run/'synthesis'/lang/(ident+'.wav')
                pcm,info=pcm_read(file)
                if row['text']!=translations[ident]['translation'] or info['sha256']!=row['result']['audio']['sha256']:
                    raise MediaError('fit_grouping_source_changed','Audio/reference changed before local grouping.')
                voices.add(row['result']['voice']);pcms.append(pcm)
            if len(voices)!=1:raise MediaError('fit_grouping_voice_conflict','Connected groups have different voices; keep them separate.')
            file=derived/'synthesis'/lang/(unit['id']+'.wav');pcm_write(file,np.concatenate(pcms));info=inspect_wav(file.read_bytes())
            speech_rows.append({'id':unit['id'],'text':text,'status':'synthesized','source_window_envelope':unit['source_window_envelope'],
                'source_unit_ids':ids,'result':{'voice':next(iter(voices)),'audio':info,'audio_path':str(file),'estimated_new_cost_inr':'0',
                    'content_verified':False,'fit_to_timeline':False}})
            if all(checks.get(i,{}).get('hypothesis_text') is not None for i in ids):
                hypothesis=' '.join(checks[i]['hypothesis_text'] for i in ids);metrics=character_error_rate(text,hypothesis)
                qa_rows.append({'id':unit['id'],'reference_text':text,'hypothesis_text':hypothesis,**metrics,
                    'status':'pass' if metrics['cer']<=.2 else 'warning','source_unit_ids':ids,'note':'Derived from existing per-unit recognition; no new recognition.'})
            else:qa_rows.append({'id':unit['id'],'status':'not_available','source_unit_ids':ids})
        atomic_json(derived/'translations'/f'translation.{lang}.json',{**old_trans,'units':target_rows,'fit_grouping_events':events})
        final_speech['languages'][lang]={'status':'synthesized','units':speech_rows}
        final_qa['languages'][lang]={'units':qa_rows}
    atomic_json(derived/'synthesis/synthesis_report.json',final_speech)
    atomic_json(derived/'verification/verification_report.json',final_qa)
    atomic_json(out/'grouping_provenance.json',{'source_run':str(run),'new_hosted_calls':0,'source_units_before':len(source['units']),
        'groups_after':len(units),'events':events,'all_english_words_preserved_in_order':True})
    return derived
