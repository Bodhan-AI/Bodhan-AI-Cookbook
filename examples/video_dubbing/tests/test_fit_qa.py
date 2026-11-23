import json
import shutil
import numpy as np
import pytest
from bodhan_dub import fit_qa
from bodhan_dub.audio_output import pcm_write
from bodhan_dub.fit_recovery import Budget
from bodhan_dub.synthesis import inspect_wav
from bodhan_dub.transcription import Response,TranscriptionClient


def test_missing_retry_verification_is_filled_once(tmp_path,monkeypatch):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_:pytest.fail('No live network'))
    path=tmp_path/'raw.wav';pcm_write(path,(np.sin(np.arange(24000)*2*np.pi*220/24000)*1000).astype(np.int16))
    calls=[];target='नमस्ते दुनिया'
    def transport(request):calls.append(request);return Response(200,json.dumps({'text':target}).encode())
    monkeypatch.setattr(fit_qa,'load_key',lambda *_:'fake')
    monkeypatch.setattr(fit_qa,'TranscriptionClient',lambda key,cache,settings,**kw:TranscriptionClient(key,cache,settings,transport=transport,allowed_languages={'hi'}))
    info=inspect_wav(path.read_bytes())
    item={'id':'u1','language':'hi','status':'fitted','translation':target,'audio_path':str(path),'audio_sha256':info['sha256'],
        'phrase_generation':True,'qa':{'cer':0,'status':'pass','audio_sha256':'different-waveform'},'fit':{'original_audio':info}}
    manifest={'source_run':str(tmp_path/'source'),'languages':{'hi':{'units':[item]}}}
    budget=Budget(tmp_path/'budget.json','.1')
    fit_qa.finish_checks(manifest,tmp_path/'checks',None,budget,ffmpeg)
    assert item['qa']['cer']==0 and len(calls)==1 and budget.spent>0
    assert item['qa']['audio_sha256']==info['sha256']
    fit_qa.finish_checks(manifest,tmp_path/'checks',None,budget,ffmpeg)
    assert len(calls)==1
