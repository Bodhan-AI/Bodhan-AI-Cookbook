"""Fit independently synthesized complete sentences/headings across source pauses."""
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re

import numpy as np

from .audio_output import RATE,pcm_read,pcm_write,process_clip,place_pcm,trim_quiet_edges
from .media import MediaError
from .synthesis import RATE as PRICE,synthesize_run
from .transcription import atomic_json


def complete_phrases(text):
    """Keep punctuation with its sentence. Never choose a word-count boundary."""
    if not isinstance(text,str) or not text.strip():raise MediaError('invalid_phrase_text','Expected nonempty provider text.')
    pieces=[];start=0
    for match in re.finditer(r'[.!?।؟۔:;]+(?:["”’»)]*)\s+',text):
        prefix=text[start:match.start()];word=prefix.split()[-1] if prefix.split() else ''
        # A label or common title followed by a dot is not a complete sentence.
        if match.group().startswith('.') and (len(word)==1 or word.casefold() in {'dr','mr','mrs','ms','prof','sr','jr','etc','vs','e.g','i.e'}):continue
        pieces.append(text[start:match.end()]);start=match.end()
    if start<len(text):pieces.append(text[start:])
    if ''.join(pieces)!=text:raise MediaError('phrase_text_changed','Sentence partition changed provider text.')
    return pieces


def phrase_partitions(lengths,capacities,maximum_speed=1.2):
    """Return feasible complete-phrase partitions; never split an atomic phrase."""
    prefix=[0]
    for length in lengths:prefix.append(prefix[-1]+length)
    @lru_cache(None)
    def visit(slot,start):
        if slot==len(capacities):return [(0,())] if start==len(lengths) else []
        choices=[]
        for end in range(start,len(lengths)+1):
            size=prefix[end]-prefix[start]
            if size>capacities[slot]*maximum_speed:break
            for score,tail in visit(slot+1,end):
                # Prefer using each source interval, and modest tempo changes.
                penalty=10 if end==start else max(0,size/capacities[slot]-1)**2
                choices.append((score+penalty,((start,end),)+tail))
        return sorted(choices,key=lambda x:x[0])[:30]
    return [plan for _,plan in visit(0,0)]



def borrow_internal_pause_tail(timing):
    """Extend an earlier speech slot while keeping a long protected pause."""
    revised = deepcopy(timing)
    slots = revised['speech_slots']
    gaps = revised.get('protected_gap_cores', [])
    changes = []
    for left, right in zip(slots, slots[1:]):
        gap = next((g for g in gaps
                    if abs(g['start_seconds'] - left['end_seconds']) < 1e-6
                    and abs(g['end_seconds'] - right['start_seconds']) < 1e-6), None)
        if gap is None:
            continue
        duration = gap['end_seconds'] - gap['start_seconds']
        amount = min(2.25, duration - max(2.5, duration / 2))
        if amount <= 0:
            continue
        left['end_seconds'] += amount
        gap['start_seconds'] += amount
        changes.append({'borrowed_seconds':amount,'retained_seconds':duration-amount})
    if not changes:
        return None
    revised['available_seconds'] = sum(s['end_seconds']-s['start_seconds'] for s in slots)
    revised['internal_pause_recovery'] = changes
    return revised


def fit_phrases(item,timing,folder,speech,budget,ffmpeg):
    """Keep translation verbatim, synthesize atoms, then place complete groups."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    atoms=complete_phrases(item['translation']);jobs=[];children=[]
    for index,text in enumerate(atoms):
        ids=[]
        # A short-input recommendation must never detach a word from its sentence.
        if len(text)>1000:return None,{'status':'needs_shorter_complete_phrases','reason':'A complete phrase exceeds the documented client input bound; refusing a word-count split.'}
        for number,part in enumerate([text],1):
            ident=f"{item['id']}-a{index+1:02d}-p{number:02d}";ids.append(ident)
            jobs.append({'id':ident,'language':item['language'],'voice':item['voice'],'text':part,
                         'source_window_envelope':item['source_window_envelope']})
        children.append(ids)
    # Identity, text and voice caching live inside SpeechClient. Cached successes cost zero.
    token=None
    if getattr(speech,'budget',None) is not budget:
        allowance=sum((Decimal(len(j['text']))*PRICE*speech.settings.attempts for j in jobs),Decimal(0))
        token=budget.reserve('complete_phrase_speech-'+item['language']+'-'+item['id'],allowance)
    generated=synthesize_run(jobs,folder/'parts',speech)
    if token is not None:budget.settle(token,generated['estimated_new_cost_inr'])
    units={u['id']:u for u in generated['languages'][item['language']]['units']}
    if any(u['status']!='synthesized' for u in units.values()):
        return None,{'status':'phrase_generation_failed','reason':'A complete-text speech piece failed; no arbitrary cut was substituted.'}
    if ''.join(units[c]['text'] for group in children for c in group)!=item['translation']:
        raise MediaError('phrase_text_changed','Synthesized text did not preserve the translation exactly.')
    raw_atoms=[];clean_atoms=[]
    for group in children:
        parts=[]
        for child in group:
            pcm,info=pcm_read(units[child]['result']['audio_path'])
            if info['sha256']!=units[child]['result']['audio']['sha256']:raise MediaError('phrase_audio_changed','A generated piece changed.')
            parts.append(pcm)
        raw=np.concatenate(parts);raw_atoms.append(raw);clean_atoms.append(trim_quiet_edges(raw)[0])
    capacities=[round(s['end_seconds']*RATE)-round(s['start_seconds']*RATE) for s in timing['speech_slots']]
    plans=phrase_partitions([len(a) for a in clean_atoms],capacities)
    if not plans:
        alternative=borrow_internal_pause_tail(timing)
        if alternative:
            alternative_capacities=[round(s['end_seconds']*RATE)-round(s['start_seconds']*RATE) for s in alternative['speech_slots']]
            alternatives=phrase_partitions([len(a) for a in clean_atoms],alternative_capacities)
            if alternatives:
                timing,capacities,plans=alternative,alternative_capacities,alternatives
    atomic_json(folder/'phrase_plan.json',{'text':item['translation'],'atoms':atoms,'children':children,
        'capacities_samples':capacities,'candidate_partitions':plans,'boundary_policy':'Complete punctuation-delimited sentences/headings only; never character-count or waveform-energy cuts.'})
    raw_path=folder/'generated.wav';pcm_write(raw_path,np.concatenate(raw_atoms));_,raw_info=pcm_read(raw_path)
    for attempt,plan in enumerate(plans):
        fits=[];fitted=[];evidence=[];placements=[];cursor=0;failed=False
        for index,((a,b),slot) in enumerate(zip(plan,timing['speech_slots'])):
            if a==b:continue
            group=folder/f'plan-{attempt:02d}'/f'slot-{index+1:02d}';group.mkdir(parents=True,exist_ok=True)
            # Each source interval receives complete atoms, never an audio slice.
            source=group/'whole_phrases.wav';pcm_write(source,np.concatenate(clean_atoms[a:b]))
            fit,pcm=process_clip(source,group/'fit',capacities[index]/RATE,ffmpeg)
            if pcm is None:failed=True;break
            _,info=pcm_read(fit['path']);fitted.append(pcm);fits.append(fit)
            evidence.append({'path':fit['path'],'sha256':info['sha256'],'frames':len(pcm),'text':''.join(atoms[a:b]),
                'atom_start':a,'atom_end':b,'speed':fit['speed'],'generation_children':[c for ids in children[a:b] for c in ids]})
            start=round(slot['start_seconds']*RATE)
            placements.append({'start_sample':start,'end_sample':start+len(pcm),'audio_start_sample':cursor,'audio_end_sample':cursor+len(pcm),
                'start_seconds':start/RATE,'end_seconds':(start+len(pcm))/RATE});cursor+=len(pcm)
        if failed:continue
        normalized=folder/f'plan-{attempt:02d}'/'normalized.wav';pcm=np.concatenate(fitted);pcm_write(normalized,pcm);_,info=pcm_read(normalized)
        boundaries=[];position=0
        for piece in evidence[:-1]:position+=piece['frames'];boundaries.append(position)
        # Validate allocation with the same strict primitive used by the renderer.
        place_pcm(pcm,timing['speech_slots'],boundaries)
        solution={**deepcopy(item),'status':'fitted','timing':timing,'audio_path':str(raw_path.resolve()),'audio_sha256':raw_info['sha256'],
            'observed_seconds':raw_info['duration_seconds'],'normalized_audio_sha256':info['sha256'],'placements':placements,'warnings':[],
            'pause_compaction':None,'phrase_generation':True,
            'fit':{'status':'fitted','speed':max(f['speed'] for f in fits),'path':str(normalized.resolve()),'original_audio':raw_info,
                'fitted_audio':info,'approved_break_samples':boundaries,'independent_pieces':evidence,
                'loudness_lufs':sum(f['loudness_lufs'] for f in fits)/len(fits),'loudness_target_met':all(f['loudness_target_met'] for f in fits),
                'true_peak_dbfs':max(f['true_peak_dbfs'] for f in fits),'boundary_policy':'No new source pause inside an independently generated complete phrase group.'}}
        # New speech must not inherit QA of the old waveform.
        solution.pop('qa',None)
        return solution,None
    return None,{'status':'needs_shorter_complete_phrases','reason':'Complete phrases do not fit the available intervals at <=1.2x. Refusing an unnatural mid-phrase pause.'}
