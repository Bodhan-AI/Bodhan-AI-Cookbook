from email.utils import formatdate

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.request_pacing import RequestPacer, PacedTransport
from bodhan_dub.transcription import Response


class FakeTime:
    def __init__(self):self.now=0;self.waits=[]
    def clock(self):return self.now
    def sleep(self,n):self.waits.append(n);self.now+=n


def pacer(rate=10):
    t=FakeTime()
    return t,RequestPacer(rate,clock=t.clock,sleep=t.sleep,wall_clock=lambda:1000+t.now)


def test_spaces_request_starts_without_initial_delay():
    t,p=pacer()
    p.wait();assert t.waits==[]
    t.now+=2
    p.wait();assert t.waits==[4]
    p.wait();assert t.waits==[4,6]


def test_numeric_retry_after_and_shared_future_requests():
    t,p=pacer()
    p.wait()
    p.observe(Response(429,b'',{'Retry-After':'20'}))
    p.wait();assert t.now==20
    p.wait();assert t.now==26


def test_date_retry_after():
    t,p=pacer()
    p.observe(Response(429,b'',{'retry-after':formatdate(1030,usegmt=True)}))
    p.wait();assert t.now==30


@pytest.mark.parametrize('value',[None,'garbage','nan','inf'])
def test_missing_or_invalid_retry_after_uses_conservative_cooldown(value):
    t,p=pacer()
    p.observe(Response(429,b'',{} if value is None else {'Retry-After':value}))
    p.wait();assert t.now==60


def test_success_does_not_create_cooldown():
    t,p=pacer()
    p.observe(Response(200,b'',{'Retry-After':'60'}))
    p.wait();assert t.now==0


def test_wait_rechecks_cooldown_if_provider_extends_it():
    t,p=pacer()
    p.wait()
    original=t.sleep
    def sleep(n):
        original(n)
        if len(t.waits)==1:p.observe(Response(429,b'',{'Retry-After':'20'}))
    p.sleep=sleep
    p.wait()
    assert t.waits==[6,20]


def test_transport_paces_each_attempt_and_observes_429():
    t,p=pacer()
    starts=[]
    def transport(_):
        starts.append(t.now)
        return Response(429 if len(starts)==1 else 200,b'',{'Retry-After':'10'})
    wrapper=PacedTransport(transport,p)
    wrapper(None);wrapper(None)
    assert starts==[0,10]


def test_excessive_server_wait_stops_without_call():
    t,p=pacer()
    p.observe(Response(429,b'',{'Retry-After':'600'}))
    with pytest.raises(MediaError):p.wait()
    assert not t.waits


@pytest.mark.parametrize('rate',[0,-1,True,float('nan'),float('inf'),121,'10'])
def test_invalid_rate(rate):
    with pytest.raises(MediaError):RequestPacer(rate)


def test_asr_retry_automatically_waits_for_cooldown(tmp_path):
    import wave
    from bodhan_dub.transcription import TranscriptionClient,RetrySettings
    path=tmp_path/'input.wav'
    with wave.open(str(path),'wb') as audio:
        audio.setparams((1,2,16000,0,'NONE','not compressed'))
        audio.writeframes(bytes(32000))
    t,p=pacer()
    starts=[]
    def raw(request):
        starts.append(t.now)
        return Response(429,b'',{'Retry-After':'20'}) if len(starts)==1 else Response(200,b'{"text":"Test words"}')
    client=TranscriptionClient('fake-key',tmp_path/'cache',RetrySettings(attempts=3),transport=PacedTransport(raw,p),sleep=t.sleep)
    assert client.transcribe(path)['text']=='Test words'
    assert starts==[0,20]
