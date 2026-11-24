import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import (LLMSettings, load_settings, main, read_experiment,
    reconcile, sdk_completion, source_windows, validate_plan, compile_joins, compile_choices, join_candidates, validate_connected_text, source_checked_reconstruction)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('bodhan_dub.reconciliation._SDK_PACER',SimpleNamespace(wait=lambda:None,observe=lambda _:None))
    import httpx
    monkeypatch.setattr(httpx.Client,'send',lambda *a,**k:pytest.fail('No real LLM calls in tests'))


def windows(a='We discuss right angles',b='right angles today',overlap=True):
    return source_windows([{'id':'a','text':a,'start_seconds':0,'end_seconds':15},
                           {'id':'b','text':b,'start_seconds':13 if overlap else 15,'end_seconds':28}])


def span(identifier,start,end,after=''):
    return {'window_id':identifier,'start':start,'end':end,'after':after}


def ref(identifier,start,end):
    return {'window_id':identifier,'start':start,'end':end}


def valid_plan():
    return {'sentences':[{'spans':[span('a',0,4),span('b',2,3,'.')]}],
            'excluded':[{'span':ref('b',0,2),'support':ref('a',2,4),'reason':'overlap_duplicate'}],
            'issues':[]}


def test_exact_source_rendering_and_overlap():
    r=validate_plan(windows(),valid_plan())
    assert r['text']=='We discuss right angles today.'
    assert r['validation']['all_source_words_accounted']
    assert r['status']=='source_backed_draft_needs_qa'
    assert r['sentences'][0]['source_window_envelope_end_seconds']==28
    assert 'timestamps' not in r['sentences'][0]


def test_full_context_variant_selects_supported_plural():
    w=windows('This chapter is about lines the angle','chapter is about lines the angles they make')
    p={'sentences':[{'spans':[span('a',0,1),span('b',0,8,'.')]}],
       'excluded':[{'span':ref('a',1,7),'support':ref('b',0,6),'reason':'overlap_variant'}],'issues':[]}
    r=validate_plan(w,p)
    assert r['text']=='This chapter is about lines the angles they make.'
    assert r['issues'][0]['code']=='uncertain_boundary'


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(text='Invented explanation'),
    lambda p:p['sentences'][0]['spans'][0].update(text='new words'),
    lambda p:p['sentences'][0]['spans'][0].update(window_id='unknown'),
    lambda p:p['sentences'][0]['spans'][0].update(start=True),
    lambda p:p['sentences'][0]['spans'][0].update(end=99),
    lambda p:p['sentences'][0]['spans'][0].update(after=' ninety'),
    lambda p:p['sentences'][0]['spans'][0].update(after='。'),
    lambda p:p['sentences'][0]['spans'][0].update(end=3),
    lambda p:p['sentences'][0]['spans'].reverse(),
    lambda p:p['sentences'][0]['spans'].append(span('a',0,1)),
    lambda p:p['excluded'][0].update(reason='grammar_cleanup'),
    lambda p:p['excluded'][0]['support'].update(end=3),
    lambda p:p['excluded'].clear(),
    lambda p:p['issues'].append({'code':'made_up','window_ids':['a']}),
    lambda p:p['issues'].append({'code':'possible_omission','window_ids':['missing']}),
])
def test_rejects_invention_unexplained_deletion_bad_references_and_reordering(mutation):
    p=valid_plan();mutation(p)
    with pytest.raises(MediaError):
        validate_plan(windows(),p)


def test_reject_delete_without_shared_audio():
    with pytest.raises(MediaError):
        validate_plan(windows(overlap=False),valid_plan())


def test_genuine_repetition_preserved():
    w=windows('It is very very important','We agree now')
    p={'sentences':[{'spans':[span('a',0,5,'.')]},{'spans':[span('b',0,3,'.')]}],'excluded':[],'issues':[]}
    assert 'very very' in validate_plan(w,p)['text']


def test_ambiguous_repeated_anchor_cannot_be_deleted():
    w=windows('yes yes','yes yes')
    p={'sentences':[{'spans':[span('a',0,2,'.')]}],
       'excluded':[{'span':ref('b',0,2),'support':ref('a',0,2),'reason':'overlap_duplicate'}],'issues':[]}
    with pytest.raises(MediaError):
        validate_plan(w,p)


def test_changed_number_cannot_be_normalized_away():
    w=windows('The angle is ninety degrees','The angle is eighty degrees')
    p={'sentences':[{'spans':[span('b',0,5,'.')]}],
       'excluded':[{'span':ref('a',0,5),'support':ref('b',0,5),'reason':'overlap_variant'}],'issues':[]}
    with pytest.raises(MediaError):
        validate_plan(w,p)


def test_inside_window_cannot_be_deleted_as_overlap():
    text='right angles '+' '.join('word'+str(i) for i in range(30))
    w=windows(text,'right angles today')
    p={'sentences':[{'spans':[span('a',2,32),span('b',0,3,'.')]}],
       'excluded':[{'span':ref('a',0,2),'support':ref('b',0,2),'reason':'overlap_duplicate'}],'issues':[]}
    with pytest.raises(MediaError):
        validate_plan(w,p)


@pytest.mark.parametrize('text',['','ୄ'*200,'1234','bonjour'*100])
def test_bad_asr_source_not_sent_to_llm(text):
    with pytest.raises(MediaError):
        windows(text)


@pytest.mark.parametrize('url',['http://remote.example/v1','https://key@host/v1','https://host/v1?key=secret'])
def test_invalid_endpoint(url):
    with pytest.raises(MediaError):
        LLMSettings(url,'model','fake-key')


def test_settings_explicit_env_and_precedence(tmp_path,monkeypatch):
    for n in ['LLM_BASE_URL','LLM_MODEL','LLM_API_KEY']:
        monkeypatch.delenv(n,raising=False)
    p=tmp_path/'.env';p.write_text('LLM_BASE_URL=https://provider.example/v1\nLLM_MODEL=model\nLLM_API_KEY="fake-key"\n')
    monkeypatch.setenv('LLM_MODEL','override')
    s=load_settings(p)
    assert s.model=='override' and 'fake-key' not in repr(s)
    with pytest.raises(MediaError):
        load_settings()


def test_cached_calls_use_no_key_in_identity_and_no_new_call(tmp_path):
    calls=[]
    def fake(settings,messages):
        calls.append(messages)
        assert set(json.loads(messages[1]['content']))=={'windows','validation_feedback'}
        return json.dumps({'text':'We discuss right angles today.','issues':[]}),{'total_tokens':100}
    s=LLMSettings('https://provider.example/v1','model','fake-key')
    first=reconcile(windows(),s,tmp_path,fake)
    second=reconcile(windows(),LLMSettings(s.base_url,s.model,'rotated-key'),tmp_path,fake)
    assert first['new_llm_calls']==1 and second['cache_hit'] and len(calls)==1
    assert 'fake-key' not in ''.join(p.read_text() for p in tmp_path.glob('*.json'))


def test_invalid_model_output_never_rendered_or_automatically_retried(tmp_path):
    calls=[]
    def fake(*args):
        calls.append(1)
        return json.dumps({'text':'Invented words'}),None
    s=LLMSettings('https://provider.example/v1','model','fake-key')
    for _ in range(2):
        with pytest.raises(MediaError):
            reconcile(windows(),s,tmp_path,fake)
    assert len(calls)==1


def test_interrupted_llm_call_not_repeated(tmp_path):
    def fake(*args):
        raise KeyboardInterrupt()
    s=LLMSettings('https://provider.example/v1','model','fake-key')
    with pytest.raises(KeyboardInterrupt):
        reconcile(windows(),s,tmp_path,fake)
    with pytest.raises(MediaError):
        reconcile(windows(),s,tmp_path,lambda *_:pytest.fail('Must not repeat'))


def test_sdk_uses_portable_settings_without_retries_or_audio(monkeypatch):
    import openai
    recorded={}
    class Fake:
        def __init__(self,**kwargs):
            recorded.update(kwargs)
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def create(self,**kwargs):
            recorded['request']=kwargs
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{}',tool_calls=None))],usage=None)
    monkeypatch.setattr(openai,'OpenAI',Fake)
    s=LLMSettings('https://provider.example/v1','model','fake-key')
    assert sdk_completion(s,[{'role':'user','content':'saved text only'}])==('{}',None)
    assert recorded['max_retries']==0 and recorded['base_url']==s.base_url
    assert set(recorded['request'])=={'model','messages','max_tokens'}


def test_known_reasoning_model_uses_low_effort_with_portable_400_fallback(monkeypatch):
    import openai
    calls=[]
    class Unsupported(Exception):status_code=400
    class Fake:
        def __init__(self,**_):self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def create(self,**kwargs):
            calls.append(kwargs)
            if 'reasoning_effort' in kwargs:raise Unsupported()
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{}',tool_calls=None))],usage=None)
    monkeypatch.setattr(openai,'OpenAI',Fake)
    assert sdk_completion(LLMSettings('https://example.test/v1','openai/gpt-oss-120b','fake'),[])==('{}',None)
    assert calls[0]['reasoning_effort']=='low' and 'reasoning_effort' not in calls[1]


@pytest.mark.parametrize('failures',[1,3])
def test_sdk_respects_rate_cooldown_and_stops_after_three_requests(monkeypatch,failures):
    import openai
    calls=[];observed=[]
    monkeypatch.setattr('bodhan_dub.reconciliation._SDK_PACER',SimpleNamespace(wait=lambda:None,observe=lambda response:observed.append(response.status)))
    class Limited(Exception):
        status_code=429
        response=SimpleNamespace(headers={'retry-after':'1'})
    class Fake:
        def __init__(self,**_):self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def create(self,**_):
            calls.append(True)
            if len(calls)<=failures:raise Limited()
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{}',tool_calls=None))],usage=None)
    monkeypatch.setattr(openai,'OpenAI',Fake)
    settings=LLMSettings('https://example.test/v1','model','fake')
    if failures==3:
        with pytest.raises(Limited) as caught:sdk_completion(settings,[])
        assert caught.value.retry_budget_exhausted
    else:assert sdk_completion(settings,[])==('{}',None)
    assert len(calls)==min(failures+1,3) and observed==[429]*min(failures,2)


def test_experiment_dry_run_never_reads_keys_or_calls(tmp_path,monkeypatch,capsys):
    p=tmp_path/'experiment_report.json'
    p.write_text(json.dumps({'windows':[{'id':'a','status':'transcribed','start_seconds':0,'end_seconds':15,'result':{'text':'An existing sentence'}}]}))
    monkeypatch.setattr('bodhan_dub.reconciliation.load_settings',lambda *_:pytest.fail('No keys in dry run'))
    assert main(['--experiment',str(p)])==0
    assert json.loads(capsys.readouterr().out)['llm_calls']==0
    assert not (tmp_path/'reconciliation.json').exists()


def test_failed_window_never_silently_omitted(tmp_path):
    p=tmp_path/'experiment.json'
    p.write_text(json.dumps({'windows':[{'id':'a','status':'failed'}]}))
    with pytest.raises(MediaError):
        read_experiment(p)


@pytest.mark.parametrize('plan',[None,[],{'sentences':[],'excluded':[],'issues':[]},
    {'sentences':[{'spans':[{'window_id':[], 'start':0,'end':1,'after':''}]}],'excluded':[],'issues':[]}])
def test_malformed_plan_is_sanitized(plan):
    with pytest.raises(MediaError):
        validate_plan(windows(),plan)


def compact_plan():
    return {'joins':[{'left':'a','right':'b','trim_left':0,'trim_right':2,'reason':'overlap_duplicate'}],
            'sentence_ends':[{'window_id':'b','token':2,'mark':'.'}],'commas':[],'issues':[]}


def test_compact_plan_compiles_with_full_provenance():
    w=windows()
    plan=compile_joins(w,compact_plan())
    assert validate_plan(w,plan)['text']=='We discuss right angles today.'


@pytest.mark.parametrize('mutation',[
    lambda p:p['joins'][0].update(trim_right=3),
    lambda p:p['joins'][0].update(trim_left=2),
    lambda p:p['joins'][0].update(trim_right=True),
    lambda p:p['joins'][0].update(left='missing'),
    lambda p:p['joins'].clear(),
    lambda p:p['sentence_ends'][0].update(token=0),
    lambda p:p['sentence_ends'][0].update(mark='invented'),
    lambda p:p.update(text='invented prose'),
])
def test_compact_bad_choices_rejected(mutation):
    p=compact_plan();mutation(p)
    with pytest.raises(MediaError):
        compile_joins(windows(),p)


def test_compact_uncertain_repetition_kept_instead_of_deleted():
    w=windows('yes yes','yes yes')
    p={'joins':[{'left':'a','right':'b','trim_left':0,'trim_right':0,'reason':'none'}],
       'sentence_ends':[],'commas':[],
       'issues':[{'code':'ambiguous_repetition','window_ids':['a','b']}]}
    result=validate_plan(w,compile_joins(w,p))
    assert result['text']=='yes yes yes yes'
    assert result['issues'][0]['code']=='ambiguous_repetition'


def test_compact_rejected_proposal_saved_without_becoming_transcript(tmp_path):
    s=LLMSettings('https://provider.example/v1','model','fake-key')
    candidate=compact_plan();candidate['joins'][0]['trim_right']=3
    with pytest.raises(MediaError):
        reconcile(windows(),s,tmp_path,lambda *_:(json.dumps(candidate),None))
    state=json.loads(next(tmp_path.glob('*.json')).read_text())
    assert state['untrusted_compact_proposal']==candidate
    assert state['state']=='invalid_or_unknown'


def choice_plan():
    return {'join_choices':['boundary-1-drop-right-2'],
            'sentence_ends':[{'window_id':'b','token':2,'mark':'.'}],'commas':[],'issues':[]}


def test_only_source_validated_choices_are_offered():
    options=join_candidates(windows())[0]['choices']
    assert {x['id'] for x in options}=={'boundary-1-keep-both','boundary-1-drop-left-2','boundary-1-drop-right-2'}
    for option in options:
        proposal={**choice_plan(),'join_choices':[option['id']]}
        # keep/drop-left retain the same b token used for terminal punctuation.
        validate_plan(windows(),compile_choices(windows(),proposal))


def test_choice_plan_builds_expected_source_text():
    assert validate_plan(windows(),compile_choices(windows(),choice_plan()))['text']=='We discuss right angles today.'


def test_unoffered_choice_rejected():
    proposal=choice_plan();proposal['join_choices']=['boundary-1-drop-right-3']
    with pytest.raises(MediaError):
        compile_choices(windows(),proposal)


def test_genuine_repeated_anchor_never_offered_for_deletion():
    assert len(join_candidates(windows('yes yes','yes yes'))[0]['choices'])==1


def test_plain_text_reconciliation_proves_source_coverage():
    plan,result=validate_connected_text(windows(),{'text':'We discuss right angles today.','issues':[]})
    assert result['text']=='We discuss right angles today.'
    assert result['validation']['all_source_words_accounted']
    assert result['presentation_text']=='We discuss right angles today.'


@pytest.mark.parametrize('text',[
    'We discuss right angles today and tomorrow.',
    'We discuss angles today.',
    'Today we discuss right angles.',
    'We discuss ninety degree angles today.',
    'We discuss right angles today <script>.',
])
def test_plain_text_cannot_add_delete_reorder_or_inject(text):
    with pytest.raises(MediaError):
        validate_connected_text(windows(),{'text':text,'issues':[]})


def test_plain_text_supported_variant_preserves_unique_preceding_word():
    w=windows('This chapter is about lines the angle','chapter is about lines the angles they make')
    _,result=validate_connected_text(w,{'text':'This chapter is about lines the angles they make.','issues':[]})
    assert result['issues'][0]['code']=='uncertain_boundary'


def test_plain_text_never_deletes_repetitions_without_supported_overlap():
    w=windows('It is very very important','We agree now')
    with pytest.raises(MediaError):
        validate_connected_text(w,{'text':'It is very important. We agree now.','issues':[]})
    _,r=validate_connected_text(w,{'text':'It is very very important. We agree now.','issues':[]})
    assert 'very very' in r['text']


def test_tampered_cached_presentation_never_accepted(tmp_path):
    settings=LLMSettings('https://provider.example/v1','model','fake-key')
    fake=lambda *_:(json.dumps({'text':'We discuss right angles today.','issues':[]}),None)
    reconcile(windows(),settings,tmp_path,fake)
    path=next(tmp_path.glob('*.json'))
    state=json.loads(path.read_text());state['presentation_text']='We discuss right angles today and tomorrow.'
    path.write_text(json.dumps(state))
    with pytest.raises(MediaError):
        reconcile(windows(),settings,tmp_path,lambda *_:pytest.fail('No new network call'))


def test_generated_duplicate_issues_are_consolidated():
    w=windows('This chapter is about lines the angle','chapter is about lines the angles they make')
    _,r=validate_connected_text(w,{'text':'This chapter is about lines the angles they make.',
        'issues':[{'code':'uncertain_boundary','window_ids':['a','b']}]})
    assert len(r['issues'])==1


@pytest.mark.parametrize('budget',[True,0,255,32769])
def test_invalid_output_budget(budget):
    with pytest.raises(MediaError):
        LLMSettings('https://provider.example/v1','model','fake-key',budget)


def test_output_budget_changes_request_identity(tmp_path):
    calls=[]
    def fake(settings,messages):
        calls.append(settings.max_output_tokens)
        return json.dumps({'text':'We discuss right angles today.','issues':[]}),None
    for budget in [4096,8192]:
        reconcile(windows(),LLMSettings('https://provider.example/v1','model','fake-key',budget),tmp_path,fake)
    assert calls==[4096,8192]


@pytest.mark.parametrize('a,b',[
    ('Fifth work','Fifth word'),
    ('make a leanier pair','make a linear pair'),
    ('and angle BOD are vertical','and angle BOD are vertically'),
])
def test_source_supported_recognition_variants_are_audited(a,b):
    w=windows(a,b+' continue')
    _,r=validate_connected_text(w,{'text':b+' continue.','issues':[]})
    assert r['issues'][0]['code']=='uncertain_boundary'
    assert r['validation']['all_spoken_words_from_bodhan']


def test_unique_intro_cannot_be_dropped_when_deduplicating():
    w=windows('Here angle AOC is one twenty degrees','AOC is one twenty degrees Next example')
    with pytest.raises(MediaError):
        validate_connected_text(w,{'text':'AOC is one twenty degrees Next example.','issues':[]})


def test_output_only_grammar_additions_are_rejected_not_spoken():
    _,r=source_checked_reconstruction(windows(),{'text':'We discuss the right angles today.','issues':[]})
    assert r['text']=='We discuss right angles today.'
    assert r['model_added_words_rejected'][0]['word']=='the'


def test_missing_source_words_cannot_be_filled_by_reconstruction():
    with pytest.raises(MediaError):
        source_checked_reconstruction(windows(),{'text':'We discuss angles today.','issues':[]})


def test_inserted_negation_or_number_not_repaired_away():
    for text in ['We discuss not right angles today.','We discuss ninety right angles today.']:
        with pytest.raises(MediaError):
            source_checked_reconstruction(windows(),{'text':text,'issues':[]})


def test_hyphen_presentation_does_not_create_a_spoken_word():
    w=source_windows([{'id':'a','text':'The non common arms','start_seconds':0,'end_seconds':15}])
    _,r=source_checked_reconstruction(w,{'text':'The non‑common arms.','issues':[]})
    assert r['text']=='The non common arms.'
