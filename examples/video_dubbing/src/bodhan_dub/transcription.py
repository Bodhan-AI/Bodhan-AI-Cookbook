"""Bodhan ASR client and explicit opt-in execution for existing WAV windows."""
import argparse
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import tempfile
import threading
import time
from typing import Callable
import urllib.error
import urllib.request
import uuid
import wave

from filelock import FileLock, Timeout as LockTimeout

from .media import MediaError
from .vad import SAMPLE_RATE
from .request_pacing import RequestPacer, PacedTransport

MODEL = 'indic-transcribe'
ENDPOINT = 'https://api.bodhan.ai/v1/audio/transcriptions'
RATE = Decimal('0.10')
RESPONSE_LIMIT = 2_000_000
ASR_LANGUAGES = frozenset('as bhb bho bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur'.split())


@dataclass(frozen=True)
class RetrySettings:
    attempts: int = 3
    timeout_seconds: float = 60
    concurrency: int = 1
    retry_ambiguous_once: bool = False

    def __post_init__(self):
        if type(self.attempts) is not int or not 1 <= self.attempts <= 5:
            raise MediaError('invalid_retry_settings', 'Use one to five total attempts.')
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise MediaError('invalid_retry_settings', 'Call timeout must be finite, positive and at most 300 seconds.')
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 4:
            raise MediaError('invalid_retry_settings', 'Use an API concurrency limit of one to four.')


@dataclass(frozen=True)
class Request:
    url: str
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)
    timeout: float


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = field(repr=False)
    headers: dict = field(default_factory=dict)


class AmbiguousCall(Exception):
    """A request may have reached the provider; do not silently repeat it."""


def http_transport(request: Request) -> Response:
    req=urllib.request.Request(request.url,data=request.body,headers=request.headers,method='POST')
    # Do not follow redirects with a bearer token to another destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req,timeout=request.timeout) as response:
            body=response.read(RESPONSE_LIMIT+1)
            if len(body)>RESPONSE_LIMIT:
                raise AmbiguousCall('Oversized response after a possibly billable call.')
            return Response(response.status,body,dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        # Error bodies may echo credentials/input; never persist or print them.
        result=Response(exc.code,b'',dict(exc.headers.items()))
        exc.close()
        return result
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        raise AmbiguousCall('Transport failed after possible provider processing.') from exc


_DEFAULT_PACER = RequestPacer(20)

def paced_http_transport(request):
    return PacedTransport(http_transport,_DEFAULT_PACER)(request)


def audio_input(path: Path) -> tuple[bytes,dict]:
    try:
        with wave.open(str(path),'rb') as audio:
            frames,rate=audio.getnframes(),audio.getframerate()
            if (rate,audio.getnchannels(),audio.getsampwidth(),audio.getcomptype())!=(SAMPLE_RATE,1,2,'NONE') or not 0<frames<=rate*30:
                raise MediaError('invalid_asr_audio','Expected nonempty <=30-second 16 kHz mono PCM16 WAV.')
            if len(audio.readframes(frames))!=frames*2:
                raise MediaError('invalid_asr_audio','The WAV samples are truncated.')
        blob=path.read_bytes()
    except MediaError:
        raise
    except (OSError,wave.Error,EOFError) as exc:
        raise MediaError('invalid_asr_audio','Cannot read a prepared ASR WAV.') from exc
    seconds=Decimal(frames)/Decimal(rate)
    return blob,{'sha256':hashlib.sha256(blob).hexdigest(),'frames':frames,'sample_rate':rate,
                 'duration_seconds':float(seconds),'estimated_cost_inr':str(seconds/60*RATE)}


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as output:
            temporary=Path(output.name)
            json.dump(value,output,ensure_ascii=False,indent=2)
            output.write('\n')
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def multipart(blob: bytes, language: str) -> tuple[bytes,str]:
    boundary='bodhan-'+uuid.uuid4().hex
    parts=[]
    for name,value in [('model',MODEL),('language',language)]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    # Fixed upload name avoids passing local paths to the remote service.
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="window.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+blob+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts),'multipart/form-data; boundary='+boundary


class TranscriptionClient:
    def __init__(self, key: str, cache: Path, settings: RetrySettings | None=None,
                 transport: Callable[[Request],Response]=paced_http_transport, sleep: Callable[[float],None]=time.sleep, allowed_languages=None):
        if not key or not key.strip() or key.startswith('<'):
            raise MediaError('missing_asr_key','Set BODHAN_TRANSCRIBE_KEY for an explicitly approved live run.')
        self.allowed_languages=frozenset({'en'} if allowed_languages is None else allowed_languages)
        if not self.allowed_languages or not self.allowed_languages<=ASR_LANGUAGES:
            raise MediaError('unsupported_source_language','Use documented Bodhan ASR language codes.')
        self._key=key
        self.cache=cache
        self.settings=settings or RetrySettings()
        self.transport=transport
        self.sleep=sleep
        self.limit=threading.BoundedSemaphore(self.settings.concurrency)

    def transcribe(self,path: Path,language: str='en',expected_sha256: str | None=None) -> dict:
        if language not in self.allowed_languages:
            raise MediaError('unsupported_source_language','This client does not allow the requested ASR language.')
        blob,info=audio_input(path)
        if expected_sha256 is not None and info['sha256']!=expected_sha256:
            raise MediaError('asr_input_changed','Audio changed after preflight; no request was sent.')
        identity={'schema':1,'input_sha256':info['sha256'],'model':MODEL,'language':language}
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        self.cache.mkdir(parents=True,exist_ok=True)
        state_path=self.cache/(fingerprint+'.json')
        # Lock a content identity across threads/processes before checking cache.
        lock=FileLock(str(state_path)+'.lock',timeout=self.settings.timeout_seconds*self.settings.attempts+180)
        try:
            with lock:
                if state_path.exists():
                    try:
                        state=json.loads(state_path.read_text())
                        if state['identity']!=identity:
                            raise ValueError
                        if state['state']=='complete' and isinstance(state['result']['text'],str):
                            return {**state['result'],'cache_hit':True,'attempts':[], 'new_estimated_cost_inr':'0'}
                        if state['state'] in {'in_flight','unknown'}:
                            raise MediaError('ambiguous_previous_call','A previous request may have been billed. Inspect the saved state before deliberately retrying; no automatic request made.')
                        if state['state']!='failed':
                            raise ValueError
                    except (ValueError,KeyError,TypeError) as exc:
                        raise MediaError('invalid_asr_cache','Cache state is unreadable; inspect it before any deliberate paid resubmission.') from exc
                with self.limit:
                    return self._request(blob,info,identity,state_path,language)
        except LockTimeout as exc:
            raise MediaError('asr_cache_busy','Another request owns this audio cache entry; retry later without duplicating it.') from exc

    def _request(self,blob,info,identity,state_path,language):
        attempts=[]
        def failure(code,message):
            error=MediaError(code,message)
            error.attempts=list(attempts)
            error.estimated_new_cost_if_billed_inr=str(Decimal(info['estimated_cost_inr'])*len(attempts))
            return error
        body,content_type=multipart(blob,language)
        request=Request(ENDPOINT,{'Authorization':'Bearer '+self._key,'Content-Type':content_type,'User-Agent':'BodhanDub/0.1'},body,self.settings.timeout_seconds)
        for number in range(1,self.settings.attempts+1):
            atomic_json(state_path,{'identity':identity,'state':'in_flight','attempts':attempts,'next_attempt':number})
            started=time.monotonic()
            try:
                response=self.transport(request)
            except Exception as exc:
                # Unknown transport exceptions are conservative too: do not use
                # an exception message that could expose a credential/body.
                attempts.append({'attempt':number,'status':'unknown','latency_seconds':round(time.monotonic()-started,3),'billing':'unknown'})
                atomic_json(state_path,{'identity':identity,'state':'unknown','attempts':attempts})
                if (self.settings.retry_ambiguous_once and isinstance(exc, AmbiguousCall)
                        and number < self.settings.attempts
                        and sum(event.get('status') == 'unknown' for event in attempts) == 1):
                    self.sleep(1)
                    continue
                raise failure('ambiguous_asr_call','No trustworthy response was received. The call may be billed; automatic retries are blocked.') from exc
            event={'attempt':number,'http_status':response.status,'latency_seconds':round(time.monotonic()-started,3),
                   'estimated_cost_if_billed_inr':info['estimated_cost_inr'],'billing':'estimated' if response.status==200 else 'unknown'}
            attempts.append(event)
            if response.status==200:
                try:
                    data=json.loads(response.body)
                    if not isinstance(data,dict) or not isinstance(data.get('text'),str) or 'error' in data:
                        raise ValueError
                    text=data['text'].replace(self._key,'[REDACTED]')
                    usage=data.get('usage')
                    safe_usage={k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','audio_seconds'} and type(v) in {int,float} and math.isfinite(v)} if isinstance(usage,dict) else None
                except (ValueError,TypeError,UnicodeDecodeError) as exc:
                    atomic_json(state_path,{'identity':identity,'state':'unknown','attempts':attempts,'reason':'unusable_success_response'})
                    raise failure('invalid_asr_response','The provider response was unusable after a possibly billable call; no automatic paid retry.') from exc
                result={'text':text,'status':'transcribed' if text.strip() else 'empty_transcript','model':MODEL,'language':language,
                        'input':info,'provider_usage':safe_usage,'attempts':attempts,'cache_hit':False,
                        'new_estimated_cost_inr':str(Decimal(info['estimated_cost_inr'])*len(attempts)),
                        'cost_note':'Duration-rate estimate assuming each attempt was billed; actual billing must be checked.','timestamps':'supplied by source window; not word-aligned'}
                atomic_json(state_path,{'identity':identity,'state':'complete','result':result})
                return result
            code={401:'invalid_asr_key',403:'asr_forbidden',404:'asr_endpoint_missing',402:'asr_credit_exhausted',413:'asr_input_too_large'}.get(response.status,'asr_http_error')
            retryable=response.status==429 or 500<=response.status<=599
            atomic_json(state_path,{'identity':identity,'state':'failed','attempts':attempts,'error_code':code})
            if not retryable or number==self.settings.attempts:
                raise failure(code,f'Transcription failed with HTTP {response.status}; see cache attempt metadata. No provider error body was logged.')
            delay=min(2**(number-1),8)
            try:
                retry_after=next((v for k,v in response.headers.items() if k.lower()=='retry-after'),None)
                if retry_after is not None:
                    value=float(retry_after)
                    if math.isfinite(value):
                        delay=max(delay,min(max(0,value),60))
            except (ValueError,TypeError):
                pass
            event['backoff_seconds']=delay
            self.sleep(delay)


def load_key(env_file: Path | None=None) -> str:
    """Environment wins; read a specifically selected dotenv file, never search."""
    key=os.environ.get('BODHAN_TRANSCRIBE_KEY')
    if key:
        return key
    if env_file is not None:
        try:
            for line in env_file.read_text().splitlines():
                name,separator,value=line.strip().removeprefix('export ').partition('=')
                if separator and name.strip()=='BODHAN_TRANSCRIBE_KEY':
                    parts=shlex.split(value,comments=True)
                    if len(parts)==1:
                        return parts[0]
        except (OSError,ValueError) as exc:
            raise MediaError('invalid_env_file','Cannot read the explicitly selected environment file.') from exc
    raise MediaError('missing_asr_key','Set BODHAN_TRANSCRIBE_KEY or explicitly supply --env-file. No automatic dotenv search.')


def inspect_windows(run: Path) -> tuple[list[dict],dict]:
    """Preflight every file before any provider call; never trust manifest costs."""
    try:
        plan=json.loads((run/'protected_regions.json').read_text())
        if plan['sample_rate']!=SAMPLE_RATE or type(plan['total_samples']) is not int or not 0<plan['total_samples']<=SAMPLE_RATE*900:
            raise ValueError
        rows=[]
        cursor=0
        for index,w in enumerate(plan['asr_windows'],1):
            start,end,a,b=[w[k] for k in ['start_sample','end_sample','core_start_sample','core_end_sample']]
            if any(type(n) is not int for n in [start,end,a,b]) or not 0<=start<=a<b<=end<=plan['total_samples'] or a!=cursor:
                raise ValueError
            path=run/'asr_windows'/f'window-{index:04d}.wav'
            if path.resolve().parent!=(run/'asr_windows').resolve():
                raise ValueError
            _,info=audio_input(path)
            if info['frames']!=end-start:
                raise ValueError
            rows.append({'id':f'window-{index:04d}','file':str(path),'start_seconds':start/SAMPLE_RATE,'end_seconds':end/SAMPLE_RATE,
                         'core_start_seconds':a/SAMPLE_RATE,'core_end_seconds':b/SAMPLE_RATE,'audio':info})
            cursor=b
        if not rows or cursor!=plan['total_samples']:
            raise ValueError
        cost=sum((Decimal(r['audio']['estimated_cost_inr']) for r in rows),Decimal(0))
        return rows,{'windows':len(rows),'audio_seconds':sum(r['audio']['duration_seconds'] for r in rows),'estimated_first_pass_cost_inr':str(cost),
                     'rate_inr_per_audio_minute':str(RATE),'source':'published/assignment rate; billing not confirmed'}
    except MediaError:
        raise
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise MediaError('invalid_transcription_run','Expected complete boundary manifest and matching request WAVs.') from exc


def transcribe_run(run: Path, client: TranscriptionClient) -> dict:
    rows,estimate=inspect_windows(run)
    report={'status':'transcribing','estimate':estimate,'windows':[],'overlap_reconciled':False,'dubbing_complete':False,
            'limitations':['Raw per-window English results only. Overlapping text is not yet reconciled; do not dub concatenated results.','Window timings are not word timestamps.','Empty transcripts and VAD/ASR disagreement need later QA.']}
    fatal=None
    for row in rows:
        entry={k:v for k,v in row.items() if k!='file'}
        if fatal:
            entry.update(status='not_attempted',reason=fatal)
            report['windows'].append(entry)
            continue
        try:
            result=client.transcribe(Path(row['file']),expected_sha256=row['audio']['sha256'])
            entry.update(status=result['status'],result=result)
        except MediaError as exc:
            entry.update(status='failed',error={'code':exc.code,'message':str(exc)}, attempts=getattr(exc,'attempts',[]),estimated_new_cost_if_billed_inr=getattr(exc,'estimated_new_cost_if_billed_inr','0'))
            if exc.code in {'invalid_asr_key','asr_forbidden','asr_endpoint_missing','ambiguous_asr_call','invalid_asr_response','ambiguous_previous_call','asr_input_changed'}:
                fatal='Stopped after '+exc.code+'; independent paid calls were not started.'
        report['windows'].append(entry)
        atomic_json(run/'transcription_report.json',report)
    failed=any(r['status'] in {'failed','not_attempted'} for r in report['windows'])
    report['status']='partial_failure' if failed else 'transcribed_unreconciled'
    report['estimated_new_cost_inr']=str(sum((Decimal(r['result']['new_estimated_cost_inr']) for r in report['windows'] if 'result' in r),Decimal(0)))
    report['estimated_new_cost_inr']=str(Decimal(report['estimated_new_cost_inr'])+sum((Decimal(r.get('estimated_new_cost_if_billed_inr','0')) for r in report['windows'] if r['status']=='failed'),Decimal(0)))
    report['cost_note']='Conservative duration-rate estimate for new attempted calls, including failures that may not be billed. Inspect cache states; actual billing is unconfirmed.'
    atomic_json(run/'transcription_report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Preflight existing ASR windows; live calls require --execute.')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--execute',action='store_true',help='Explicitly make billable Bodhan transcription calls.')
    parser.add_argument('--env-file',type=Path,help='Explicit dotenv file; not read during dry run.')
    parser.add_argument('--attempts',type=int,default=3)
    parser.add_argument('--requests-per-minute',type=float,default=10,help='Maximum request starts per minute; default 10, shared within this client.')
    parser.add_argument('--call-timeout',type=float,default=60)
    args=parser.parse_args(argv)
    try:
        settings=RetrySettings(args.attempts,args.call_timeout)
        pacer=RequestPacer(args.requests_per_minute)
        run=args.run.expanduser().resolve()
        _,estimate=inspect_windows(run)
        if not args.execute:
            print(json.dumps({'status':'dry_run','estimate':estimate,'hosted_api_calls':0}))
            return 0
        client=TranscriptionClient(load_key(args.env_file),run/'.transcription_cache',settings,transport=PacedTransport(http_transport,pacer))
        report=transcribe_run(run,client)
        report['rate_control']={'requests_per_minute':args.requests_per_minute,'events':pacer.events,'scope':'This process only; independent processes/key usage may still cause 429.'}
        atomic_json(run/'transcription_report.json',report)
        print(json.dumps({'status':report['status'],'report':str(run/'transcription_report.json')}))
        return 1 if report['status']=='partial_failure' else 0
    except (MediaError,OSError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','filesystem_error'),'message':str(exc) if isinstance(exc,MediaError) else 'Cannot access transcription files.'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
