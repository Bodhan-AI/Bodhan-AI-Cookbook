"""Full command integration with actual media tools and mocked hosted transports."""
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from bodhan_dub import pipeline as p
from bodhan_dub.__main__ import main
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings
from bodhan_dub.transcription import Response,TranscriptionClient
from bodhan_dub.translation import TranslationClient
from bodhan_dub.synthesis import SpeechClient


@pytest.mark.parametrize('failed_language,garbled_source,source_conflict',[(None,False,False),('bn',False,False),(None,True,False),(None,False,True)])
def test_required_command_runs_all_stages_without_live_network(tmp_path,monkeypatch,failed_language,garbled_source,source_conflict):
    from threading import Event
    verification_started = Event()
    original_verify = p.qa.verify
    original_synthesize = p.tts.synthesize_run

    def observe_verification(*args, **kwargs):
        verification_started.set()
        return original_verify(*args, **kwargs)

    def observe_synthesis(*args, **kwargs):
        result = original_synthesize(*args, **kwargs)
        if kwargs.get('on_piece') and result['status'] == 'synthesized':
            assert verification_started.wait(10), 'Ready groups should enter verification during synthesis'
        return result

    monkeypatch.setattr(p.qa, 'verify', observe_verification)
    monkeypatch.setattr(p.tts, 'synthesize_run', observe_synthesis)
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No real network'))
    video=tmp_path/'source.mp4'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=black:s=64x64:r=10:d=30',
        '-f','lavfi','-i','sine=frequency=220:duration=30','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
    monkeypatch.setattr(p,'detect_speech',lambda *_:{'sample_rate':16000,'total_samples':480000,
        'speech_regions':[{'start_sample':16000,'end_sample':464000}],
        'non_speech_regions':[{'start_sample':0,'end_sample':16000},{'start_sample':464000,'end_sample':480000}]})
    for module in [p.asr,p.mt,p.tts]:monkeypatch.setattr(module,'load_key',lambda *_:'fake')
    monkeypatch.setattr(p.rec,'load_settings',lambda *_:LLMSettings('https://example.test/v1','test','fake'))
    # Hosted semantic model behavior has its own mocked integration coverage.
    monkeypatch.setattr(p,'before_speech',lambda units,translated,*_,**_kw:translated)
    monkeypatch.setattr(p.rec,'reconcile',lambda windows,*_:p.deterministic_draft(windows))
    from bodhan_dub.source_punctuation import punctuate_source
    def punctuate(windows,draft,settings,cache):
        return punctuate_source(windows,draft,settings,cache,lambda *_:(json.dumps({'text':draft['text']}),None))
    monkeypatch.setattr(p,'punctuate_source',punctuate)
    monkeypatch.setattr(p.grouping,'llm_plan',lambda *_:(_ for _ in ()).throw(MediaError('fixture_fallback','Use deterministic grouping.')))
    original_correct=p.correct
    def correct(*args,**kwargs):
        assert callable(kwargs['recovery']) and kwargs['max_recovery_calls']==2
        return {'blocked_window_ids':['fixture-conflict']} if source_conflict else original_correct(*args,**kwargs)
    monkeypatch.setattr(p,'correct',correct)
    target='अपनी नोटबुक खोलें।';calls=[]
    english=iter(['Open your notebook draw a line.','draw a line name the angles.','name the angles close your notebook.'])
    def recognize(request):
        lang='hi' if b'\r\nhi\r\n' in request.body else 'en';calls.append('asr-'+lang)
        count=calls.count('asr-en')
        if lang=='hi':text=target
        elif count<=3:
            text=next(english)
            if garbled_source and count==1:text='ୄ'*120
        else:text='Open your notebook' if count==4 else 'notebook draw a line.'
        return Response(200,json.dumps({'text':text}).encode())
    monkeypatch.setattr(p.asr,'TranscriptionClient',lambda key,cache,settings,**kw:TranscriptionClient(key,cache,settings,transport=recognize,**kw))
    def translate(request):
        calls.append('translation');body=json.loads(request.body)
        if 'Bengali' in body['messages'][0]['content']:return Response(401,b'{}')
        ids=re.findall(r'<segment id="([^"]+)">',body['messages'][0]['content'])
        text='<bodhan_segments>'+''.join('<segment id="'+i+'">'+target+'</segment>' for i in ids)+'</bodhan_segments>'
        return Response(200,json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'completion_tokens':10}}).encode())
    monkeypatch.setattr(p.mt,'TranslationClient',lambda key,cache,settings,**kw:TranslationClient(key,cache,settings,transport=translate,**kw))
    stream=io.BytesIO()
    with wave.open(stream,'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes((np.sin(np.arange(24000)*2*np.pi*220/24000)*1000).astype('<i2').tobytes())
    def speak(request):calls.append('speech');return Response(200,stream.getvalue())
    monkeypatch.setattr(p.tts,'SpeechClient',lambda key,cache,ffmpeg,settings,**kw:SpeechClient(key,cache,ffmpeg,settings,transport=speak,**kw))
    out=tmp_path/'result'
    langs='hi,bn' if failed_language else 'hi'
    assert main(['--input',str(video),'--langs',langs,'--out',str(out),'--allow-short-input'])==int(bool(failed_language) or source_conflict)
    report=json.loads((out/'report.json').read_text())
    if source_conflict:
        assert report['error']['code']=='unresolved_source_conflict'
        assert 'translation' not in calls and 'speech' not in calls
        return
    assert report['dubbing_complete']==(not failed_language) and report['fit_validation']['all_units_covered_once']
    if failed_language:assert report['languages']['bn']['status']=='failed'
    assert report['languages']['hi']['skipped_units']==0
    assert (out/'hi/video.hi.mp4').is_file() and (out/'hi/subtitles.hi.srt').is_file()
    assert calls.count('asr-en')==(5 if garbled_source else 3) and calls.count('speech')==calls.count('asr-hi')
    if garbled_source:assert report['source_quality_recovery'][0]['status']=='recovered'
    assert not report['fit_validation']['source_sync_within_300ms_verified']
    assert report['quality_targets']['segment_sync']['status']=='unverified'
    assert report['quality_targets']['runtime']['status']=='unverified'  # Thirty seconds is not the reference benchmark.
    events=[json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    assert events[-1]['stage']=='videos_and_subtitles'
    assert all('elapsed_seconds' in event for event in events)
    count=len(calls)
    assert main(['--input',str(video),'--langs',langs,'--out',str(out),'--allow-short-input'])==1
    assert len(calls)==count


def test_unknown_language_fails_before_any_calls(tmp_path,monkeypatch):
    monkeypatch.setattr(p.asr,'load_key',lambda *_:pytest.fail('No credentials read'))
    assert main(['--input','missing.mp4','--langs','xx','--out',str(tmp_path/'out')])==1
    assert not (tmp_path/'out').exists()


def test_budget_reserves_before_operation_and_keeps_uncertain_charge(tmp_path):
    budget=p.Budget(tmp_path/'budget.json','1');calls=[]
    with pytest.raises(MediaError):p.stage_call(budget,'too-large','2',lambda:calls.append(True),lambda _:0)
    assert not calls
    with pytest.raises(OSError):p.stage_call(budget,'uncertain','.4',lambda:(_ for _ in ()).throw(OSError()),lambda _:0)
    assert str(budget.spent)=='0.4'


def test_short_phrases_share_requests_and_long_phrases_stay_whole():
    text = 'एक। दो। तीन चार पाँच छह सात। अंत।'
    pieces = list(p.speech_pieces(text, target_characters=10))
    assert pieces == ['एक। दो। ', 'तीन चार पाँच छह सात। ', 'अंत।']
    assert ''.join(pieces) == text
