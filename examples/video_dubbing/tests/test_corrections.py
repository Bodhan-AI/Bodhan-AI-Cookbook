from copy import deepcopy

import pytest

from bodhan_dub.corrections import correct, BodhanBoundaryRecovery
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import compile_joins,source_windows,validate_plan


def sources(texts):
    return source_windows([{'id':f'w{i}','text':text,'start_seconds':i*13,'end_seconds':i*13+15} for i,text in enumerate(texts)])


def draft(windows,joins=None,issues=None):
    if joins is None:
        joins=[{'left':a['id'],'right':b['id'],'trim_left':0,'trim_right':0,'reason':'none'} for a,b in zip(windows,windows[1:])]
    plan=compile_joins(windows,{'joins':joins,'sentence_ends':[],'commas':[],'issues':issues or []})
    return validate_plan(windows,plan)


def test_exact_duplicates_resolved_without_manual_review_or_calls():
    w=sources(['We discuss right angles','right angles today'])
    d=draft(w,issues=[{'code':'ambiguous_repetition','window_ids':['w0','w1']}])
    r=correct(w,d,recovery=lambda _:pytest.fail('No recovery needed'))
    assert r['text']=='We discuss right angles today'
    assert r['status']=='corrected_source_backed' and r['recovery_calls']==0
    assert r['correction_events'][0]['reason']=='exact_overlap_deduplicated'
    assert d['text']=='We discuss right angles right angles today'


def test_consistent_term_selected_only_from_other_bodhan_source():
    w=source_windows([{'id':'w0','text':'Here make a leanier pair','start_seconds':0,'end_seconds':15},
                      {'id':'w1','text':'make a linear pair now','start_seconds':13,'end_seconds':28},
                      {'id':'w2','text':'A linear pair appears here','start_seconds':40,'end_seconds':55}])
    d=draft(w,joins=[{'left':'w0','right':'w1','trim_left':0,'trim_right':4,'reason':'overlap_variant'},
                     {'left':'w1','right':'w2','trim_left':0,'trim_right':0,'reason':'none'}])
    assert 'leanier' in d['text']
    r=correct(w,d)
    assert 'leanier' not in r['text'] and 'linear pair' in r['text']
    assert r['correction_events'][0]['reason']=='consistent_existing_source_term'
    assert r['validation']['all_source_words_accounted']


def test_complete_neighbor_source_is_used_without_knowledge_completion():
    w=sources(['We now say Fifth work','Fifth word adjacent angles'])
    r=correct(w,draft(w,issues=[{'code':'uncertain_boundary','window_ids':['w0','w1']}]))
    assert r['text']=='We now say Fifth word adjacent angles'
    assert r['status']=='corrected_source_backed'


def test_repeated_single_words_not_silently_deduplicated():
    w=sources(['yes yes','yes yes'])
    r=correct(w,draft(w,issues=[{'code':'ambiguous_repetition','window_ids':['w0','w1']}]))
    assert r['text']=='yes yes yes yes'
    assert r['status']=='partial_failure'
    assert all(s['status']=='skipped' for s in r['sentences'])


def test_numeric_disagreement_not_guessed():
    w=sources(['The angle is ninety degrees','The angle is eighty degrees'])
    r=correct(w,draft(w,issues=[{'code':'uncertain_boundary','window_ids':['w0','w1']}]))
    assert r['blocked_window_ids']==['w0','w1']
    assert r['status']=='partial_failure'


def test_protected_names_need_additional_evidence():
    w=sources(['We call Dr Smith','Dr Jones speaks now'])
    r=correct(w,draft(w),protected_terms=['Smith','Jones'])
    assert r['status']=='partial_failure'
    assert r['correction_events'][0]['reason']=='protected_term_conflict'


def test_bounded_recovery_corroborates_only_an_existing_source_choice():
    w=sources(['We call Dr Smith','Dr Jones speaks now'])
    calls=[]
    def recover(request):
        calls.append(request)
        return {'text':'Dr Jones speaks now'}
    r=correct(w,draft(w),recovery=recover,protected_terms=['Smith','Jones'],max_recovery_calls=1)
    assert len(calls)==1 and r['recovery_calls']==1
    assert 'Dr Jones' in r['text'] and 'Smith' not in r['text']
    assert r['correction_events'][0]['reason']=='bodhan_recovery_corroboration'
    assert calls[0]['end_seconds']-calls[0]['start_seconds']<=15


def test_recovery_budget_is_shared_across_conflicts():
    w=sources(['yes yes','yes yes','yes yes','yes yes'])
    issues=[{'code':'ambiguous_repetition','window_ids':[f'w{i}',f'w{i+1}']} for i in range(3)]
    calls=[]
    r=correct(w,draft(w,issues=issues),recovery=lambda request:(calls.append(request) or {'text':'yes yes'}),max_recovery_calls=2)
    assert len(calls)==2 and r['status']=='partial_failure'
    assert r['recovery_calls']==2


def test_failed_recovery_keeps_partial_outputs_and_reason():
    w=sources(['yes yes','yes yes'])
    def recover(_):raise MediaError('provider_failure','Temporary failure')
    r=correct(w,draft(w,issues=[{'code':'ambiguous_repetition','window_ids':['w0','w1']}]),recovery=recover)
    assert r['status']=='partial_failure' and r['recovery_calls']==1
    assert r['correction_events'][0]['reason']=='recovery_failed'
    assert 'skip_reason' in r['sentences'][0]


@pytest.mark.parametrize('budget',[True,-1,4])
def test_invalid_budget(budget):
    w=sources(['An existing statement'])
    with pytest.raises(MediaError):correct(w,draft(w),max_recovery_calls=budget)


def test_unexplained_input_modification_is_rejected():
    w=sources(['We discuss right angles','right angles today'])
    d=draft(w)
    d['sentences'][0]['source_spans'][0]['start']=1
    with pytest.raises(MediaError):correct(w,d)


def test_non_boundary_omission_flag_cannot_be_silently_cleared():
    w=sources(['An existing statement'])
    r=correct(w,draft(w,issues=[{'code':'possible_omission','window_ids':['w0']}]))
    assert r['status']=='partial_failure'
    assert r['correction_events'][0]['reason']=='non_boundary_quality_issue'


def test_normalization_uses_recorded_source_phrase_not_a_dictionary():
    w=source_windows([{'id':'w0','text':'A leanier pair Here make a leanier pair','start_seconds':0,'end_seconds':15},
                      {'id':'w1','text':'make a linear pair now','start_seconds':13,'end_seconds':28},
                      {'id':'w2','text':'A linear pair appears here','start_seconds':40,'end_seconds':55}])
    d=draft(w,joins=[{'left':'w0','right':'w1','trim_left':0,'trim_right':4,'reason':'overlap_variant'},
                    {'left':'w1','right':'w2','trim_left':0,'trim_right':0,'reason':'none'}])
    r=correct(w,d)
    assert 'leanier' not in r['text']
    assert r['term_normalizations']
    assert r['term_normalizations'][0]['source_phrase']==['linear','pair']
    assert 'leanier' in d['text']


def test_bodhan_recovery_exports_exact_short_source_and_calls_client_once(tmp_path):
    import wave
    from types import SimpleNamespace
    source=bytes(range(256))*2500
    with wave.open(str(tmp_path/'source_audio.wav'),'wb') as audio:
        audio.setparams((1,2,16000,0,'NONE','not compressed'));audio.writeframes(source)
    seen=[]
    def transcribe(path):
        with wave.open(str(path),'rb') as audio:
            assert audio.getnframes()==16000*10
            assert audio.readframes(audio.getnframes())==source[2*32000:12*32000]
        seen.append(path)
        return {'text':'An existing phrase','cache_hit':False}
    adapter=BodhanBoundaryRecovery(tmp_path,SimpleNamespace(transcribe=transcribe),tmp_path/'recovery')
    result=adapter({'start_seconds':2,'end_seconds':12,'window_ids':['a','b']})
    assert len(seen)==1 and result['text']=='An existing phrase'
    assert result['evidence']['source_start_seconds']==2


def test_unusable_recovery_does_not_become_source_evidence():
    w=sources(['yes yes','yes yes'])
    r=correct(w,draft(w,issues=[{'code':'ambiguous_repetition','window_ids':['w0','w1']}]),
              recovery=lambda _: {'text':'ୄ'*100})
    assert r['status']=='partial_failure'
    assert r['correction_events'][0]['reason']=='recovery_failed'


def test_critical_omission_not_cleared_by_an_exact_overlap():
    w=sources(['We discuss right angles','right angles today'])
    r=correct(w,draft(w,issues=[{'code':'possible_omission','window_ids':['w0','w1']}]))
    assert r['status']=='partial_failure'
    assert r['issues'][0]['reason']=='unresolved_content_coverage'


def test_no_external_term_is_invented_without_source_support():
    w=sources(['A leanier pair is mentioned'])
    r=correct(w,draft(w))
    assert r['text']=='A leanier pair is mentioned'
    assert not r['term_normalizations']
