import io
import json
import shutil
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from bodhan_dub import translation_repair as repair,meaning_checks,fit_qa
from bodhan_dub.audio_output import RATE,pcm_write
from bodhan_dub.reconciliation import LLMSettings
from bodhan_dub.synthesis import SpeechClient,inspect_wav
from bodhan_dub.translation import TranslationClient
from bodhan_dub.transcription import TranscriptionClient,Response


def test_meaning_acceptance_precedes_speech_and_new_audio_is_verified(tmp_path,monkeypatch):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_:pytest.fail('No live API'))
    source=tmp_path/'source';source.mkdir();raw=source/'old.wav'
    samples=(np.sin(np.arange(RATE)*2*np.pi*220/RATE)*1000).astype(np.int16);pcm_write(raw,samples);info=inspect_wav(raw.read_bytes())
    item={'id':'u1','language':'hi','original_english':'Open your notebook.','english':'Open your notebook.','translation':'पुराना पाठ।',
        'voice':'Kavya','status':'fitted','audio_path':str(raw),'audio_sha256':info['sha256'],'normalized_audio_sha256':info['sha256'],
        'source_window_envelope':{'start_seconds':0,'end_seconds':5},'observed_seconds':1,
        'fit':{'speed':1,'path':str(raw),'original_audio':info},'timing':{'speech_slots':[{'start_seconds':0,'end_seconds':5}]},
        'placements':[{'start_sample':0,'end_sample':RATE,'audio_start_sample':0,'audio_end_sample':RATE}],
        'qa':{'cer':0,'status':'pass','audio_sha256':info['sha256']}}
    manifest={'source_run':str(source/'run'),'all_units_fitted':True,'timeline':{'duration_seconds':5,'units':[{'id':'u1'}],'protected_gaps':[]},
        'languages':{'hi':{'units':[item]}},'combined_estimated_cost_inr':'0'}
    path=source/'fit_manifest.json';path.write_text(json.dumps(manifest));calls=[];target='अपनी नोटबुक खोलें।'
    monkeypatch.setattr(repair,'load_key',lambda *_:'fake');monkeypatch.setattr(repair,'speech_key',lambda *_:'fake')
    monkeypatch.setattr(repair,'load_settings',lambda *_:LLMSettings('https://example.test/v1','test','fake'))
    monkeypatch.setattr(meaning_checks,'judge',lambda rows,*a,**kw:{r['id']:{'id':r['id'],'ok':True,'issues':[]} for r in rows})
    def mt(request):
        body=json.loads(request.body);is_back='into English:' in body['messages'][0]['content'];calls.append('back' if is_back else 'forward')
        text='<bodhan_segments><segment id="u1">'+('Open your notebook.' if is_back else target)+'</segment></bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':5}}).encode())
    pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None,events=[])
    monkeypatch.setattr(repair,'TranslationClient',lambda key,cache,settings,**kw:TranslationClient(key,cache,settings,transport=mt,pacer=pacer,**kw))
    def speak(request):calls.append('speech');return Response(200,raw.read_bytes())
    monkeypatch.setattr(repair,'SpeechClient',lambda key,cache,ffmpeg,settings:SpeechClient(key,cache,ffmpeg,settings,transport=speak,pacer=pacer))
    monkeypatch.setattr(fit_qa,'load_key',lambda *_:'fake')
    def asr(request):calls.append('asr');return Response(200,json.dumps({'text':target}).encode())
    monkeypatch.setattr(fit_qa,'TranscriptionClient',lambda key,cache,settings,**kw:TranscriptionClient(key,cache,settings,transport=asr,allowed_languages={'hi'}))
    result=repair.repair_meanings(path,tmp_path/'quality',None,initial_checks={'hi/u1':{'ok':False,'issues':['Wrong meaning.']}})
    assert calls==['forward','back','speech','asr']
    assert not result['quality_repair']['unresolved']
    final=result['languages']['hi']['units'][0]
    assert final['translation']==target and final['qa']['cer']==0
    assert final['translation_meaning_check']['ok']
    assert float(result['quality_repair']['new_estimated_cost_inr'])<3


def test_english_repair_guard_protects_explicit_numbers_ordinals_and_labels():
    from bodhan_dub.media import MediaError
    source='Second term: angle AOC is one hundred and twenty degrees.'
    assert repair.english_guard(source,source)==source
    with pytest.raises(MediaError):repair.english_guard(source,'Angle AOC is twenty degrees.')
    spaced='Lines A B and C D cross at O; ray O E lies on side B. Angle AOC is one pair.'
    assert repair.english_guard(spaced,'Lines AB and CD cross at O; ray OE lies on side B. Angle AOC is one pair.')
    assert 'O E' in repair.english_guard(spaced,'Lines A B and C D cross at O; ray O\u202fE lies on side B. Angle AOC is one pair.')
    with pytest.raises(MediaError):repair.english_guard(spaced,'Angle AOC is one pair.')


def test_english_guard_retry_keeps_protected_number_before_native_calls(tmp_path,monkeypatch):
    attempts=[]
    def propose(_settings,messages,*_):
        attempts.append(messages)
        return {'english':'Seventh heading: form a pair.' if len(attempts)==1 else 'Seventh heading: form one pair.'}
    monkeypatch.setattr(repair,'llm_json',propose)
    monkeypatch.setattr(repair,'judge',lambda *_:{'bn/u1':{'ok':True}})
    source='Seventh heading: form one pair.'
    settings=LLMSettings('https://example.test/v1','test','fake')
    text=repair.propose_english({'id':'u1','language':'bn','original_english':source},[],settings,tmp_path/'proposal.json')
    assert text==source and len(attempts)==2
    assert 'one' in attempts[-1][-1]['content']


def test_partial_translation_retries_only_missing_units_and_reuses_successes(tmp_path):
    from bodhan_dub.translation import TranslationSettings,translate_run
    from bodhan_dub.fit_recovery import Budget
    requests=[]
    def translate(request):
        body=json.loads(request.body);requests.append(body['messages'][0]['content'])
        ident='u1' if len(requests)==1 else 'u2'
        content='<bodhan_segments><segment id="'+ident+'">अपनी नोटबुक खोलें।</segment></bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'completion_tokens':5}}).encode())
    pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None)
    client=TranslationClient('fake',tmp_path/'cache',TranslationSettings(attempts=1,max_output_tokens=256),transport=translate,pacer=pacer)
    units=[{'id':ident,'text':'Open your notebook.','status':'ready_for_translation','source_window_envelope':{'start_seconds':0,'end_seconds':2}} for ident in ('u1','u2')]
    report=translate_run(units,['hi'],tmp_path/'out',client)
    assert report['status']=='partial_failure'
    result=repair.recover_translation_units(units,report,client,Budget(tmp_path/'budget.json','1'),tmp_path/'out')
    assert result['status']=='translated' and len(requests)==2
    assert '<segment id="u1">' not in requests[1] and '<segment id="u2">' in requests[1]


@pytest.mark.parametrize('additional_failure',[0,1])
def test_clear_unchanged_english_can_receive_contextual_bodhan_repair(tmp_path,monkeypatch,additional_failure):
    source='Open your notebook.';calls=[];rounds=[]
    monkeypatch.setattr(repair,'load_settings',lambda *_:LLMSettings('https://example.test/v1','model','fake'))
    monkeypatch.setattr(repair,'load_key',lambda *_:'fake')
    monkeypatch.setattr(repair,'TranslationClient',lambda *args,**kwargs:SimpleNamespace(context=kwargs.get('english_context')))
    def assess(*args,**kwargs):
        rounds.append(True);ok=len(rounds)>1+additional_failure;return {'hi/u1':{'ok':ok,'issues':[] if ok else ['Missing instruction.']}}
    monkeypatch.setattr(repair,'assess_pairs',assess)
    monkeypatch.setattr(repair,'propose_english',lambda *_:source if not calls else source+' Please.')
    def translate(client,units,*_):
        calls.append(client.context);return {'units':{'u1':{'translation':'अपनी नोटबुक खोलें।'}}}
    monkeypatch.setattr(repair,'charged_translate',translate)
    translated={'languages':{'hi':{'status':'translated','units':[{'id':'u1','source_text':source,'translation':'गलत पाठ।','status':'translated'}]}}}
    result=repair.before_speech([{'id':'u1','text':source}],translated,None,None,tmp_path)
    assert calls==[source]*(1+additional_failure) and result['status']=='translated'


@pytest.mark.parametrize('prior_state',['complete','in_flight'])
def test_invalid_completed_context_can_change_source_but_unknown_request_stops(tmp_path,monkeypatch,prior_state):
    from bodhan_dub.media import MediaError
    source='Open your notebook.';calls=[];proposals=[];checks=[]
    monkeypatch.setattr(repair,'load_settings',lambda *_:LLMSettings('https://example.test/v1','model','fake'))
    monkeypatch.setattr(repair,'load_key',lambda *_:'fake')
    monkeypatch.setattr(repair,'TranslationClient',lambda *a,**kw:None)
    def assess(*a,**kw):
        checks.append(True);return {'hi/u1':{'ok':len(checks)>1,'issues':['Missing instruction.']}}
    monkeypatch.setattr(repair,'assess_pairs',assess)
    def propose(item,issues,*_):
        proposals.append(issues);return source if len(proposals)==1 else 'Please open your notebook.'
    monkeypatch.setattr(repair,'propose_english',propose)
    def translate(_client,units,*_):
        calls.append(units[0]['text'])
        if len(calls)==1:
            exc=MediaError('previous_translation_attempt','Do not replay.');exc.previous_state=prior_state;raise exc
        return {'units':{'u1':{'translation':'अपनी नोटबुक खोलें।'}}}
    monkeypatch.setattr(repair,'charged_translate',translate)
    report={'languages':{'hi':{'units':[{'id':'u1','source_text':source,'translation':'गलत।','status':'translated'}]}}}
    result=repair.before_speech([{'id':'u1','text':source}],report,None,None,tmp_path)
    if prior_state=='complete':
        assert result['status']=='translated' and calls==[source,'Please open your notebook.']
        assert 'complete, explicit' in proposals[-1][-1]
    else:assert result['status']=='partial_failure' and calls==[source]


@pytest.mark.parametrize('request_state',['invalid','unknown'])
def test_completed_invalid_whole_document_uses_two_smaller_batches_only(tmp_path,monkeypatch,request_state):
    units=[{'id':f'u{i}','text':'Open your notebook.','status':'ready_for_translation','source_window_envelope':{'start_seconds':i,'end_seconds':i+1}} for i in range(4)]
    calls=[]
    def translate(_client,batch,*_):
        calls.append([u['id'] for u in batch]);return {'units':{u['id']:{'translation':'अपनी नोटबुक खोलें।'} for u in batch}}
    monkeypatch.setattr(repair,'charged_translate',translate)
    data={'languages':{'hi':{'status':'failed','units':[],'error':{'code':'previous_translation_attempt'},'previous_request_state':request_state}}}
    result=repair.recover_translation_units(units,data,None,None,tmp_path)
    if request_state=='invalid':
        assert calls==[['u0','u1'],['u2','u3']] and result['status']=='translated'
        assert all(u['recovered_after_document_split'] for u in result['languages']['hi']['units'])
    else:assert calls==[] and result['status']=='partial_failure'



@pytest.mark.parametrize('omit_last',[False,True])
def test_real_translation_client_recovers_invalid_document_and_reuses_split_cache(tmp_path,omit_last):
    import re
    from bodhan_dub.translation import translate_run,TranslationSettings
    from bodhan_dub.fit_recovery import Budget
    calls=[]
    def transport(request):
        content=json.loads(request.body)['messages'][0]['content'];ids=re.findall(r'<segment id="([^"]+)">',content);calls.append(ids)
        kept=ids[:1] if omit_last and ids==['u2','u3'] else ids
        text='not XML' if len(ids)==4 else '<bodhan_segments>'+''.join('<segment id="'+ident+'">अपनी नोटबुक खोलें।</segment>' for ident in kept)+'</bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':5}}).encode())
    client=TranslationClient('fake',tmp_path/'cache',TranslationSettings(attempts=1,max_output_tokens=256),transport=transport,pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None))
    units=[{'id':f'u{i}','text':'Open your notebook.','status':'ready_for_translation','source_window_envelope':{'start_seconds':i,'end_seconds':i+1}} for i in range(4)]
    budget=Budget(tmp_path/'budget.json','1')
    for name in ['first','continued']:
        out=tmp_path/name;initial=translate_run(units,['hi'],out,client)
        assert initial['status']=='partial_failure'
        recovered=repair.recover_translation_units(units,initial,client,budget,out)
        assert recovered['status']=='translated' and len(recovered['languages']['hi']['units'])==4
    assert calls==[['u0','u1','u2','u3'],['u0','u1'],['u2','u3']]+([['u3']] if omit_last else [])



def test_unknown_smaller_document_is_not_retried_as_individual_units(tmp_path,monkeypatch):
    from bodhan_dub.media import MediaError
    units=[{'id':f'u{i}','text':'Open your notebook.','status':'ready_for_translation','source_window_envelope':{'start_seconds':i,'end_seconds':i+1}} for i in range(4)]
    calls=[]
    def translate(_client,batch,*_):
        ids=[u['id'] for u in batch];calls.append(ids)
        if ids==['u0','u1']:raise MediaError('ambiguous_translation_call','Unknown provider outcome.')
        return {'units':{u['id']:{'translation':'अपनी नोटबुक खोलें।'} for u in batch}}
    monkeypatch.setattr(repair,'charged_translate',translate)
    data={'languages':{'hi':{'units':[],'error':{'code':'invalid_translation_structure'}}}}
    result=repair.recover_translation_units(units,data,None,None,tmp_path)
    assert calls==[['u0','u1'],['u2','u3']]
    assert result['status']=='partial_failure'
    assert all(u.get('uncertain_request') for u in result['languages']['hi']['units'][:2])



def test_missing_multisentence_unit_can_use_complete_source_pieces(tmp_path):
    import re
    from bodhan_dub.translation import TranslationSettings
    from bodhan_dub.fit_recovery import Budget
    source='Open your notebook. Read the next definition.';calls=[]
    def transport(request):
        ids=re.findall(r'<segment id="([^"]+)">',json.loads(request.body)['messages'][0]['content']);calls.append(ids)
        text='<bodhan_segments>'+''.join('<segment id="'+ident+'">अपनी नोटबुक खोलें।</segment>' for ident in ids if '-s' in ident)+'</bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':5}}).encode())
    client=TranslationClient('fake',tmp_path/'cache',TranslationSettings(attempts=1,max_output_tokens=256),transport=transport,pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None))
    unit={'id':'u1','text':source,'status':'ready_for_translation','source_window_envelope':{'start_seconds':0,'end_seconds':8}}
    report={'languages':{'hi':{'units':[{'id':'u1','source_text':source,'status':'failed','reason':'missing_translation'}]}}}
    result=repair.recover_translation_units([unit],report,client,Budget(tmp_path/'budget.json','1'),tmp_path)
    row=result['languages']['hi']['units'][0]
    assert result['status']=='translated' and row['recovered_from_sentence_parts']
    assert ''.join(c['text'] for c in row['translation_source_parts'])==source
    assert calls==[['u1'],['u1-s01','u1-s02']]
    combined='An explanation, right? both. FIFTH WORD: ADJACENT ANGLES.'
    assert repair.english_translation_pieces(combined)==['An explanation, right? both. ','FIFTH WORD: ADJACENT ANGLES.']


def test_uncertain_missing_unit_is_not_retried_by_sentence(tmp_path,monkeypatch):
    from bodhan_dub.media import MediaError
    calls=[]
    def translate(*args):calls.append(True);raise MediaError('ambiguous_translation_call','Unknown outcome.')
    monkeypatch.setattr(repair,'charged_translate',translate)
    source='Open your notebook. Read the next definition.'
    units=[{'id':'u1','text':source,'status':'ready_for_translation','source_window_envelope':{'start_seconds':0,'end_seconds':8}}]
    report={'languages':{'hi':{'units':[{'id':'u1','source_text':source,'status':'failed'}]}}}
    result=repair.recover_translation_units(units,report,None,None,tmp_path)
    assert calls==[True] and result['status']=='partial_failure'
    assert result['languages']['hi']['units'][0]['uncertain_request']



def test_contextual_repair_allows_written_native_numbers_before_semantic_check(tmp_path,monkeypatch):
    source='Angle AOC is one hundred and ten degrees.';reviews=[]
    monkeypatch.setattr(repair,'load_settings',lambda *_:LLMSettings('https://example.test/v1','model','fake'))
    monkeypatch.setattr(repair,'load_key',lambda *_:'fake')
    monkeypatch.setattr(repair,'propose_english',lambda *_:'Angle AOC measures 110 (one hundred and ten) degrees.')
    def assess(*a,**kw):
        reviews.append(True);return {'hi/u1':{'ok':len(reviews)>1,'issues':['Wrong numerical value.']}}
    monkeypatch.setattr(repair,'assess_pairs',assess)
    native='कोण AOC एक सौ दस डिग्री है।'
    def transport(request):
        text='<bodhan_segments><segment id="__source_context">'+native+'</segment><segment id="u1">'+native+'</segment></bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':5}}).encode())
    monkeypatch.setattr(repair,'TranslationClient',lambda key,cache,settings,**kw:TranslationClient(key,cache,settings,transport=transport,pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None),**kw))
    report={'languages':{'hi':{'units':[{'id':'u1','status':'translated','source_text':source,'translation':'गलत माप।'}]}}}
    result=repair.before_speech([{'id':'u1','text':source}],report,None,__import__('bodhan_dub.fit_recovery',fromlist=['Budget']).Budget(tmp_path/'budget.json','1'),tmp_path/'out')
    assert result['status']=='translated' and len(reviews)==2
    assert result['languages']['hi']['units'][0]['translation']==native


def test_independent_repair_preserves_source_order_and_scopes_glossary(tmp_path, monkeypatch):
    from bodhan_dub.translation import TranslationSettings

    source = 'Draw the linear pair. Name the vertex of the linear pair.'
    client = SimpleNamespace(
        _key='fake', cache=tmp_path, settings=TranslationSettings(),
        glossary={'linear pair': {'hi': 'रैखिक युग्म'}, 'reflex': {'hi': 'प्रतिवर्ती'}},
        code_mixed=False, pacer=object(),
    )
    requests = []
    monkeypatch.setattr(repair, 'TranslationClient', lambda *args, **kwargs: SimpleNamespace(**kwargs))

    def translate(statement_client, units, language, *_):
        requests.append((units[0]['text'], statement_client.glossary, language))
        text = 'रैखिक युग्म बनाइए।' if len(requests) == 1 else 'युग्म के शीर्ष का नाम बताइए।'
        return {'units': {units[0]['id']: {'translation': text}}}

    monkeypatch.setattr(repair, 'charged_translate', translate)
    assert repair.translate_independent_parts(source, 'u1', 'hi', client, None) == 'रैखिक युग्म बनाइए। युग्म के शीर्ष का नाम बताइए।'
    assert requests == [
        ('Draw the linear pair. ', {'linear pair': {'hi': 'रैखिक युग्म'}}, 'hi'),
        ('Name the vertex of the linear pair.', {}, 'hi'),
    ]


def test_disputed_glossary_check_uses_intact_translation(tmp_path, monkeypatch):
    source = 'Name one linear pair.'
    target = 'एक रैखिक युग्म का नाम बताओ।'
    glossary = {'linear pair': {'hi': 'रैखिक युग्म'}}
    monkeypatch.setattr(repair, 'load_settings', lambda *_: LLMSettings('https://example.test/v1', 'model', 'fake'))
    monkeypatch.setattr(repair, 'load_key', lambda *_: 'fake')
    monkeypatch.setattr(repair, 'TranslationClient', lambda *args, **kwargs: None)
    reviews = []

    def assess(items, *args, reference_glossary, **kwargs):
        assert items[0]['translation'] == target
        reviews.append(reference_glossary)
        return {'hi/u1': {'ok': reference_glossary is None, 'issues': [], 'verification_term_ids': {'CONCEPT_AA': 'linear pair'}}}

    monkeypatch.setattr(repair, 'assess_pairs', assess)
    translated = {'languages': {'hi': {'units': [{'id': 'u1', 'source_text': source, 'translation': target, 'status': 'translated'}]}}}
    result = repair.before_speech([{'id': 'u1', 'text': source}], translated, None, None, tmp_path, reference_glossary=glossary)
    assert reviews == [glossary, None]
    assert result['status'] == 'translated'
    assert result['languages']['hi']['units'][0]['meaning_check']['verification_route'] == 'unmasked_back_translation'
