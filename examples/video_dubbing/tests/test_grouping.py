import json
from pathlib import Path

import pytest

from bodhan_dub.grouping import GroupSettings,build_groups,fallback_plan,grouping_input,llm_plan,main,validate_groups
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings


def fixture(texts=None):
    texts=texts or ['First word','Collinear means points on one line','And these points share one line']
    tokens=[word for text in texts for word in text.split()]
    sentences=[];cursor=0
    for text in texts:
        size=len(text.split())
        sentences.append({'text':text+'.','source_spans':[{'window_id':'w0','start':cursor,'end':cursor+size,'after':'.'}],
                          'status':'ready_for_sentence_grouping'})
        cursor+=size
    corrected={'status':'corrected_source_backed','source_windows':[{'id':'w0','tokens':tokens,'start_seconds':0,'end_seconds':15}],
               'sentences':sentences,'excluded':[],'issues':[]}
    detector={'sample_rate':16000,'total_samples':15*16000,
              'speech_regions':[{'start_sample':16000,'end_sample':5*16000},{'start_sample':8*16000,'end_sample':14*16000}]}
    return corrected,detector


def test_groups_preserve_words_and_source_references():
    corrected,detector=fixture()
    result=build_groups(corrected,detector,{'ends':[2],'issues':[]})
    assert result['units'][0]['sentence_ids']==[0,1,2]
    assert result['validation']['all_corrected_words_preserved_in_order']
    assert result['units'][0]['text']=='First word. Collinear means points on one line. And these points share one line.'
    assert not result['timeline_ready_for_synthesis']
    assert result['units'][0]['timing_resolution']=='coarse_source_window_bounds_not_final_sync'


def test_pauses_are_reported_not_removed_or_filled_with_speech():
    corrected,detector=fixture()
    result=build_groups(corrected,detector)
    pauses=result['units'][0]['protected_non_speech_gaps']
    assert {'start_seconds':5,'end_seconds':8} in pauses
    assert result['source_non_speech_gaps']


@pytest.mark.parametrize('ends',[[0],[1,0,2],[0,0,2],[-1,2],[True,2],[3],[]])
def test_invalid_coverage_or_order_rejected(ends):
    corrected,_=fixture();rows=grouping_input(corrected)
    with pytest.raises(MediaError):validate_groups(rows,{'ends':ends,'issues':[]},GroupSettings())


def test_llm_cannot_rewrite_or_add_text():
    corrected,_=fixture();rows=grouping_input(corrected)
    with pytest.raises(MediaError):validate_groups(rows,{'ends':[2],'issues':[],'text':'Invented words'},GroupSettings())


def test_oversize_existing_sentence_not_chopped_or_discarded():
    corrected,detector=fixture([' '.join(['word']*65)])
    result=build_groups(corrected,detector)
    assert len(result['units'])==1
    assert result['units'][0]['issues']==['oversize_sentence']
    assert len(result['units'][0]['text'].split())==65


def test_merging_over_limit_rejected():
    corrected,_=fixture([' '.join(['word']*35),' '.join(['other']*35)])
    with pytest.raises(MediaError):validate_groups(grouping_input(corrected),{'ends':[1],'issues':[]},GroupSettings())


def test_ready_and_skipped_sources_do_not_mix():
    corrected,detector=fixture()
    corrected['sentences'][1].update(status='skipped',skip_reason='Unresolved recognition')
    with pytest.raises(MediaError):build_groups(corrected,detector,{'ends':[2],'issues':[]})
    result=build_groups(corrected,detector)
    assert [u['status'] for u in result['units']]==['ready_for_translation','skipped','ready_for_translation']


def test_definite_source_gap_prevents_merging():
    corrected,_=fixture();rows=grouping_input(corrected)
    rows[0]['window_end_seconds']=3
    rows[1]['window_start_seconds']=8
    with pytest.raises(MediaError):validate_groups(rows,{'ends':[2],'issues':[]},GroupSettings())


def test_source_word_edit_detected_before_grouping():
    corrected,_=fixture();corrected['sentences'][0]['text']='A fabricated definition.'
    with pytest.raises(MediaError):grouping_input(corrected)


def test_bad_timeline_rejected():
    corrected,detector=fixture();detector['sample_rate']=24000
    with pytest.raises(MediaError):build_groups(corrected,detector)


def test_valid_llm_plan_is_cached_and_revalidated(tmp_path):
    corrected,_=fixture();rows=grouping_input(corrected);calls=[]
    def complete(settings,messages):
        calls.append(messages)
        assert 'audio' not in json.loads(messages[1]['content'])
        return json.dumps({'ends':[2],'issues':[]}),{'total_tokens':20}
    llm=LLMSettings('https://provider.example/v1','model','fake-key')
    first,meta=llm_plan(rows,GroupSettings(),llm,tmp_path,complete)
    second,cached=llm_plan(rows,GroupSettings(),llm,tmp_path,complete)
    assert first==second and len(calls)==1 and cached['new_calls']==0
    assert 'fake-key' not in next(tmp_path.glob('*.json')).read_text()


@pytest.mark.parametrize('response',['not json','{"ends":[0],"issues":[]}','{"ends":[2],"issues":[],"text":"rewrite"}'])
def test_invalid_llm_plan_falls_back_without_losing_words_or_retrying(tmp_path,response):
    corrected,_=fixture();rows=grouping_input(corrected);calls=[]
    def complete(*args):calls.append(1);return response,None
    llm=LLMSettings('https://provider.example/v1','model','fake-key')
    plan,meta=llm_plan(rows,GroupSettings(),llm,tmp_path,complete)
    assert meta['mode']=='deterministic_fallback'
    validate_groups(rows,plan,GroupSettings())
    llm_plan(rows,GroupSettings(),llm,tmp_path,complete)
    assert len(calls)==1


@pytest.mark.parametrize('settings',[{'max_words':0},{'max_characters':True},{'max_window_span_seconds':121}])
def test_invalid_settings(settings):
    with pytest.raises(MediaError):GroupSettings(**settings)


def test_cli_deterministic_default_has_no_key_or_api_dependency(tmp_path,monkeypatch,capsys):
    corrected,detector=fixture()
    a=tmp_path/'corrections.json';b=tmp_path/'vad.json';out=tmp_path/'units.json'
    a.write_text(json.dumps(corrected));b.write_text(json.dumps(detector))
    monkeypatch.setattr('bodhan_dub.grouping.load_settings',lambda *_:pytest.fail('No keys'))
    assert main(['--corrections',str(a),'--vad',str(b),'--out',str(out)])==0
    assert json.loads(capsys.readouterr().out)['planning']['new_calls']==0
    assert main(['--corrections',str(a),'--vad',str(b),'--out',str(out)])==1


def test_invalid_timeline_fails_before_any_llm_key_read(tmp_path,monkeypatch):
    corrected,detector=fixture();detector['sample_rate']=24000
    a=tmp_path/'c.json';b=tmp_path/'v.json'
    a.write_text(json.dumps(corrected));b.write_text(json.dumps(detector))
    monkeypatch.setattr('bodhan_dub.grouping.load_settings',lambda *_:pytest.fail('Preflight first'))
    assert main(['--corrections',str(a),'--vad',str(b),'--out',str(tmp_path/'out.json'),'--use-llm'])==1


def test_bad_cached_plan_falls_back_without_another_call(tmp_path):
    corrected,_=fixture();rows=grouping_input(corrected)
    llm=LLMSettings('https://provider.example/v1','model','fake-key')
    complete=lambda *_:(json.dumps({'ends':[2],'issues':[]}),None)
    llm_plan(rows,GroupSettings(),llm,tmp_path,complete)
    next(tmp_path.glob('*.json')).write_text('broken json')
    plan,metadata=llm_plan(rows,GroupSettings(),llm,tmp_path,lambda *_:pytest.fail('No new call'))
    assert metadata['new_calls']==0 and metadata['mode']=='deterministic_fallback'


def test_concurrent_same_input_grouping_only_calls_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    corrected,_=fixture();rows=grouping_input(corrected);calls=[]
    def complete(*_):calls.append(1);time.sleep(.01);return json.dumps({'ends':[2],'issues':[]}),None
    llm=LLMSettings('https://provider.example/v1','model','fake-key')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:llm_plan(rows,GroupSettings(),llm,tmp_path,complete),range(2)))
    assert len(calls)==1
    assert sorted(r[1]['new_calls'] for r in results)==[0,1]


def test_fallback_keeps_adjacent_complete_sentences_in_bounded_groups():
    texts=['Open the notebook and read the definition','These statements describe the same connected example','Now compare the labels in the same figure']
    corrected,detector=fixture(texts)
    result=build_groups(corrected,detector)
    assert len(result['units'])==1
    assert result['units'][0]['sentence_ids']==[0,1,2]
    assert result['validation']['all_corrected_words_preserved_in_order']
    rows=grouping_input(corrected)
    limited=GroupSettings(max_words=15)
    plan=fallback_plan(rows,limited)
    assert plan['ends']==[1,2]
    validate_groups(rows,plan,limited)


def test_bounded_fallback_still_respects_observed_gap_and_source_failure():
    corrected,_=fixture(['Read the first complete statement here','Read the next complete statement here','Read the final complete statement here'])
    rows=grouping_input(corrected)
    rows[0]['window_end_seconds']=3
    rows[1]['window_start_seconds']=5
    assert fallback_plan(rows,GroupSettings())['ends']==[0,2]
    rows[2]['status']='skipped'
    assert fallback_plan(rows,GroupSettings())['ends']==[0,1,2]
