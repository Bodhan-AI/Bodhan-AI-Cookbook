import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.transcription import Response
from bodhan_dub.translation import (TranslationClient,TranslationSettings,document,digit_groups,
    inspect_units,languages,load_key,main,parse_document,translate_run,translation_quality)


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No hosted calls in tests'))


def units():
    return [{'id':'unit-0001','text':'The class starts at nine.','status':'ready_for_translation',
             'source_window_envelope':{'start_seconds':0,'end_seconds':15}},
            {'id':'unit-0002','text':'Open your notebook.','status':'ready_for_translation',
             'source_window_envelope':{'start_seconds':13,'end_seconds':28}}]


def response(text=None,finish='stop',usage=None):
    text=text or '<bodhan_segments><segment id="unit-0001">कक्षा नौ बजे शुरू होती है।</segment><segment id="unit-0002">अपनी नोटबुक खोलें।</segment></bodhan_segments>'
    return Response(200,json.dumps({'choices':[{'finish_reason':finish,'message':{'content':text}}],
                                    'usage':usage or {'completion_tokens':20,'prompt_tokens':40,'total_tokens':60}}).encode())


def client(tmp_path,responses,attempts=1):
    calls=[];waits=[]
    def transport(request):
        calls.append(request)
        if not responses:pytest.fail('Unexpected extra provider call')
        value=responses.pop(0)
        if isinstance(value,BaseException):raise value
        return value
    c=TranslationClient('fake-key',tmp_path,TranslationSettings(attempts=attempts),transport=transport,
                        sleep=waits.append,pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None))
    return c,calls,waits


def test_single_official_user_prompt_and_all_units_one_document(tmp_path):
    c,calls,_=client(tmp_path,[response()])
    result=c.translate(units(),'hi')
    assert len(result['units'])==2 and not result['errors'] and len(calls)==1
    body=json.loads(calls[0].body)
    assert set(body)=={'model','messages','max_output_tokens'}
    assert len(body['messages'])==1 and body['messages'][0]['role']=='user'
    assert body['messages'][0]['content'].startswith('Translate the following text into Hindi:\n\n')
    assert 'unit-0001' in body['messages'][0]['content'] and 'unit-0002' in body['messages'][0]['content']
    assert 'fake-key' not in repr(calls[0])
    assert result['known_new_cost_inr']=='0.00040'


def test_success_cache_reused_without_new_paid_calls(tmp_path):
    c,calls,_=client(tmp_path,[response()])
    c.translate(units(),'hi');r=c.translate(units(),'hi')
    assert len(calls)==1 and r['cache_hits']==2 and r['attempts']==[]
    assert r['known_new_cost_inr']=='0'
    assert 'fake-key' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))


def test_partial_success_reuses_translated_unit_and_requests_only_missing(tmp_path):
    partial='<bodhan_segments><segment id="unit-0001">कक्षा नौ बजे शुरू होती है।</segment></bodhan_segments>'
    remaining='<bodhan_segments><segment id="unit-0002">अपनी नोटबुक खोलें।</segment></bodhan_segments>'
    c,calls,_=client(tmp_path,[response(partial),response(remaining)])
    first=c.translate(units(),'hi')
    assert 'unit-0002' in first['errors']
    second=c.translate(units(),'hi')
    assert second['cache_hits']==1 and len(second['units'])==2
    body=json.loads(calls[1].body)['messages'][0]['content']
    assert 'unit-0001' not in body and 'unit-0002' in body


@pytest.mark.parametrize('text',[
    'Unlabelled text',
    '<wrong></wrong>',
    '<bodhan_segments>invented introduction<segment id="unit-0001">नमस्ते</segment></bodhan_segments>',
    '<bodhan_segments><segment id="unknown">नमस्ते</segment></bodhan_segments>',
    '<!DOCTYPE x><bodhan_segments/>',
    '<bodhan_segments><segment id="unit-0001"><b>नमस्ते</b></segment></bodhan_segments>',
])
def test_unknown_or_unlabelled_translations_rejected(text):
    with pytest.raises(MediaError):parse_document(text,units())


def test_duplicate_unit_never_silently_accepted():
    text='<bodhan_segments><segment id="unit-0001">एक</segment><segment id="unit-0001">दो</segment><segment id="unit-0002">तीन</segment></bodhan_segments>'
    translated,errors=parse_document(text,units())
    assert 'unit-0001' not in translated and 'unit-0001' in errors
    assert translated['unit-0002']=='तीन'


def test_xml_escaping_round_trip_does_not_change_source():
    rows=[{'id':'unit-0001','text':'A < B & C > D'}]
    assert parse_document(document(rows),rows)[0]['unit-0001']=='A < B & C > D'


def test_truncated_success_not_cached_or_automatically_repeated(tmp_path):
    c,calls,_=client(tmp_path,[response(finish='length')],attempts=3)
    with pytest.raises(MediaError) as error:c.translate(units(),'hi')
    assert error.value.code=='truncated_translation'
    with pytest.raises(MediaError):c.translate(units(),'hi')
    assert len(calls)==1


def test_unknown_transport_not_silently_retried(tmp_path):
    c,calls,_=client(tmp_path,[TimeoutError('Never print key')],attempts=3)
    with pytest.raises(MediaError) as error:c.translate(units(),'hi')
    assert error.value.code=='ambiguous_translation_call'
    with pytest.raises(MediaError):c.translate(units(),'hi')
    assert len(calls)==1


def test_interruption_preserves_pre_send_journal(tmp_path):
    c,calls,_=client(tmp_path,[KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):c.translate(units(),'hi')
    with pytest.raises(MediaError):c.translate(units(),'hi')
    assert len(calls)==1


def test_transient_errors_have_bounded_backoff(tmp_path):
    c,calls,waits=client(tmp_path,[Response(429,b''),Response(503,b''),response()],attempts=3)
    assert len(c.translate(units(),'hi')['units'])==2
    assert len(calls)==3 and waits==[1,2]


@pytest.mark.parametrize('status',[400,401,403,404,413])
def test_permanent_error_no_retry_or_provider_body_echo(tmp_path,status):
    c,calls,_=client(tmp_path,[Response(status,b'fake-key')],attempts=3)
    with pytest.raises(MediaError) as error:c.translate(units(),'hi')
    assert len(calls)==1 and 'fake-key' not in str(error.value)


@pytest.mark.parametrize('target,reason',[
    ('The class starts at nine.','translation_echoed_english'),
    ('Wrong language response','missing_target_script'),
    ('क'*100,'degenerate_translation'),
    ('','unusable_translation'),
])
def test_unusable_translation_quality(target,reason):
    assert translation_quality('The class starts at nine.',target,'hi')==reason


def test_native_digits_normalized_without_target_rewriting():
    assert digit_groups('30 and 60')==digit_groups('३० और ६०')
    assert translation_quality('Angle is 90 degrees','कोण ८० डिग्री है','hi')=='literal_number_mismatch'


def test_language_failure_does_not_stop_other_languages(tmp_path):
    class Fake:
        def translate(self,rows,lang):
            if lang=='ta':raise MediaError('bad_translation','Tamil failed')
            return {'units':{u['id']:{'translation':'generated by provider','cache_hit':True} for u in rows},'errors':{},'attempts':[],
                    'known_new_cost_inr':'0','conservative_new_cost_upper_bound_inr':'0'}
    r=translate_run(units(),['hi','ta','bn'],tmp_path,Fake())
    assert r['status']=='partial_failure'
    assert r['languages']['hi']['status']=='translated'
    assert r['languages']['bn']['status']=='translated'
    assert (tmp_path/'translation_report.json').exists()


def test_shared_auth_error_stops_unnecessary_calls(tmp_path):
    calls=[]
    class Fake:
        def translate(self,rows,lang):calls.append(lang);raise MediaError('invalid_translation_key','Invalid credential')
    r=translate_run(units(),['hi','ta','bn'],tmp_path,Fake())
    assert calls==['hi'] and r['status']=='partial_failure'


def test_unsupported_and_duplicate_languages_rejected():
    for values in [['hi','hi'],['xx'],[]]:
        with pytest.raises(MediaError):languages(values)
    assert languages(['gu','kn','ml','mr'])==['gu','kn','ml','mr']


def test_explicit_environment_file_and_environment_precedence(tmp_path,monkeypatch):
    monkeypatch.delenv('BODHAN_TRANSLATE_KEY',raising=False)
    p=tmp_path/'.env';p.write_text('BODHAN_TRANSLATE_KEY="fake-file-key"\n')
    assert load_key(p)=='fake-file-key'
    monkeypatch.setenv('BODHAN_TRANSLATE_KEY','fake-env-key')
    assert load_key(p)=='fake-env-key'


@pytest.mark.parametrize('settings',[{'attempts':0},{'attempts':True},{'max_output_tokens':1},{'timeout_seconds':float('inf')},{'requests_per_minute':0}])
def test_invalid_settings(settings):
    with pytest.raises(MediaError):TranslationSettings(**settings)


def test_dry_run_reads_no_keys_and_creates_no_outputs(tmp_path,monkeypatch,capsys):
    path=tmp_path/'units.json'
    path.write_text(json.dumps({'status':'grouped_for_translation','validation':{'all_corrected_words_preserved_in_order':True},'units':units()}))
    monkeypatch.setattr('bodhan_dub.translation.load_key',lambda *_:pytest.fail('No keys in dry run'))
    assert main(['--units',str(path)])==0
    r=json.loads(capsys.readouterr().out)
    assert r['hosted_calls']==0 and r['estimate']['main_requests']==3
    assert r['estimate']['first_attempt_token_cap_cost_inr']=='0.24576'


def test_invalid_unit_preflight_before_key_access(tmp_path,monkeypatch):
    p=tmp_path/'units.json';rows=units();rows[-1]['id']=rows[0]['id']
    p.write_text(json.dumps({'status':'grouped_for_translation','validation':{'all_corrected_words_preserved_in_order':True},'units':rows}))
    monkeypatch.setattr('bodhan_dub.translation.load_key',lambda *_:pytest.fail('Preflight first'))
    assert main(['--units',str(p),'--execute','--out',str(tmp_path/'out')])==1


def test_top_level_truncation_flag_rejected_even_with_stop_reason(tmp_path):
    r=response();data=json.loads(r.body);data['truncated']=True
    c,calls,_=client(tmp_path,[Response(200,json.dumps(data).encode())])
    with pytest.raises(MediaError) as error:c.translate(units(),'hi')
    assert error.value.code=='truncated_translation'
    assert error.value.known_new_cost_inr=='0.00040'


def test_changed_source_context_invalidates_cached_unit_translations(tmp_path):
    c,calls,_=client(tmp_path,[response(),response()])
    c.translate(units(),'hi')
    rows=units();rows[0]['text']='The school starts at nine.'
    assert c.translate(rows,'hi')['cache_hits']==0
    assert len(calls)==2


def test_corrupt_unit_cache_never_causes_silent_paid_repeat(tmp_path):
    c,calls,_=client(tmp_path,[response()])
    c.translate(units(),'hi')
    next(tmp_path.rglob('unit-0001.json')).write_text('broken')
    with pytest.raises(MediaError):c.translate(units(),'hi')
    assert len(calls)==1


def test_same_document_concurrent_calls_do_not_duplicate_provider_work(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,calls,_=client(tmp_path,[response()])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.translate(units(),'hi'),range(2)))
    assert len(calls)==1
    assert sorted(r['cache_hits'] for r in results)==[0,2]
