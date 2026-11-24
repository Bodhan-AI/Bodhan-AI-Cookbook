from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import time
import urllib.error
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.transcription import (AmbiguousCall, ENDPOINT, MODEL, Request, Response, RetrySettings,
    TranscriptionClient, audio_input, http_transport, inspect_windows, load_key, main, transcribe_run)

KEY='test-placeholder-key'


@pytest.fixture(autouse=True)
def block_live_provider_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Live provider networking is forbidden in transcription tests.')
    monkeypatch.setattr('urllib.request.OpenerDirector.open', forbidden)



def make_wav(path,seconds=1,rate=16000):
    with wave.open(str(path),'wb') as audio:
        audio.setparams((1,2,rate,0,'NONE','not compressed'))
        audio.writeframes(b'\x00\x00'*int(seconds*rate))
    return path


def response(text='A sample sentence.',usage=None):
    return Response(200,json.dumps({'text':text,'usage':usage}).encode())


class ScriptedTransport:
    def __init__(self,*responses):
        self.responses=list(responses);self.calls=[]
    def __call__(self,request):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError('Unexpected extra provider call')
        result=self.responses.pop(0)
        if isinstance(result,BaseException):
            raise result
        return result


def cache_state(path):
    return json.loads(next(path.glob('*.json')).read_text())


def make_run(folder,count=2):
    (folder/'asr_windows').mkdir()
    windows=[]
    for i in range(count):
        make_wav(folder/'asr_windows'/f'window-{i+1:04d}.wav')
        windows.append({'start_sample':i*16000,'end_sample':(i+1)*16000,
                        'core_start_sample':i*16000,'core_end_sample':(i+1)*16000})
    (folder/'protected_regions.json').write_text(json.dumps({'sample_rate':16000,'total_samples':count*16000,'asr_windows':windows}))
    return folder


def test_success_schema_multipart_and_reuse(tmp_path):
    audio=make_wav(tmp_path/'input.wav')
    transport=ScriptedTransport(response(usage={'total_tokens':3,'unsafe':KEY}))
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    first=client.transcribe(audio);second=client.transcribe(audio)
    assert first['text']=='A sample sentence.' and not first['cache_hit']
    assert second['cache_hit'] and second['new_estimated_cost_inr']=='0' and second['attempts']==[]
    assert len(transport.calls)==1
    request=transport.calls[0]
    assert request.url==ENDPOINT and request.timeout==60
    assert b'window.wav' in request.body and MODEL.encode() in request.body and b'RIFF' in request.body
    assert str(audio).encode() not in request.body
    assert KEY not in repr(request)
    assert KEY not in next((tmp_path/'cache').glob('*.json')).read_text()
    assert first['provider_usage']=={'total_tokens':3}


def test_changed_content_is_different_request(tmp_path):
    one=make_wav(tmp_path/'one.wav');two=make_wav(tmp_path/'two.wav',2)
    transport=ScriptedTransport(response(),response('Different input.'))
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    client.transcribe(one);client.transcribe(two)
    assert len(transport.calls)==2


def test_modified_since_preflight_never_sent(tmp_path):
    audio=make_wav(tmp_path/'one.wav')
    transport=ScriptedTransport()
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    with pytest.raises(MediaError) as error:
        client.transcribe(audio,expected_sha256='old-placeholder-hash')
    assert error.value.code=='asr_input_changed' and not transport.calls


def test_retry_transient_then_cache(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    transport=ScriptedTransport(Response(429,b'',{'Retry-After':'2'}),Response(503,b''),response())
    sleeps=[]
    result=TranscriptionClient(KEY,tmp_path/'cache',transport=transport,sleep=sleeps.append).transcribe(audio)
    assert len(result['attempts'])==3 and sleeps==[2,2]
    assert cache_state(tmp_path/'cache')['state']=='complete'


def test_retry_limit(tmp_path):
    transport=ScriptedTransport(*[Response(503,b'')]*3)
    sleeps=[]
    with pytest.raises(MediaError) as error:
        TranscriptionClient(KEY,tmp_path/'cache',transport=transport,sleep=sleeps.append).transcribe(make_wav(tmp_path/'audio.wav'))
    assert len(transport.calls)==3 and sleeps==[1,2]
    assert len(error.value.attempts)==3
    assert cache_state(tmp_path/'cache')['state']=='failed'


@pytest.mark.parametrize('status',[400,401,403,404,413,422,302])
def test_permanent_errors_not_retried_or_echoed(tmp_path,status):
    transport=ScriptedTransport(Response(status,KEY.encode()))
    with pytest.raises(MediaError) as error:
        TranscriptionClient(KEY,tmp_path/'cache',transport=transport).transcribe(make_wav(tmp_path/'audio.wav'))
    assert len(transport.calls)==1
    assert KEY not in str(error.value)
    assert KEY not in next((tmp_path/'cache').glob('*.json')).read_text()


def test_timeout_not_retried_in_same_or_next_run(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    transport=ScriptedTransport(AmbiguousCall(KEY))
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    for expected in ['ambiguous_asr_call','ambiguous_previous_call']:
        with pytest.raises(MediaError) as error:
            client.transcribe(audio)
        assert error.value.code==expected
    assert len(transport.calls)==1 and cache_state(tmp_path/'cache')['state']=='unknown'


def test_interruption_leaves_inflight_marker(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    transport=ScriptedTransport(KeyboardInterrupt())
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    with pytest.raises(KeyboardInterrupt):
        client.transcribe(audio)
    with pytest.raises(MediaError) as error:
        client.transcribe(audio)
    assert error.value.code=='ambiguous_previous_call' and len(transport.calls)==1


@pytest.mark.parametrize('body',[b'not-json',b'[]',b'{}',b'{"text": 3}',b'{"text":"hello","error":{}}',b'\xff'])
def test_unusable_success_not_paid_twice(tmp_path,body):
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=ScriptedTransport(Response(200,body)))
    with pytest.raises(MediaError) as error:
        client.transcribe(make_wav(tmp_path/'audio.wav'))
    assert error.value.code=='invalid_asr_response'
    assert cache_state(tmp_path/'cache')['state']=='unknown'


def test_empty_transcript_is_cached_not_invented(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    transport=ScriptedTransport(response(''))
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    assert client.transcribe(audio)['status']=='empty_transcript'
    assert client.transcribe(audio)['cache_hit'] and len(transport.calls)==1


def test_provider_echo_of_secret_redacted(tmp_path):
    result=TranscriptionClient(KEY,tmp_path/'cache',transport=ScriptedTransport(response(KEY))).transcribe(make_wav(tmp_path/'audio.wav'))
    assert result['text']=='[REDACTED]'
    assert KEY not in next((tmp_path/'cache').glob('*.json')).read_text()


def test_corrupt_cache_does_not_silently_resubmit(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    transport=ScriptedTransport(response())
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    client.transcribe(audio)
    next((tmp_path/'cache').glob('*.json')).write_text('broken')
    with pytest.raises(MediaError) as error:
        client.transcribe(audio)
    assert error.value.code=='invalid_asr_cache' and len(transport.calls)==1


def test_concurrent_identical_input_has_one_call(tmp_path):
    audio=make_wav(tmp_path/'audio.wav')
    calls=[]
    def transport(request):
        calls.append(request);time.sleep(0.03);return response()
    client=TranscriptionClient(KEY,tmp_path/'cache',RetrySettings(concurrency=2),transport=transport)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:client.transcribe(audio),range(2)))
    assert len(calls)==1 and sum(r['cache_hit'] for r in results)==1


@pytest.mark.parametrize('seconds,rate',[(0,16000),(31,16000),(1,24000)])
def test_invalid_audio_never_sent(tmp_path,seconds,rate):
    transport=ScriptedTransport()
    with pytest.raises(MediaError):
        TranscriptionClient(KEY,tmp_path/'cache',transport=transport).transcribe(make_wav(tmp_path/'audio.wav',seconds,rate))
    assert transport.calls==[]


@pytest.mark.parametrize('options',[{'attempts':0},{'attempts':6},{'concurrency':0},{'timeout_seconds':0},{'timeout_seconds':float('nan')}])
def test_invalid_client_limits(options):
    with pytest.raises(MediaError):
        RetrySettings(**options)


def test_explicit_env_loading_and_environment_precedence(tmp_path,monkeypatch):
    file=tmp_path/'test.env';file.write_text('export BODHAN_TRANSCRIBE_KEY="placeholder-from-file" # comment\n')
    monkeypatch.delenv('BODHAN_TRANSCRIBE_KEY',raising=False)
    assert load_key(file)=='placeholder-from-file'
    with pytest.raises(MediaError):
        load_key()
    monkeypatch.setenv('BODHAN_TRANSCRIBE_KEY','placeholder-from-environment')
    assert load_key(file)=='placeholder-from-environment'


def test_default_cli_is_dry_without_loading_credentials(tmp_path,monkeypatch,capsys):
    make_run(tmp_path)
    monkeypatch.setattr('bodhan_dub.transcription.load_key',lambda *_:pytest.fail('Dry run must not read keys'))
    monkeypatch.setattr('bodhan_dub.transcription.http_transport',lambda *_:pytest.fail('No network allowed'))
    assert main(['--run',str(tmp_path)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='dry_run' and result['hosted_api_calls']==0
    assert not (tmp_path/'transcription_report.json').exists()


def test_run_reports_raw_windows_without_false_reconciliation(tmp_path):
    make_run(tmp_path)
    transport=ScriptedTransport(response())
    client=TranscriptionClient(KEY,tmp_path/'cache',transport=transport)
    report=transcribe_run(tmp_path,client)
    assert report['status']=='transcribed_unreconciled'
    assert not report['overlap_reconciled'] and not report['dubbing_complete']
    assert len(report['windows'])==2 and len(transport.calls)==1
    assert report['windows'][1]['result']['cache_hit']
    assert report['windows'][1]['start_seconds']==1


def test_fatal_auth_stops_later_paid_requests(tmp_path):
    make_run(tmp_path)
    transport=ScriptedTransport(Response(401,b''))
    report=transcribe_run(tmp_path,TranscriptionClient(KEY,tmp_path/'cache',transport=transport))
    assert report['status']=='partial_failure'
    assert [r['status'] for r in report['windows']]==['failed','not_attempted']
    assert len(transport.calls)==1


def test_preflight_all_before_any_call(tmp_path):
    make_run(tmp_path)
    (tmp_path/'asr_windows/window-0002.wav').write_bytes(b'broken')
    transport=ScriptedTransport(response())
    with pytest.raises(MediaError):
        transcribe_run(tmp_path,TranscriptionClient(KEY,tmp_path/'cache',transport=transport))
    assert not transport.calls


def test_http_transport_discards_errors_and_blocks_redirects(monkeypatch):
    captured=[]
    class Opener:
        def open(self,*args,**kwargs):
            raise urllib.error.HTTPError(ENDPOINT,401,'No',{},io.BytesIO(KEY.encode()))
    def builder(handler):
        captured.append(handler)
        return Opener()
    monkeypatch.setattr('urllib.request.build_opener',builder)
    result=http_transport(Request(ENDPOINT,{},b'body',1))
    assert result.status==401 and result.body==b''
    assert captured[0].redirect_request(None,None,None,None,None,'https://other.invalid') is None


def test_source_pipeline_can_retry_one_uncertain_call(tmp_path):
    settings = RetrySettings(retry_ambiguous_once=True)
    transport = ScriptedTransport(AmbiguousCall('timeout'), response())
    client = TranscriptionClient(KEY, tmp_path/'cache', settings=settings, transport=transport, sleep=lambda _: None)
    result = client.transcribe(make_wav(tmp_path/'audio.wav'))
    assert len(result['attempts']) == 2
    assert result['attempts'][0]['billing'] == 'unknown'
