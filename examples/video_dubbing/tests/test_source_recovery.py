from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import wave
import pytest
from bodhan_dub import source_recovery as recovery
from bodhan_dub.fit_recovery import Budget
from bodhan_dub.media import MediaError


def fixture(tmp_path,monkeypatch):
    path=tmp_path/'parent.wav';pcm=(b'\x01\x00'*16000+b'\x02\x00'*16000)*3
    with wave.open(str(path),'wb') as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(pcm)
    monkeypatch.setattr(recovery,'inspect_windows',lambda _ :([{'id':'w1','file':str(path)}],{}))
    return [{'id':'w1','text':'ୄ'*120,'start_seconds':10,'end_seconds':16}],pcm


def test_split_recovery_preserves_samples_context_and_budget(tmp_path,monkeypatch):
    rows,pcm=fixture(tmp_path,monkeypatch);calls=[];texts=iter(['Open the notebook.','Draw a line.'])
    def transcribe(path,**_):
        with wave.open(str(path),'rb') as w:calls.append(w.readframes(w.getnframes()))
        return {'text':next(texts),'new_estimated_cost_inr':'.001'}
    client=SimpleNamespace(settings=SimpleNamespace(attempts=1),transcribe=transcribe)
    budget=Budget(tmp_path/'budget.json',Decimal('1'))
    result,events=recovery.recover_source(rows,tmp_path,client,budget)
    assert result[0]['start_seconds']==10 and result[1]['end_seconds']==16
    assert result[0]['end_seconds']>result[1]['start_seconds']
    assert calls[0]==pcm[:int((result[0]['end_seconds']-10)*16000)*2]
    assert calls[1]==pcm[int((result[1]['start_seconds']-10)*16000)*2:]
    assert budget.spent==Decimal('.002') and events[0]['status']=='recovered'
    assert rows[0]['text']=='ୄ'*120


def test_unusable_repair_stops_without_inventing_or_looping(tmp_path,monkeypatch):
    rows,_=fixture(tmp_path,monkeypatch);calls=[]
    def transcribe(*_,**__):calls.append(True);return {'text':'ୄ'*120,'new_estimated_cost_inr':'.001'}
    client=SimpleNamespace(settings=SimpleNamespace(attempts=1),transcribe=transcribe)
    with pytest.raises(MediaError,match='no invented replacement'):
        recovery.recover_source(rows,tmp_path,client,Budget(tmp_path/'budget.json','1'))
    assert len(calls)==1


def test_recovery_limit_blocks_all_requests_before_charge(tmp_path,monkeypatch):
    rows,_=fixture(tmp_path,monkeypatch)
    client=SimpleNamespace(transcribe=lambda *_:pytest.fail('No calls'))
    budget=Budget(tmp_path/'budget.json','1')
    with pytest.raises(MediaError,match='Too many'):
        recovery.recover_source(rows,tmp_path,client,budget,maximum_windows=0)
    assert budget.spent==0


def test_boundary_adapter_reserves_and_settles_within_shared_budget(tmp_path,monkeypatch):
    calls=[]
    def adapter(*_):
        def invoke(request):
            calls.append(request)
            return {'text':'Complementary angles.','evidence':{'result':{'new_estimated_cost_inr':'.01'}}}
        return invoke
    monkeypatch.setattr(recovery,'BodhanBoundaryRecovery',adapter)
    client=SimpleNamespace(settings=SimpleNamespace(attempts=1));budget=Budget(tmp_path/'budget.json','1')
    result=recovery.boundary_recovery(tmp_path,client,budget)({'start_seconds':1,'end_seconds':13})
    assert result['text']=='Complementary angles.' and len(calls)==1 and budget.spent==Decimal('.01')
