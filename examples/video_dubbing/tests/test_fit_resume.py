import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from bodhan_dub import fit_resume
from bodhan_dub.audio_output import pcm_write
from bodhan_dub.fit_recovery import Budget
from bodhan_dub.synthesis import SpeechClient
from bodhan_dub.transcription import Response


@pytest.mark.parametrize('status',[429,None])
def test_retry_only_known_transient_speech_failure(tmp_path,monkeypatch,status):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
    parent={'id':'unit-0001','text':'தமிழ் உரை','children':['unit-0001-r1-01'],'status':'failed'}
    root=tmp_path/'fit';root.mkdir();round_dir=root/'round-1';(round_dir/'parts').mkdir(parents=True);(round_dir/'assembled').mkdir()
    manifest={'languages':{'ta':{'units':[{'id':'unit-0001','status':'recovery_failed','voice':'Anitha',
        'source_window_envelope':{'start_seconds':0,'end_seconds':2}}]}}}
    (root/'fit_manifest.json').write_text(json.dumps(manifest))
    failure={'id':parent['children'][0],'text':parent['text'],'status':'failed','attempts':[{'http_status':status}]}
    (round_dir/'parts/synthesis_report.json').write_text(json.dumps({'languages':{'ta':{'units':[failure]}}}))
    (round_dir/'assembled/synthesis_report.json').write_text(json.dumps({'languages':{'ta':{'units':[parent]}}}))
    fixture=tmp_path/'short.wav';pcm_write(fixture,(np.sin(np.arange(24000)*2*np.pi*220/24000)*1000).astype(np.int16))
    calls=[]
    def transport(request):calls.append(request);return Response(200,fixture.read_bytes())
    monkeypatch.setattr(fit_resume,'load_key',lambda *_:'fake')
    monkeypatch.setattr(fit_resume,'SpeechClient',lambda key,cache,ffmpeg,settings:SpeechClient(key,cache,ffmpeg,settings,transport=transport))
    budget=Budget(root/'budget.json','1');fit_resume.retry_throttled(root,budget,None)
    if status==429:
        assert len(calls)==1 and budget.spent>0
        assert (root/'transient-ta-unit-0001/assembled/ta/unit-0001.wav').exists()
        fit_resume.retry_throttled(root,budget,None)
        assert len(calls)==1
    else:assert not calls and budget.spent==0
