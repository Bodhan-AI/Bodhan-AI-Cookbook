import io
import json
from pathlib import Path
import shutil
import wave

import numpy as np
import pytest

from bodhan_dub import fit_recovery as recovery
from bodhan_dub.audio_output import RATE,pcm_write
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings,WORD
from bodhan_dub.synthesis import SpeechClient,inspect_wav
from bodhan_dub.transcription import Response,TranscriptionClient
from bodhan_dub.translation import TranslationClient


def test_actual_local_fit_and_mocked_bodhan_recovery_path(tmp_path,monkeypatch):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No live calls'))
    root=tmp_path/'detect';run=root/'saved';(run/'synthesis/ta').mkdir(parents=True);(run/'translations').mkdir()
    english='Compare what you wrote. Your notebook now has seven words, each with its own figure.'
    unit={'id':'unit-0001','text':english,'source_spans':[{'window_id':'w1','start':0,'end':len(WORD.findall(english))}],
          'source_window_envelope':{'start_seconds':0,'end_seconds':5}}
    (run/'speech_units.en.json').write_text(json.dumps({'units':[unit]}))
    (run/'corrections.final.json').write_text(json.dumps({'source_windows':[{'id':'w1','tokens':WORD.findall(english),'start_seconds':0,'end_seconds':5}]}))
    (root/'speech_regions.json').write_text(json.dumps({'sample_rate':16000,'total_samples':80000,
        'speech_regions':[{'start_sample':16000,'end_sample':48000}],
        'non_speech_regions':[{'start_sample':0,'end_sample':16000},{'start_sample':48000,'end_sample':80000}]}))
    source=run/'synthesis/ta/unit-0001.wav';pcm_write(source,(np.sin(np.arange(4*RATE)*2*np.pi*220/RATE)*1000).astype(np.int16))
    original=source.read_bytes();old_text='முன் உரை';target='ஏழு சொற்கள்'
    (run/'synthesis/synthesis_report.json').write_text(json.dumps({'languages':{'ta':{'units':[{'id':'unit-0001','status':'synthesized','text':old_text,
        'result':{'voice':'Anitha','audio':inspect_wav(original)}}]}}}))
    (run/'translations/translation.ta.json').write_text(json.dumps({'units':[{'id':'unit-0001','translation':old_text}]}))
    class NoWait:
        events=[]
        def wait(self):pass
        def observe(self,response):pass
    monkeypatch.setattr(recovery,'RequestPacer',lambda *_:NoWait())
    monkeypatch.setattr(recovery,'load_settings',lambda *_:LLMSettings('https://example.test/v1','test','fake'))
    def llm(settings,messages):
        if messages[0]['content'].startswith('Shorten'):
            return json.dumps({'items':[{'id':'unit-0001','language':'ta','english':'Compare answers. Your notebook has seven illustrated words.'}]}),None
        return json.dumps({'checks':[{'id':'ta/unit-0001','ok':True,'reason':'Fixture preserves facts'}]}),None
    monkeypatch.setattr(recovery,'sdk_completion',llm)
    for key in ['translation_key','speech_key','asr_key']:monkeypatch.setattr(recovery,key,lambda *_:'fake')
    calls=[]
    def trans(request):
        calls.append('translation');content='<bodhan_segments><segment id="unit-0001">'+target+'</segment></bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'completion_tokens':1}}).encode())
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:
        f.setparams((1,2,RATE,0,'NONE','not compressed'));f.writeframes((np.sin(np.arange(round(1.2*RATE))*2*np.pi*220/RATE)*1000).astype('<i2').tobytes())
    def speak(request):calls.append('speech');return Response(200,buffer.getvalue())
    def recognize(request):calls.append('asr');return Response(200,json.dumps({'text':target}).encode())
    monkeypatch.setattr(recovery,'TranslationClient',lambda key,cache,settings:TranslationClient(key,cache,settings,transport=trans))
    monkeypatch.setattr(recovery,'SpeechClient',lambda key,cache,ffmpeg,settings:SpeechClient(key,cache,ffmpeg,settings,transport=speak))
    monkeypatch.setattr(recovery,'TranscriptionClient',lambda key,cache,settings,**kw:TranscriptionClient(key,cache,settings,transport=recognize,allowed_languages={'ta'}))
    result=recovery.run_fit(run,tmp_path/'fit',None)
    u=result['languages']['ta']['units'][0]
    assert result['all_units_fitted'] and u['status']=='fitted' and u['content_recovery']
    assert u['fit']['speed']<=1.2 and u['qa']['cer']==0
    assert calls==['translation','speech','asr'] and source.read_bytes()==original
    assert recovery.run_fit(run,tmp_path/'fit',None)==result
    calls.clear()
    with pytest.raises(MediaError):recovery.run_fit(run,tmp_path/'too-small',None,budget_limit='.001')
    assert not calls
    partial = {**result, 'status': 'unresolved_fit', 'all_units_fitted':False}
    previous = tmp_path/'previous.json'
    previous.write_text(json.dumps(partial))
    continued = recovery.run_fit(run,tmp_path/'continued',None,resume_manifest=previous)
    assert continued['all_units_fitted']
    assert continued['languages']['ta']['units'] == result['languages']['ta']['units']
    assert continued['new_estimated_cost_inr'] == '0' and not calls
