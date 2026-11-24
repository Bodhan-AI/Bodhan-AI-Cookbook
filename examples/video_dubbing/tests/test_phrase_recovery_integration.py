"""Exercise the production fitter's new phrase path before English rewriting."""
import io
import json
import shutil
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from bodhan_dub import fit_recovery as recovery,fit_qa
from bodhan_dub.audio_output import RATE,pcm_write
from bodhan_dub.fit_validation import validate_fit_manifest
from bodhan_dub.reconciliation import WORD
from bodhan_dub.synthesis import SpeechClient,inspect_wav
from bodhan_dub.transcription import TranscriptionClient,Response


def test_multi_slot_repair_uses_complete_phrases_before_changing_english(tmp_path,monkeypatch):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_:pytest.fail('No live network'))
    root=tmp_path/'detect';run=root/'run';(run/'synthesis/hi').mkdir(parents=True);(run/'translations').mkdir()
    english='Those are the terms. Open your notebook.';target='आज वे सात शब्द हैं। अपनी नोटबुक खोलें।'
    unit={'id':'u1','text':english,'source_spans':[{'window_id':'w1','start':0,'end':len(WORD.findall(english))}],
        'source_window_envelope':{'start_seconds':0,'end_seconds':5}}
    (run/'speech_units.en.json').write_text(json.dumps({'units':[unit]}))
    (run/'corrections.final.json').write_text(json.dumps({'source_windows':[{'id':'w1','tokens':WORD.findall(english),'start_seconds':0,'end_seconds':5}]}))
    (root/'speech_regions.json').write_text(json.dumps({'sample_rate':16000,'total_samples':80000,
        'speech_regions':[{'start_sample':0,'end_sample':16000},{'start_sample':48000,'end_sample':80000}],
        'non_speech_regions':[{'start_sample':16000,'end_sample':48000}]}))
    raw=run/'synthesis/hi/u1.wav';pcm_write(raw,(np.sin(np.arange(3*RATE)*2*np.pi*220/RATE)*1000).astype(np.int16))
    (run/'synthesis/synthesis_report.json').write_text(json.dumps({'languages':{'hi':{'units':[{'id':'u1','status':'synthesized','text':target,
        'result':{'voice':'Kavya','audio':inspect_wav(raw.read_bytes())}}]}}}))
    (run/'translations/translation.hi.json').write_text(json.dumps({'units':[{'id':'u1','translation':target}]}))
    # Old-waveform CER must not be inherited by newly generated speech.
    (run/'verification').mkdir();(run/'verification/verification_report.json').write_text(json.dumps({'languages':{'hi':{'units':[{'id':'u1','cer':0,'hypothesis_text':'old audio','status':'pass'}]}}}))
    calls=[]
    monkeypatch.setattr(recovery,'speech_key',lambda *_:'fake')
    monkeypatch.setattr(recovery,'load_settings',lambda *_:pytest.fail('No English shortening needed'))
    def speak(request):
        text=json.loads(request.body)['input'];calls.append(('tts',text));seconds=.8 if text.startswith('आज') else 1.3
        stream=io.BytesIO()
        with wave.open(stream,'wb') as w:
            w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes((np.sin(np.arange(round(seconds*RATE))*2*np.pi*220/RATE)*1000).astype('<i2').tobytes())
        return Response(200,stream.getvalue())
    monkeypatch.setattr(recovery,'SpeechClient',lambda key,cache,ffmpeg,settings:SpeechClient(key,cache,ffmpeg,settings,transport=speak,
        pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None,events=[])))
    def recognize(request):calls.append(('asr',None));return Response(200,json.dumps({'text':target}).encode())
    monkeypatch.setattr(fit_qa,'load_key',lambda *_:'fake')
    monkeypatch.setattr(fit_qa,'TranscriptionClient',lambda key,cache,settings,**kw:TranscriptionClient(key,cache,settings,transport=recognize,allowed_languages={'hi'}))
    fitted=recovery.run_fit(run,tmp_path/'fit',None)
    item=fitted['languages']['hi']['units'][0]
    assert fitted['all_units_fitted'] and item['phrase_generation']
    assert item['translation']==target and item['english']==english and item['qa']['hypothesis_text']==target
    assert ''.join(text for kind,text in calls if kind=='tts')==target
    assert calls[-1][0]=='asr'
    assert validate_fit_manifest(fitted)['no_waveform_cuts_inside_generated_pieces']
