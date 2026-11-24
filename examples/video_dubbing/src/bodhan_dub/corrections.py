"""Automatic, source-backed overlap correction and bounded recovery."""
import argparse
from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
import json
import hashlib
import io
import math
import wave
from pathlib import Path

from .media import MediaError
from .reconciliation import (WORD, compile_joins, join_candidates, read_experiment,
                             validate_plan, quality_check)
from .transcription import atomic_json, TranscriptionClient, RetrySettings, load_key


def source_plan(draft):
    return {'sentences':[{'spans':s['source_spans']} for s in draft['sentences']],
            'excluded':draft['excluded'],'issues':draft['issues']}


def word_refs(plan):
    return [(s['window_id'],i) for sentence in plan['sentences'] for s in sentence['spans']
            for i in range(s['start'],s['end'])]


def original_joins(windows,plan):
    decisions=[]
    for left,right in zip(windows,windows[1:]):
        item={'left':left['id'],'right':right['id'],'trim_left':0,'trim_right':0,'reason':'none'}
        for e in plan['excluded']:
            if {e['span']['window_id'],e['support']['window_id']}=={left['id'],right['id']}:
                field='trim_left' if e['span']['window_id']==left['id'] else 'trim_right'
                item[field]=e['span']['end']-e['span']['start']
                item['reason']=e['reason']
        decisions.append(item)
    return decisions


def retained_variant(windows,index,decision):
    left,right=windows[index:index+2]
    count=decision['trim_left'] or decision['trim_right']
    a,b=left['tokens'][-count:],right['tokens'][:count]
    for offset,(x,y) in enumerate(zip(a,b)):
        if x.casefold()!=y.casefold():
            return (right['id'],offset) if decision['trim_left'] else (left['id'],len(left['tokens'])-count+offset)
    return None


def consistency_score(windows,index,decision):
    """Corroboration from other, non-overlapping source windows, not a dictionary."""
    ref=retained_variant(windows,index,decision)
    if ref is None:return 0
    left,right=windows[index:index+2]
    a=left['tokens'][:len(left['tokens'])-decision['trim_left']]
    b=right['tokens'][decision['trim_right']:]
    joined=[w.casefold() for w in a+b]
    position=(ref[1] if ref[0]==left['id'] else len(a)+ref[1]-decision['trim_right'])
    probes=set()
    for width in [2,3]:
        for start in range(max(0,position-width+1),min(position+1,len(joined)-width+1)):
            probes.add(tuple(joined[start:start+width]))
    score=0
    for window in windows:
        if window['start_seconds']<right['end_seconds'] and window['end_seconds']>left['start_seconds']:
            continue
        tokens=[t.casefold() for t in window['tokens']]
        score+=sum(any(tuple(tokens[i:i+len(phrase)])==phrase for i in range(len(tokens)-len(phrase)+1)) for phrase in probes)
    return score


def render_with_original_punctuation(windows,old_plan,new_plan):
    by_id={w['id']:w for w in windows}
    old_refs=word_refs(old_plan);new_refs=word_refs(new_plan)
    old_tokens=[by_id[i]['tokens'][j].casefold() for i,j in old_refs]
    new_tokens=[by_id[i]['tokens'][j].casefold() for i,j in new_refs]
    old_marks={}
    cursor=0
    for sentence in old_plan['sentences']:
        for span in sentence['spans']:
            cursor+=span['end']-span['start']
            if span['after']:old_marks[cursor-1]=span['after']
    marks={}
    for tag,a,b,c,d in SequenceMatcher(None,old_tokens,new_tokens,autojunk=False).get_opcodes():
        if tag=='equal' or (tag=='replace' and b-a==d-c):
            for source in range(a,b):
                if source in old_marks:marks[c+source-a]=old_marks[source]
    sentences=[];spans=[]
    for index,(ident,token) in enumerate(new_refs):
        if spans and spans[-1]['window_id']==ident and spans[-1]['end']==token and not spans[-1]['after']:
            spans[-1]['end']=token+1
        else:spans.append({'window_id':ident,'start':token,'end':token+1,'after':''})
        spans[-1]['after']=marks.get(index,'')
        if marks.get(index) in {'.','?','!'}:
            sentences.append({'spans':spans});spans=[]
    if spans:sentences.append({'spans':spans})
    return {**new_plan,'sentences':sentences}


def correct(windows,draft,recovery=None,max_recovery_calls=2,protected_terms=()):
    if type(max_recovery_calls) is not int or not 0<=max_recovery_calls<=3:
        raise MediaError('invalid_recovery_budget','Allow zero to three boundary recovery calls.')
    old=source_plan(draft)
    validate_plan(windows,old)
    decisions=original_joins(windows,old)
    available=join_candidates(windows)
    events=[];blocked=set();calls=0;term_choices=[]
    protected={w.casefold() for term in protected_terms for w in WORD.findall(term)}
    flags={tuple(issue['window_ids']):[] for issue in old['issues']}
    for issue in old['issues']:flags[tuple(issue['window_ids'])].append(issue['code'])

    for index,boundary in enumerate(available):
        pair=(boundary['left'],boundary['right'])
        choices=boundary['choices'][1:]
        original=decisions[index]
        exact=[c for c in choices if c['decision']['reason']=='overlap_duplicate']
        variants=[c for c in choices if c['decision']['reason']=='overlap_variant']
        chosen=None;reason=None;evidence=0;recovery_evidence=None
        if exact:
            exact.sort(key=lambda c:(c['decision']['trim_left']+c['decision']['trim_right'],c['decision']==original,c['decision']['trim_right']>0),reverse=True)
            chosen=exact[0]['decision'];reason='exact_overlap_deduplicated'
        elif variants:
            ranked=sorted([(consistency_score(windows,index,c['decision']),c) for c in variants],key=lambda x:x[0],reverse=True)
            current=next((c for c in variants if c['decision']==original),None)
            best_score,best=ranked[0]
            current_score=consistency_score(windows,index,original) if current else -1
            if best_score>=2 and (not current or best_score>current_score):
                chosen=best['decision'];reason='consistent_existing_source_term';evidence=best_score
            elif current:
                chosen=current['decision'];reason='existing_complete_source_selection'
            else:
                # The right-hand source continues the thought after a clipped
                # tail. This is an auditable source choice, not a new word.
                fuller=next((c for c in variants if c['decision']['trim_left']>0),None)
                if fuller:
                    chosen=fuller['decision'];reason='fuller_neighbor_source_phrase'
            if chosen:
                count=chosen['trim_left'] or chosen['trim_right']
                a=windows[index]['tokens'][-count:];b=windows[index+1]['tokens'][:count]
                changed={t.casefold() for x,y in zip(a,b) if x.casefold()!=y.casefold() for t in [x,y]}
                if changed & protected:
                    chosen=None;reason='protected_term_conflict'
        elif pair in flags:
            reason='unresolved_source_disagreement'
        else:
            continue

        critical=any(code in {'possible_omission','sentence_fragment'} for code in flags.get(pair,[]))
        if critical:
            chosen=None;reason='unresolved_content_coverage'
        if chosen is None and recovery is not None and calls<max_recovery_calls:
            calls+=1
            try:
                # The callback's production implementation calls Bodhan only.
                # It returns a source transcript for the straddling audio.
                recovered=recovery({'window_ids':list(pair),
                                    'start_seconds':max(0,windows[index+1]['start_seconds']-5),
                                    'end_seconds':min(windows[-1]['end_seconds'],windows[index]['end_seconds']+5)})
                text=recovered['text']
                quality_check(text)
                recovery_evidence=recovered.get('evidence',{'text':text})
                words=[w.casefold() for w in WORD.findall(text)]
                supported={}
                for candidate in variants:
                    decision=candidate['decision']
                    count=decision['trim_left'] or decision['trim_right']
                    kept=(windows[index+1]['tokens'][:count] if decision['trim_left'] else windows[index]['tokens'][-count:])
                    phrase=[w.casefold() for w in kept]
                    if any(words[j:j+count]==phrase for j in range(len(words)-count+1)):
                        ref=retained_variant(windows,index,decision)
                        previous=supported.get(ref)
                        if previous is None or count>previous['trim_left']+previous['trim_right']:
                            supported[ref]=decision
                if len(supported)==1 and not critical:
                    chosen=next(iter(supported.values()));reason='bodhan_recovery_corroboration'
            except (MediaError,KeyError,TypeError,ValueError):
                reason='recovery_failed'
        if chosen is not None:
            trial=deepcopy(decisions);trial[index]=chosen
            try:compile_joins(windows,{'joins':trial,'sentence_ends':[],'commas':[],'issues':[]})
            except MediaError:
                chosen=None;reason='conflicting_neighbor_source_support'
            else:
                decisions=trial
                if reason=='consistent_existing_source_term':
                    count=chosen['trim_left'] or chosen['trim_right']
                    a=windows[index]['tokens'][-count:];b=windows[index+1]['tokens'][:count]
                    for x,y in zip(a,b):
                        if x.casefold()!=y.casefold():
                            old_word,new_word=(x,y) if chosen['trim_left'] else (y,x)
                            term_choices.append((old_word,new_word,list(pair),evidence))
        if chosen is None:
            blocked.update(pair)
        if chosen is not None or reason is not None or pair in flags:
            events.append({'window_ids':list(pair),'status':'resolved' if chosen else 'unresolved',
                           'reason':reason,'decision':chosen,'source_consistency_score':evidence,
                           'original_flag_codes':flags.get(pair,[]),'recovery_evidence':recovery_evidence})

    handled={tuple(e['window_ids']) for e in events}
    for issue in old['issues']:
        if tuple(issue['window_ids']) not in handled:
            blocked.update(issue['window_ids'])
            events.append({'window_ids':issue['window_ids'],'status':'unresolved','reason':'non_boundary_quality_issue',
                           'decision':None,'original_flag_codes':[issue['code']]})
    plan=compile_joins(windows,{'joins':decisions,'sentence_ends':[],'commas':[],'issues':[]})
    plan=render_with_original_punctuation(windows,old,plan)
    # Normalize repeated technical wording only after an observed overlap has
    # established the two source spellings and other independent source windows
    # corroborate the complete replacement phrase. Retain raw tokens/evidence.
    normalized=deepcopy(windows)
    normalizations=[]
    for wrong,preferred,pair,score in term_choices:
        if wrong.casefold() in protected or preferred.casefold() in protected:continue
        if SequenceMatcher(None,wrong.casefold(),preferred.casefold()).ratio()<.6 and score<4:continue
        for raw,updated in zip(windows,normalized):
            for pos in range(len(raw['tokens'])-1):
                if raw['tokens'][pos].casefold()!=wrong.casefold():continue
                next_word=raw['tokens'][pos+1].casefold()
                phrase=(preferred.casefold(),next_word)
                support=[]
                for candidate in windows:
                    for j in range(len(candidate['tokens'])-1):
                        if tuple(t.casefold() for t in candidate['tokens'][j:j+2])==phrase:
                            support.append((candidate,j));break
                independent=[];end=-1
                for candidate,j in sorted(support,key=lambda x:x[0]['end_seconds']):
                    if candidate['start_seconds']>=end:
                        independent.append((candidate,j));end=candidate['end_seconds']
                if len(independent)<2:continue
                source,j=independent[0]
                replacement=source['tokens'][j]
                updated['tokens'][pos]=replacement
                normalizations.append({'window_id':raw['id'],'token':pos,'old':raw['tokens'][pos],
                    'new':replacement,'reason':'repeated_source_term_consistency',
                    'source_phrase':source['tokens'][j:j+2],
                    'support':[{'window_id':w['id'],'start':k,'end':k+2} for w,k in independent],
                    'trigger_overlap':pair})
    # Lexical normalization changes term variants into exact matches; validate
    # the same provenance plan against the derived source-token ledger.
    for exclusion in plan['excluded']:
        if exclusion['reason']=='overlap_variant':
            by_id={w['id']:w for w in normalized}
            a,b=exclusion['span'],exclusion['support']
            x=by_id[a['window_id']]['tokens'][a['start']:a['end']]
            y=by_id[b['window_id']]['tokens'][b['start']:b['end']]
            if [v.casefold() for v in x]==[v.casefold() for v in y]:exclusion['reason']='overlap_duplicate'
    windows=normalized
    result=validate_plan(windows,plan)
    result['source_validation_warnings']=result.pop('issues')
    result['issues']=[{'code':'unresolved_correction','window_ids':e['window_ids'],'reason':e['reason']} for e in events if e['status']=='unresolved']
    result['original_issues']=draft['issues']
    result['validation']['raw_token_changes_have_explicit_source_evidence']=True
    for sentence in result['sentences']:
        impacted={s['window_id'] for s in sentence['source_spans']} & blocked
        sentence['status']='skipped' if impacted else 'ready_for_sentence_grouping'
        if impacted:sentence['skip_reason']='Unresolved ASR conflict after the configured recovery budget.'
    result.update(status='partial_failure' if blocked else 'corrected_source_backed',
                  correction_events=events,blocked_window_ids=sorted(blocked),
                  recovery_calls=calls,recovery_budget=max_recovery_calls,
                  original_flags_retained_as_audit=True,
                  requires_sentence_grouping=True,term_normalizations=normalizations,
                  source_windows=windows,raw_source_tokens_preserved_in_original_run=True)
    return result


class BodhanBoundaryRecovery:
    """A bounded caller uses this adapter; it cannot call another provider."""
    def __init__(self,run,client,out):
        self.run,self.client,self.out=Path(run),client,Path(out)

    def __call__(self,request):
        a,b=request['start_seconds'],request['end_seconds']
        if any(type(n) not in {int,float} or not math.isfinite(n) for n in [a,b]) or not 0<=a<b or b-a>15:
            raise MediaError('invalid_recovery_audio','Recovery audio must be a finite source interval of at most fifteen seconds.')
        with wave.open(str(self.run/'source_audio.wav'),'rb') as source:
            if (source.getframerate(),source.getnchannels(),source.getsampwidth())!=(16000,1,2):
                raise MediaError('invalid_recovery_audio','Expected the prepared source WAV.')
            first,last=round(a*16000),round(b*16000)
            if last>source.getnframes():raise MediaError('invalid_recovery_audio','Recovery interval exceeds the source.')
            source.setpos(first);pcm=source.readframes(last-first)
        if len(pcm)!=(last-first)*2:raise MediaError('invalid_recovery_audio','Truncated source recovery audio.')
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as target:
            target.setparams((1,2,16000,0,'NONE','not compressed'));target.writeframes(pcm)
        blob=buffer.getvalue();digest=hashlib.sha256(blob).hexdigest()
        self.out.mkdir(parents=True,exist_ok=True)
        path=self.out/('recovery-'+digest+'.wav')
        if path.exists():
            if path.read_bytes()!=blob:raise MediaError('invalid_recovery_audio','Existing recovery audio does not match the source.')
        else:
            with path.open('xb') as output:output.write(blob)
        result=self.client.transcribe(path)
        quality_check(result['text'])
        evidence={'source_start_seconds':first/16000,'source_end_seconds':last/16000,'result':result}
        atomic_json(path.with_suffix('.json'),evidence)
        return {'text':result['text'],'evidence':evidence}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Correct saved English overlaps without inventing words.')
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--draft',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--allow-bodhan-recovery',action='store_true',help='Allow bounded, potentially billable Bodhan-only recovery.')
    parser.add_argument('--recovery-budget',type=int,default=2)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--protect',action='append',default=[],help='Names, identifiers or terms requiring extra evidence.')
    args=parser.parse_args(argv)
    try:
        if args.out.exists():raise MediaError('output_exists','Refusing to overwrite a correction result.')
        draft=json.loads(args.draft.read_text())
        windows=read_experiment(args.experiment)
        recovery=None
        if args.allow_bodhan_recovery:
            run=args.experiment.resolve().parent.parent
            client=TranscriptionClient(load_key(args.env_file),run/'.transcription_cache',RetrySettings(attempts=1))
            recovery=BodhanBoundaryRecovery(run,client,args.out.parent/'correction_recovery')
        result=correct(windows,draft,recovery=recovery,max_recovery_calls=args.recovery_budget if recovery else 0,protected_terms=args.protect)
        atomic_json(args.out,result)
        print(json.dumps({'status':result['status'],'resolved':sum(e['status']=='resolved' for e in result['correction_events']),
                          'unresolved':sum(e['status']=='unresolved' for e in result['correction_events']),'recovery_calls':result['recovery_calls']}))
        return int(result['status']=='partial_failure')
    except (MediaError,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_correction_input'),
                          'message':str(exc) if isinstance(exc,MediaError) else 'Cannot read correction inputs.'}))
        return 1


if __name__=='__main__':raise SystemExit(main())
