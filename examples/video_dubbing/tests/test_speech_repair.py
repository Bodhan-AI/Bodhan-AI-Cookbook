from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.speech_repair import assemble,main,plan,split_text
from bodhan_dub.synthesis import inspect_wav


def wav(samples=b'\x10\x00'*2400):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:
        f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(samples)
    return buffer.getvalue()


def source(tmp_path,text=None):
    text=text or ('ஒரு வாக்கியம். மற்றொரு வாக்கியம். '*8)
    p=tmp_path/'source';(p/'ta').mkdir(parents=True)
    blob=wav();(p/'ta/unit-0006.wav').write_bytes(blob)
    d={'languages':{'ta':{'units':[{'id':'unit-0006','status':'synthesized','text':text,'source_window_envelope':{'start_seconds':0,'end_seconds':10},
         'result':{'voice':'Anitha','audio':inspect_wav(blob)}}]}}}
    path=p/'synthesis_report.json';path.write_text(json.dumps(d));return path


@pytest.mark.parametrize('text',['A sentence. '*45,'தமிழ் உரை, '*50,'word '*100,'short.'])
def test_split_preserves_every_character_and_bounds(text):
    pieces=split_text(text,80)
    assert ''.join(pieces)==text and all(0<len(p)<=80 for p in pieces)


def test_prefers_full_sentence_before_clause():
    text='First complete sentence. '+('next phrase, '*10)
    assert split_text(text,80)[0]=='First complete sentence. '


@pytest.mark.parametrize('text,bound',[('x'*500,140),('',140),('valid',20),('valid',True)])
def test_invalid_or_unsplittable_input_rejected(text,bound):
    with pytest.raises(MediaError):split_text(text,bound)


def test_plan_matches_original_text_voice_and_exact_cost(tmp_path):
    path=source(tmp_path);p=plan(path,'ta',['unit-0006'],80)
    assert ''.join(j['text'] for j in p['jobs'])==p['parents'][0]['text']
    assert all(j['voice']=='Anitha' for j in p['jobs'])
    assert Decimal(p['synthesis_cost_inr'])==Decimal(len(p['parents'][0]['text']))*Decimal('.0006')
    with pytest.raises(MediaError):plan(path,'ta',['unit-0006','unit-0006'])
    with pytest.raises(MediaError):plan(path,'ta',['missing'])
    with pytest.raises(MediaError):plan(path,'hi',['unit-0006'])


def parts(tmp_path,p):
    units=[]
    for n,j in enumerate(p['jobs'],1):
        blob=wav(bytes([n,0])*2400);path=tmp_path/(j['id']+'.wav');path.write_bytes(blob)
        units.append({**j,'status':'synthesized','result':{'audio_path':str(path),'audio':inspect_wav(blob)}})
    return {'languages':{'ta':{'units':units}}}


def test_assembly_preserves_every_pcm_sample_in_order_and_original(tmp_path):
    path=source(tmp_path);original=path.read_bytes();p=plan(path,'ta',['unit-0006'],80);chunks=parts(tmp_path,p)
    r=assemble(p,chunks,tmp_path/'candidate');row=r['languages']['ta']['units'][0]
    assert not r['content_verified'] and not r['fit_to_timeline']
    with wave.open(row['result']['audio_path'],'rb') as f:actual=f.readframes(f.getnframes())
    expected=b''
    for u in chunks['languages']['ta']['units']:
        with wave.open(u['result']['audio_path'],'rb') as f:expected+=f.readframes(f.getnframes())
    assert actual==expected and path.read_bytes()==original


def test_missing_piece_marks_failed_without_incomplete_assembly(tmp_path):
    p=plan(source(tmp_path),'ta',['unit-0006'],80);r=parts(tmp_path,p);r['languages']['ta']['units'].pop()
    output=assemble(p,r,tmp_path/'candidate')
    assert output['status']=='partial_failure' and not (tmp_path/'candidate/ta/unit-0006.wav').exists()


def test_changed_piece_text_or_audio_refused(tmp_path):
    p=plan(source(tmp_path),'ta',['unit-0006'],80);r=parts(tmp_path,p)
    changed=deepcopy(r);changed['languages']['ta']['units'][0]['text']='changed'
    with pytest.raises(MediaError):assemble(p,changed,tmp_path/'candidate')
    Path(r['languages']['ta']['units'][0]['result']['audio_path']).write_bytes(wav(b'\x20\x00'*2400))
    with pytest.raises(MediaError):assemble(p,r,tmp_path/'candidate')


def test_dry_run_and_budget_rejection_make_no_calls_or_outputs(tmp_path,monkeypatch,capsys):
    path=source(tmp_path)
    monkeypatch.setattr('bodhan_dub.speech_repair.load_key',lambda *_:pytest.fail('No keys'))
    args=['--synthesis',str(path),'--language','ta','--units','unit-0006','--out',str(tmp_path/'new')]
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)['hosted_calls']==0
    assert not (tmp_path/'new').exists()
    assert main(args+['--execute','--budget-inr','0'])==1
    assert not (tmp_path/'new').exists()
