import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
from types import SimpleNamespace
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.synthesis import (DEFAULT_VOICES,SpeechClient,SpeechSettings,inspect_translations,inspect_wav,
    load_key,main,normalize_audio,synthesize_run)
from bodhan_dub.transcription import Response


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No real speech calls in tests'))


def wav(rate=24000,seconds=.1,silent=False):
    buf=io.BytesIO()
    with wave.open(buf,'wb') as out:
        out.setparams((1,2,rate,0,'NONE','not compressed'))
        out.writeframes(b''.join(struct.pack('<h',0 if silent else int(1000*math.sin(i*2*math.pi*440/rate))) for i in range(int(rate*seconds))))
    return buf.getvalue()


def client(tmp_path,responses,attempts=1):
    calls=[];waits=[]
    def transport(request):
        calls.append(request)
        if not responses:pytest.fail('Unexpected extra call')
        item=responses.pop(0)
        if isinstance(item,BaseException):raise item
        return item
    pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None,events=[])
    return SpeechClient('fake-key',tmp_path,'unused-ffmpeg',SpeechSettings(attempts=attempts),transport=transport,pacer=pacer,sleep=waits.append),calls,waits


def test_real_wav_metadata_and_duration():
    info=inspect_wav(wav())
    assert info['duration_seconds']==.1 and info['sample_rate']==24000
    assert info['channels']==1 and info['frames']==2400
    assert info['peak_dbfs']<0 and info['rms_dbfs']<0
    assert not info['loudness_normalized']


@pytest.mark.parametrize('blob',[b'not audio',wav(silent=True),wav(rate=16000),wav(seconds=0),wav()[:-100]])
def test_bad_empty_silent_or_truncated_wav_rejected(blob):
    with pytest.raises(MediaError):inspect_wav(blob)


def test_success_uses_bodhan_and_correct_instruction_contract(tmp_path):
    c,calls,_=client(tmp_path,[Response(200,wav(),{'Content-Type':'audio/mpeg'})])
    r=c.speak('नमस्ते दुनिया।','hi','Kavya')
    body=json.loads(calls[0].body)
    assert body['model']=='indic-speak' and body['input']=='नमस्ते दुनिया।'
    assert json.loads(body['instructions'])=={'lang':'hi'} and body['voice']=='Kavya'
    assert r['audio']['duration_seconds']==.1 and Path(r['audio_path']).exists()
    assert not r['content_verified'] and not r['fit_to_timeline']
    assert 'fake-key' not in repr(calls[0])


def test_success_cache_avoids_new_call_and_charge(tmp_path):
    c,calls,_=client(tmp_path,[Response(200,wav())])
    c.speak('नमस्ते दुनिया।','hi','Kavya')
    r=c.speak('नमस्ते दुनिया।','hi','Kavya')
    assert len(calls)==1 and r['cache_hit'] and r['estimated_new_cost_inr']=='0'
    assert 'fake-key' not in ''.join(p.read_text() for p in tmp_path.glob('*.json'))


def test_changed_text_or_voice_has_distinct_audio_cache(tmp_path):
    c,calls,_=client(tmp_path,[Response(200,wav()),Response(200,wav()),Response(200,wav())])
    a=c.speak('एक पाठ।','hi','Kavya')
    b=c.speak('दूसरा पाठ।','hi','Kavya')
    d=c.speak('एक पाठ।','hi','Suhani')
    assert len({a['audio_path'],b['audio_path'],d['audio_path']})==3 and len(calls)==3


def test_corrupt_cached_audio_never_silently_resubmitted(tmp_path):
    c,calls,_=client(tmp_path,[Response(200,wav())])
    r=c.speak('नमस्ते।','hi','Kavya');Path(r['audio_path']).write_bytes(b'corrupt')
    with pytest.raises(MediaError):c.speak('नमस्ते।','hi','Kavya')
    assert len(calls)==1


def test_transient_failures_have_bounded_retries(tmp_path):
    c,calls,waits=client(tmp_path,[Response(429,b''),Response(503,b''),Response(200,wav())],attempts=3)
    c.speak('नमस्ते।','hi','Kavya')
    assert len(calls)==3 and waits==[1,2]


@pytest.mark.parametrize('status',[400,401,402,403,404,422])
def test_permanent_errors_do_not_repeat_or_echo_credentials(tmp_path,status):
    c,calls,_=client(tmp_path,[Response(status,b'fake-key')],attempts=3)
    with pytest.raises(MediaError) as e:c.speak('नमस्ते।','hi','Kavya')
    assert len(calls)==1 and 'fake-key' not in str(e.value)


def test_timeout_and_unusable_audio_block_automatic_paid_repeat(tmp_path):
    for i,response in enumerate([TimeoutError('fake-key'),Response(200,b'{"error":"fake-key"}')]):
        c,calls,_=client(tmp_path/str(i),[response],attempts=3)
        with pytest.raises(MediaError):c.speak('नमस्ते।','hi','Kavya')
        with pytest.raises(MediaError):c.speak('नमस्ते।','hi','Kavya')
        assert len(calls)==1


def test_interrupted_request_journal_prevents_duplicate(tmp_path):
    c,calls,_=client(tmp_path,[KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):c.speak('नमस्ते।','hi','Kavya')
    with pytest.raises(MediaError):c.speak('नमस्ते।','hi','Kavya')
    assert len(calls)==1


@pytest.mark.parametrize('text,lang,voice',[('','hi','Kavya'),('a'*1001,'hi','Kavya'),('Text','xx','Kavya'),('Text','hi','not-a-voice')])
def test_bad_configuration_rejected_before_call(tmp_path,text,lang,voice):
    c,calls,_=client(tmp_path,[])
    with pytest.raises(MediaError):c.speak(text,lang,voice)
    assert not calls


def test_same_text_concurrent_calls_only_generate_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,calls,_=client(tmp_path,[Response(200,wav())])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.speak('नमस्ते।','hi','Kavya'),range(2)))
    assert len(calls)==1 and sum(r['cache_hit'] for r in results)==1


def test_mp3_decoded_by_real_ffmpeg_when_available(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('Requires ffmpeg')
    path=tmp_path/'tone.wav';path.write_bytes(wav(seconds=1));mp3=tmp_path/'tone.mp3'
    result=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(path),str(mp3)],capture_output=True)
    assert result.returncode==0
    audio,info=normalize_audio(mp3.read_bytes(),ffmpeg)
    assert audio[:4]==b'RIFF' and abs(info['duration_seconds']-1)<.2


def translation_fixture(path,partial=False):
    rows={}
    for lang in ['hi','ta','bn']:
        rows[lang]={'status':'translated','units':[{'id':'unit-0001','status':'translated','translation':'नमस्ते।','source_window_envelope':{'start_seconds':0,'end_seconds':15}}]}
    if partial:rows['ta']={'status':'failed','units':[]}
    path.write_text(json.dumps({'status':'partial_failure' if partial else 'translated','languages':rows}))


def test_inspect_languages_default_voices_and_partial_source_isolation(tmp_path):
    path=tmp_path/'translations.json';translation_fixture(path,partial=True)
    jobs=inspect_translations(path,['hi','ta','bn'],{})
    assert jobs[0]['voice']=='Kavya' and jobs[2]['voice']=='Ishita'
    assert jobs[1]['preflight_error']


def test_run_preserves_language_successes_and_measures_audio(tmp_path):
    path=tmp_path/'translations.json';translation_fixture(path,partial=True)
    jobs=inspect_translations(path,['hi','ta','bn'],{})
    c,calls,_=client(tmp_path/'cache',[Response(200,wav()),Response(200,wav())])
    r=synthesize_run(jobs,tmp_path/'out',c)
    assert r['status']=='partial_failure'
    assert r['languages']['hi']['status']=='synthesized' and r['languages']['bn']['status']=='synthesized'
    assert len(calls)==2 and not r['content_verified']
    assert Path(r['languages']['hi']['units'][0]['result']['audio_path']).exists()


def test_dry_run_no_keys_or_audio_calls(tmp_path,monkeypatch,capsys):
    p=tmp_path/'translations.json';translation_fixture(p)
    monkeypatch.setattr('bodhan_dub.synthesis.load_key',lambda *_:pytest.fail('No key access'))
    assert main(['--translations',str(p)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['hosted_calls']==0 and result['units']==3


def test_env_precedence_and_explicit_file(tmp_path,monkeypatch):
    monkeypatch.delenv('BODHAN_SPEAK_KEY',raising=False)
    path=tmp_path/'.env';path.write_text('BODHAN_SPEAK_KEY="fake-file-key"\n')
    assert load_key(path)=='fake-file-key'
    monkeypatch.setenv('BODHAN_SPEAK_KEY','fake-env-key')
    assert load_key(path)=='fake-env-key'


@pytest.mark.parametrize('settings',[{'attempts':0},{'workers':0},{'timeout_seconds':float('nan')},{'requests_per_minute':0},{'maximum_audio_seconds':301}])
def test_invalid_settings(settings):
    with pytest.raises(MediaError):SpeechSettings(**settings)



def test_budget_only_reserves_uncached_speech_under_the_cache_lock(tmp_path):
    from decimal import Decimal
    from bodhan_dub.fit_recovery import Budget
    from bodhan_dub.synthesis import RATE

    old = 'नमस्ते दुनिया।'
    new = 'नया पाठ।'
    c, calls, _ = client(tmp_path/'cache', [Response(200,wav()), Response(200,wav())], attempts=3)
    c.speak(old, 'hi', 'Kavya')
    cap = Decimal(len(new)) * RATE * c.settings.attempts
    c.budget = Budget(tmp_path/'budget.json', cap)
    assert c.speak(old, 'hi', 'Kavya')['cache_hit']
    assert c.budget.spent == 0
    assert not c.speak(new, 'hi', 'Kavya')['cache_hit']
    assert c.budget.spent == Decimal(len(new)) * RATE
    with pytest.raises(MediaError, match='budget would be exceeded'):
        c.speak('एक इससे भी लंबा नया और अलग पाठ।', 'hi', 'Kavya')
    assert len(calls) == 2


def test_ambiguous_speech_is_counted_and_never_replayed(tmp_path):
    from decimal import Decimal
    from bodhan_dub.fit_recovery import Budget
    from bodhan_dub.synthesis import RATE

    sentence = 'नया पाठ।'
    c, calls, _ = client(tmp_path/'cache', [TimeoutError()], attempts=3)
    c.budget = Budget(tmp_path/'budget.json', '1')
    with pytest.raises(MediaError) as error:
        c.speak(sentence, 'hi', 'Kavya')
    assert error.value.code == 'ambiguous_speech_call'
    assert c.budget.spent == Decimal(len(sentence)) * RATE
    with pytest.raises(MediaError) as retry:
        c.speak(sentence, 'hi', 'Kavya')
    assert retry.value.code == 'previous_speech_attempt'
    assert len(calls) == 1
