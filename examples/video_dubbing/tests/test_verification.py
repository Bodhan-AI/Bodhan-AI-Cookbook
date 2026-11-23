from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.synthesis import inspect_wav
from bodhan_dub.transcription import TranscriptionClient,Response
from bodhan_dub.verification import (assess,character_error_rate,estimate,inspect_synthesis,
    main,merge_chunks,normalize,prepare,verify,window_ranges)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No hosted verification calls in tests'))


def test_normalization_preserves_indic_marks_and_normalizes_digits():
    assert normalize('कक्षा १२!')==normalize('कक्षा12')
    assert normalize('தமிழ்')=='தமிழ்'
    assert normalize('A O C.')=='aoc'


def test_cer_exact_insertions_deletions_and_empty_hypothesis():
    assert character_error_rate('abc','abc')['cer']==0
    assert character_error_rate('abc','ab')['edit_distance']==1
    assert character_error_rate('abc','abcc')['edit_distance']==1
    assert character_error_rate('abc','')['cer']==1
    assert character_error_rate('a','aaaa')['cer']==3
    with pytest.raises(MediaError):character_error_rate('...','x')


@pytest.mark.parametrize('seconds',[.1,1,14,15,15.1,20,30.55,60,180])
def test_window_plan_bounds_and_complete_unique_ownership(seconds):
    frames=round(seconds*16000);rows=window_ranges(frames)
    assert rows[0]['core_start_sample']==0 and rows[-1]['core_end_sample']==frames
    assert sum(r['core_end_sample']-r['core_start_sample'] for r in rows)==frames
    assert all(0<r['end_sample']-r['start_sample']<=15*16000 for r in rows)
    assert all(a['core_end_sample']==b['core_start_sample'] for a,b in zip(rows,rows[1:]))
    assert all(a['end_sample']-b['start_sample']==32000 for a,b in zip(rows,rows[1:]))


def test_native_overlap_merge_independent_of_reference():
    chunks=[{'start_sample':0,'end_sample':224000,'text':'यह एक नया कोण है'},
            {'start_sample':192000,'end_sample':400000,'text':'कोण है और यह समकोण है'}]
    result,issues=merge_chunks(chunks)
    assert result=='यह एक नया कोण है और यह समकोण है'
    assert not issues


def test_no_match_not_guessed_from_target_or_filled():
    result,issues=merge_chunks([{'start_sample':0,'end_sample':20,'text':'पहला वाक्य'},
                               {'start_sample':18,'end_sample':40,'text':'अलग शब्द'}])
    assert result=='पहला वाक्य अलग शब्द' and 'overlap_not_resolved_conservatively' in issues


def test_silence_or_degenerate_recognition_cannot_pass():
    _,issues=merge_chunks([{'start_sample':0,'end_sample':20,'text':''},
                          {'start_sample':18,'end_sample':40,'text':'क'*100}])
    assert set(issues)=={'empty_recognition_chunk','degenerate_recognition_chunk'}


def test_asr_languages_are_explicit_and_default_stays_english(tmp_path):
    path=tmp_path/'input.wav'
    with wave.open(str(path),'wb') as f:f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(bytes(32000))
    calls=[]
    def transport(request):calls.append(request);return Response(200,b'{"text":"native result"}')
    default=TranscriptionClient('fake-key',tmp_path/'a',transport=transport)
    with pytest.raises(MediaError):default.transcribe(path,language='hi')
    native=TranscriptionClient('fake-key',tmp_path/'b',transport=transport,allowed_languages={'hi'})
    native.transcribe(path,language='hi')
    assert len(calls)==1 and b'\r\nhi\r\n' in calls[0].body
    with pytest.raises(MediaError):native.transcribe(path,language='ta')


def make_synthesis(tmp_path):
    root=tmp_path/'synthesis';(root/'hi').mkdir(parents=True)
    path=root/'hi/unit-0001.wav'
    with wave.open(str(path),'wb') as f:
        f.setparams((1,2,24000,0,'NONE','not compressed'))
        f.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(i*2*math.pi*440/24000))) for i in range(24000)))
    info=inspect_wav(path.read_bytes())
    report={'status':'synthesized','languages':{'hi':{'status':'synthesized','units':[{'id':'unit-0001','status':'synthesized','text':'कक्षा शुरू होती है',
              'result':{'audio':info,'audio_path':'ignored-untrusted-path'}}]}}}
    target=root/'synthesis_report.json';target.write_text(json.dumps(report))
    return target,path


def test_source_audio_digest_checked_before_calls(tmp_path):
    path,audio=make_synthesis(tmp_path)
    rows=inspect_synthesis(path)
    assert rows[0]['language']=='hi' and estimate(rows)['requests']==1
    audio.write_bytes(audio.read_bytes()+b'changed')
    with pytest.raises(MediaError):inspect_synthesis(path)


def test_real_resampling_export_and_reuse(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('Requires ffmpeg')
    path,_=make_synthesis(tmp_path);rows=inspect_synthesis(path)
    requests=prepare(rows,tmp_path/'verification',ffmpeg)
    assert len(requests)==1 and requests[0]['audio_seconds']==1
    again=prepare(rows,tmp_path/'verification',ffmpeg)
    assert again[0]['input_sha256']==requests[0]['input_sha256']


def simple_requests():
    return [{'id':'hi/u/1','language':'hi','unit_id':'u','file':'unused.wav','input_sha256':'known',
             'start_sample':0,'end_sample':16000,'core_start_sample':0,'core_end_sample':16000}]


def simple_rows():
    return [{'language':'hi','id':'u','status':'ready','reference_text':'कक्षा शुरू होती है'}]


def fake_client(text='कक्षा शुरू होती है'):
    calls=[]
    def transcribe(path,language,expected_sha256):
        calls.append((str(path),language,expected_sha256))
        return {'text':text,'new_estimated_cost_inr':'0.001666666666666666666666666667','cache_hit':False,'attempts':[{'attempt':1}]}
    return SimpleNamespace(transcribe=transcribe),calls


def test_verification_records_per_unit_cer_and_real_target_language(tmp_path):
    client,calls=fake_client()
    result=verify(simple_rows(),simple_requests(),tmp_path,client,Decimal('.01'))
    assert result['status']=='pass' and result['languages']['hi']['units'][0]['cer']==0
    assert calls[0][1:] == ('hi','known')
    assert not result['dubbing_complete']


def test_budget_exceeded_means_zero_calls(tmp_path):
    client,calls=fake_client()
    with pytest.raises(MediaError):verify(simple_rows(),simple_requests(),tmp_path,client,Decimal('0'))
    assert not calls


def test_high_cer_is_recovery_warning_not_silently_rewritten(tmp_path):
    client,_=fake_client('कुछ अलग कहा गया')
    result=verify(simple_rows(),simple_requests(),tmp_path,client,Decimal('.01'))
    unit=result['languages']['hi']['units'][0]
    assert result['status']=='needs_recovery' and unit['cer']>.2
    assert unit['reference_text']=='कक्षा शुरू होती है'


def test_failed_request_not_repeated_on_resume(tmp_path):
    calls=[]
    def transcribe(*args,**kwargs):calls.append(1);raise MediaError('ambiguous_asr_call','No trustworthy response')
    client=SimpleNamespace(transcribe=transcribe)
    verify(simple_rows(),simple_requests(),tmp_path,client,Decimal('.01'))
    result=verify(simple_rows(),simple_requests(),tmp_path,client,Decimal('.01'))
    assert len(calls)==1 and result['status']=='needs_recovery'


def test_language_failure_does_not_stop_other_languages(tmp_path):
    rows=simple_rows()+[{'language':'ta','id':'u','status':'source_failed','reason':'Speech failed'}]
    client,_=fake_client();r=verify(rows,simple_requests(),tmp_path,client,Decimal('.01'))
    assert r['languages']['hi']['status']=='pass' and r['languages']['ta']['status']=='needs_recovery'


def test_dry_run_reads_no_keys_and_makes_no_files(tmp_path,monkeypatch,capsys):
    path,_=make_synthesis(tmp_path)
    monkeypatch.setattr('bodhan_dub.verification.load_key',lambda *_:pytest.fail('No keys'))
    assert main(['--synthesis',str(path)])==0
    assert json.loads(capsys.readouterr().out)['hosted_calls']==0


def test_shared_credential_failure_stops_remaining_jobs(tmp_path):
    requests=simple_requests()+[{**simple_requests()[0],'id':'hi/u/2'}]
    calls=[]
    def transcribe(*args,**kwargs):calls.append(1);raise MediaError('invalid_asr_key','Invalid credential')
    result=verify(simple_rows(),requests,tmp_path,SimpleNamespace(transcribe=transcribe),Decimal('.01'),workers=1)
    assert len(calls)==1 and result['status']=='needs_recovery'


@pytest.mark.parametrize('budget',['nan','not-a-number','-1'])
def test_invalid_budget_rejected_without_keys_or_calls(tmp_path,monkeypatch,budget):
    path,_=make_synthesis(tmp_path)
    monkeypatch.setattr('bodhan_dub.verification.load_key',lambda *_:pytest.fail('No key access'))
    assert main(['--synthesis',str(path),'--execute','--out',str(tmp_path/'qa'),'--budget-inr',budget])==1
