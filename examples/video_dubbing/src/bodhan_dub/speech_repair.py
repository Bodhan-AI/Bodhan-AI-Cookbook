"""Split unchanged synthesis text into bounded pieces and assemble repair candidates."""
import argparse
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import wave

from .media import MediaError,tool_path
from .synthesis import (RATE,SpeechClient,SpeechSettings,atomic_bytes,inspect_wav,
                        load_key,synthesize_run)
from .transcription import atomic_json
from .verification import inspect_synthesis


def split_text(text,maximum=140):
    if not isinstance(text,str) or not text.strip() or type(maximum) is not int or not 40<=maximum<=200:
        raise MediaError('invalid_repair_text','Use nonempty text and a character bound from 40 to 200.')
    pieces=[];remaining=text
    while len(remaining)>maximum:
        # Prefer complete sentences, then clause boundaries, then whole words.
        cut=None
        for priority,pattern in enumerate([r'[.!?।]\s+',r'[,;:،]\s+',r'\s+']):
            ends=[m.end() for m in re.finditer(pattern,remaining[:maximum+1]) if (1 if priority==0 else 30)<=m.end()<=maximum]
            if ends:cut=max(ends);break
        if cut is None:
            raise MediaError('unsplittable_repair_text','No safe word boundary inside the synthesis bound; no requests sent.')
        pieces.append(remaining[:cut]);remaining=remaining[cut:]
    if remaining:pieces.append(remaining)
    if ''.join(pieces)!=text or any(not p.strip() or len(p)>maximum for p in pieces):
        raise MediaError('invalid_repair_plan','Text partition did not preserve every source character.')
    return pieces


def plan(source,language,identifiers,maximum=140):
    rows=inspect_synthesis(source)
    if not identifiers or len(set(identifiers))!=len(identifiers):
        raise MediaError('invalid_repair_selection','Select distinct existing unit IDs.')
    original=json.loads(Path(source).read_text())
    if language not in original['languages']:raise MediaError('invalid_repair_language','Language is absent from the source report.')
    selected={r['id']:r for r in rows if r['language']==language}
    originals={r['id']:r for r in original['languages'][language]['units']}
    jobs=[];parents=[]
    for ident in identifiers:
        if ident not in selected or selected[ident]['status']!='ready':
            raise MediaError('invalid_repair_selection','Selected source speech is absent or failed.')
        row=selected[ident];unit=originals[ident];pieces=split_text(row['reference_text'],maximum)
        voice=unit['result']['voice'];children=[]
        for index,piece in enumerate(pieces,1):
            child=f'{ident}-part-{index:02d}';children.append(child)
            if len(child)>80:raise MediaError('invalid_repair_selection','Unit ID is too long for repair child IDs.')
            jobs.append({'language':language,'voice':voice,'id':child,'text':piece,'source_window_envelope':unit.get('source_window_envelope')})
        parents.append({'id':ident,'language':language,'text':row['reference_text'],'source_audio_sha256':row['source_audio_sha256'],
                        'children':children,'voice':voice,'source_window_envelope':unit.get('source_window_envelope')})
    return {'version':1,'maximum_characters':maximum,'parents':parents,'jobs':jobs,
            'synthesis_cost_inr':str(sum((Decimal(len(j['text']))*RATE for j in jobs),Decimal(0)))}


def assemble(repair_plan,parts,out):
    out=Path(out);report={'status':'synthesized','languages':{},'content_verified':False,'fit_to_timeline':False,
        'dubbing_complete':False,'repair_method':'Unchanged text split into bounded pieces; concatenate native PCM without trimming.',
        'limitations':['Repair candidate only. Content verification and naturalness remain separate. Source timing is coarse.']}
    for parent in repair_plan['parents']:
        lang=parent['language'];data=report['languages'].setdefault(lang,{'status':'synthesized','units':[]})
        lookup={u['id']:u for u in parts['languages'].get(lang,{}).get('units',[])}
        row={'id':parent['id'],'text':parent['text'],'source_window_envelope':parent['source_window_envelope'],'children':parent['children']}
        if any(child not in lookup or lookup[child]['status']!='synthesized' for child in parent['children']):
            row.update(status='failed',reason='At least one bounded synthesis piece failed; original audio remains untouched.')
            data['status']=report['status']='partial_failure';data['units'].append(row);continue
        if ''.join(lookup[c]['text'] for c in parent['children'])!=parent['text']:
            raise MediaError('repair_text_changed','Generated part text does not reconstruct the original translation.')
        pcm=[]
        for child in parent['children']:
            unit=lookup[child];blob=Path(unit['result']['audio_path']).read_bytes();info=inspect_wav(blob)
            if info['sha256']!=unit['result']['audio']['sha256']:
                raise MediaError('repair_audio_changed','Generated repair piece changed since synthesis.')
            with wave.open(io.BytesIO(blob),'rb') as audio:pcm.append(audio.readframes(audio.getnframes()))
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as audio:
            audio.setparams((1,2,24000,0,'NONE','not compressed'));audio.writeframes(b''.join(pcm))
        blob=buffer.getvalue();info=inspect_wav(blob);target=out/lang/(parent['id']+'.wav')
        if target.exists() and target.read_bytes()!=blob:raise MediaError('repair_output_exists','Refusing to overwrite a different repair candidate.')
        atomic_bytes(target,blob)
        row.update(status='synthesized',result={'language':lang,'voice':parent['voice'],'audio':info,'audio_path':str(target),
                   'content_verified':False,'fit_to_timeline':False,'estimated_new_cost_inr':'0','note':'Cost is recorded on the child synthesis report, not billed again for local assembly.'})
        data['units'].append(row)
    atomic_json(out/'synthesis_report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Estimate or explicitly generate selected short-piece speech repairs.')
    parser.add_argument('--synthesis',type=Path,required=True)
    parser.add_argument('--language',required=True)
    parser.add_argument('--units',required=True)
    parser.add_argument('--max-characters',type=int,default=140)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--budget-inr',default='.41')
    args=parser.parse_args(argv)
    try:
        repair=plan(args.synthesis,args.language,args.units.split(','),args.max_characters)
        budget=Decimal(args.budget_inr)
        if not budget.is_finite() or budget<0 or Decimal(repair['synthesis_cost_inr'])>budget:
            raise MediaError('repair_budget_exceeded','Single-attempt synthesis exceeds the explicit repair budget.')
        if not args.execute:
            print(json.dumps({'status':'dry_run','hosted_calls':0,'pieces':len(repair['jobs']),'characters':sum(len(j['text']) for j in repair['jobs']),
                'maximum_piece_characters':max(len(j['text']) for j in repair['jobs']),'synthesis_cost_inr':repair['synthesis_cost_inr']}));return 0
        if args.out is None:raise MediaError('missing_repair_output','Supply a separate repair output directory.')
        if args.out.resolve()==args.synthesis.resolve().parent:raise MediaError('repair_source_output','Repair output must be separate from the original synthesis.')
        identity=args.out/'repair_plan.json'
        if identity.exists() and json.loads(identity.read_text())!=repair:raise MediaError('repair_plan_changed','Existing repair has different source text, audio identity or settings.')
        atomic_json(identity,repair)
        client=SpeechClient(load_key(args.env_file),args.out/'.speech_cache',tool_path('ffmpeg'),SpeechSettings(attempts=1,workers=2,requests_per_minute=10))
        parts=synthesize_run(repair['jobs'],args.out/'parts',client)
        result=assemble(repair,parts,args.out/'assembled')
        print(json.dumps({'status':result['status'],'pieces':len(repair['jobs']),'estimated_new_cost_inr':parts['estimated_new_cost_inr']}))
        return int(result['status']!='synthesized')
    except (MediaError,OSError,ValueError,KeyError,TypeError,ArithmeticError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_repair_input'),
            'message':str(exc) if isinstance(exc,MediaError) else 'Cannot process speech repair inputs.'}));return 1


if __name__=='__main__':raise SystemExit(main())
