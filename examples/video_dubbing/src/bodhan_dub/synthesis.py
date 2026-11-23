"""Bodhan Indic-Speak generation, real audio validation and content caching."""
import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

from filelock import FileLock,Timeout as LockTimeout

from .media import MediaError,tool_path
from .request_pacing import RequestPacer,PacedTransport
from .transcription import Request,Response,atomic_json

MODEL='indic-speak'
ENDPOINT='https://api.bodhan.ai/v1/audio/speech'
RATE=Decimal('6')/10000
MAX_BYTES=32_000_000
DEFAULT_VOICES={'as':'Prastuti','bn':'Ishita','brx':'Gwrbw','doi':'Preeti','en':'Arun','gu':'Dhara',
 'hi':'Kavya','kn':'Deepika','kok':'Anjali','ks':'Zoon','mai':'Vaidehi','ml':'Lakshmi','mni':'Thoibi',
 'mr':'Anagha','ne':'Srijana','or':'Itishree','pa':'Kaur','sa':'Bharati','sat':'Phulmani','sd':'Moomal',
 'ta':'Anitha','te':'Sravani','ur':'Saba'}
ALL_VOICES=set(DEFAULT_VOICES.values())|{'Ankur','Sourav','Sansuma','Sham','Parth','Suhani','Amit','Adarsh',
 'Sandeep','Ishfaq','Madhukar','Kiran','Chaoba','Chinmay','Sagar','Akash','Manpreet','Aryaman','Sibu','Rano','Vamsi','Zaid'}


@dataclass(frozen=True)
class SpeechSettings:
    attempts: int = 3
    timeout_seconds: float = 120
    requests_per_minute: float = 10
    workers: int = 2
    maximum_audio_seconds: int = 180

    def __post_init__(self):
        if type(self.attempts) is not int or not 1<=self.attempts<=3 or type(self.workers) is not int or not 1<=self.workers<=4:
            raise MediaError('invalid_speech_settings','Use one to three attempts and one to four concurrent workers.')
        if type(self.timeout_seconds) not in {int,float} or not math.isfinite(self.timeout_seconds) or not 0<self.timeout_seconds<=300:
            raise MediaError('invalid_speech_settings','Use a finite positive timeout of at most 300 seconds.')
        if type(self.maximum_audio_seconds) is not int or not 0<self.maximum_audio_seconds<=300:
            raise MediaError('invalid_speech_settings','Use a positive audio validation limit of at most 300 seconds.')
        RequestPacer(self.requests_per_minute)


def load_key(env_file=None):
    key=os.environ.get('BODHAN_SPEAK_KEY')
    if not key and env_file is not None:
        try:
            for line in Path(env_file).read_text().splitlines():
                name,sep,value=line.strip().removeprefix('export ').partition('=')
                if sep and name.strip()=='BODHAN_SPEAK_KEY':
                    parts=shlex.split(value,comments=True)
                    if len(parts)==1:key=parts[0];break
        except (OSError,ValueError) as exc:
            raise MediaError('invalid_env_file','Cannot read the explicitly selected environment file.') from exc
    if not key or not key.strip() or key.startswith('<'):
        raise MediaError('missing_speech_key','Set BODHAN_SPEAK_KEY or explicitly select its environment file.')
    return key


def speech_transport(request):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    req=urllib.request.Request(request.url,data=request.body,headers=request.headers,method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=request.timeout) as response:
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:raise MediaError('oversize_speech_response','Speech response exceeded the audio-size limit.')
            return Response(response.status,body,dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        result=Response(exc.code,b'',dict(exc.headers.items()));exc.close();return result


def inspect_wav(blob,maximum_seconds=180):
    try:
        with wave.open(io.BytesIO(blob),'rb') as audio:
            if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getcomptype())!=(24000,1,2,'NONE'):
                raise ValueError
            frames=audio.getnframes()
            if not 0<frames<=24000*maximum_seconds:raise ValueError
            pcm=audio.readframes(frames)
            if len(pcm)!=frames*2:raise ValueError
        samples=array('h',pcm)
        if sys.byteorder!='little':samples.byteswap()
        peak=max(abs(v) for v in samples)
        if peak==0:raise MediaError('silent_speech_output','Provider returned entirely silent audio.')
        rms=math.sqrt(sum(v*v for v in samples)/len(samples))
        return {'duration_seconds':frames/24000,'frames':frames,'sample_rate':24000,'channels':1,'sample_width':2,
                'peak_dbfs':20*math.log10(peak/32768),'rms_dbfs':20*math.log10(rms/32768),
                'sha256':hashlib.sha256(blob).hexdigest(),'loudness_normalized':False}
    except MediaError:raise
    except (wave.Error,EOFError,ValueError,OSError) as exc:
        raise MediaError('invalid_speech_audio','Expected complete nonempty PCM16 mono 24 kHz speech within the duration bound.') from exc


def normalize_audio(blob,ffmpeg,maximum_seconds=180):
    # Sniff the actual bytes; probes returned WAV under an audio/mpeg label.
    wav=blob[:4]==b'RIFF' and blob[8:12]==b'WAVE'
    mp3=blob[:3]==b'ID3' or (len(blob)>2 and blob[0]==255 and blob[1]&224==224)
    if not wav and not mp3:
        raise MediaError('invalid_speech_audio','Provider response is not recognizable WAV or MP3 audio.')
    if wav:
        try:return blob,inspect_wav(blob,maximum_seconds)
        except MediaError as exc:
            if exc.code=='silent_speech_output':raise
    with tempfile.TemporaryDirectory(prefix='bodhan-audio-') as folder:
        raw=Path(folder)/('input.wav' if wav else 'input.mp3');out=Path(folder)/'normalized.wav'
        raw.write_bytes(blob)
        result=subprocess.run([ffmpeg,'-nostdin','-v','error','-protocol_whitelist','file,pipe','-i',str(raw),
                               '-map','0:a:0','-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(out)],
                              capture_output=True,timeout=60,check=False)
        if result.returncode!=0 or not out.exists():
            raise MediaError('invalid_speech_audio','Cannot decode the returned speech audio.')
        converted=out.read_bytes()
        return converted,inspect_wav(converted,maximum_seconds)


def atomic_bytes(path,blob):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:
            temporary=Path(output.name);output.write(blob)
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


class SpeechClient:
    def __init__(self,key,cache,ffmpeg,settings=None,transport=speech_transport,pacer=None,sleep=time.sleep,*,budget=None):
        if not key or not key.strip() or key.startswith('<'):
            raise MediaError('missing_speech_key','A speech key is required for live synthesis.')
        self._key=key;self.cache=Path(cache);self.ffmpeg=ffmpeg;self.settings=settings or SpeechSettings();self.sleep=sleep
        self.pacer=pacer or RequestPacer(self.settings.requests_per_minute)
        self.transport=PacedTransport(transport,self.pacer)
        self.limit=threading.BoundedSemaphore(self.settings.workers)
        self.budget=budget
        self.budget_lock=threading.Lock()

    def speak(self,text,lang,voice):
        if lang not in DEFAULT_VOICES or voice not in ALL_VOICES:
            raise MediaError('unsupported_speech_configuration','Use a documented speech language and voice.')
        if not isinstance(text,str) or not text.strip() or len(text)>1000:
            raise MediaError('invalid_speech_text','Speech input must be nonempty and at most 1000 characters; split at sentence boundaries first.')
        identity={'version':1,'model':MODEL,'text':text,'lang':lang,'voice':voice}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.cache.mkdir(parents=True,exist_ok=True)
        journal=self.cache/(digest+'.json');audio_path=self.cache/(digest+'.wav')
        try:
            with FileLock(str(journal)+'.lock',timeout=self.settings.timeout_seconds*self.settings.attempts+300):
                if journal.exists():
                    try:
                        state=json.loads(journal.read_text())
                        if state['identity']!=identity:raise ValueError
                        if state['state']=='complete':
                            blob=audio_path.read_bytes();info=inspect_wav(blob,self.settings.maximum_audio_seconds)
                            if info['sha256']!=state['result']['audio']['sha256']:raise ValueError
                            return {**state['result'],'cache_hit':True,'attempts':[],'estimated_new_cost_inr':'0','audio_path':str(audio_path)}
                        if state['state']!='failed':
                            raise MediaError('previous_speech_attempt','A previous request was uncertain or unusable; inspect it before a deliberate paid repeat.')
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        raise MediaError('invalid_speech_cache','Speech cache is unreadable or changed; no paid resubmission.') from exc
                with self.limit:
                    return self._budgeted_request(identity,journal,audio_path)
        except LockTimeout as exc:
            raise MediaError('speech_cache_busy','Another speech request owns this source identity.') from exc

    def _budgeted_request(self, identity, journal, audio_path):
        # The cache lock remains held. A verified hit never reaches this path.
        if self.budget is None:
            return self._request(identity, journal, audio_path)
        maximum = Decimal(len(identity['text'])) * RATE * self.settings.attempts
        with self.budget_lock:
            reservation = self.budget.reserve('speech_' + identity['lang'], maximum)
        try:
            result = self._request(identity, journal, audio_path)
        except MediaError as error:
            estimated = getattr(error, 'estimated_new_cost_if_billed_inr', None)
            if estimated is not None:
                with self.budget_lock:
                    self.budget.settle(reservation, estimated)
            raise
        with self.budget_lock:
            self.budget.settle(reservation, result['estimated_new_cost_inr'])
        return result

    def _request(self,identity,journal,audio_path):
        body={'model':MODEL,'input':identity['text'],'voice':identity['voice'],
              'instructions':json.dumps({'lang':identity['lang']})}
        request=Request(ENDPOINT,{'Authorization':'Bearer '+self._key,'Content-Type':'application/json'},
                        json.dumps(body,ensure_ascii=False).encode(),self.settings.timeout_seconds)
        cost=Decimal(len(identity['text']))*RATE;attempts=[]
        def failure(code,message):
            error=MediaError(code,message);error.attempts=list(attempts)
            error.estimated_new_cost_if_billed_inr=str(cost*len(attempts));return error
        for index in range(self.settings.attempts):
            atomic_json(journal,{'identity':identity,'state':'in_flight','attempts':attempts,'next_attempt':index+1})
            started=time.monotonic()
            try:response=self.transport(request)
            except Exception as exc:
                attempts.append({'attempt':index+1,'status':'unknown','billing':'unknown'})
                atomic_json(journal,{'identity':identity,'state':'unknown','attempts':attempts})
                raise failure('ambiguous_speech_call','No trustworthy response; a possibly billed request will not be repeated automatically.') from exc
            attempts.append({'attempt':index+1,'http_status':response.status,'latency_seconds':round(time.monotonic()-started,3),'billing':'unconfirmed'})
            if response.status==200:
                raw_path=audio_path.with_suffix('.response')
                atomic_bytes(raw_path,response.body)
                try:blob,info=normalize_audio(response.body,self.ffmpeg,self.settings.maximum_audio_seconds)
                except Exception as exc:
                    atomic_json(journal,{'identity':identity,'state':'invalid','attempts':attempts,'raw_response':raw_path.name})
                    raise failure('invalid_speech_audio','Returned audio failed decoding/validation; raw bytes preserved and automatic paid repeats blocked.') from exc
                atomic_bytes(audio_path,blob)
                result={'language':identity['lang'],'voice':identity['voice'],'characters':len(identity['text']),
                        'audio':info,'attempts':attempts,'cache_hit':False,'estimated_new_cost_inr':str(cost*len(attempts)),
                        'audio_path':str(audio_path),'content_verified':False,'fit_to_timeline':False}
                atomic_json(journal,{'identity':identity,'state':'complete','result':result})
                return result
            code={401:'invalid_speech_key',403:'speech_forbidden',404:'speech_endpoint_missing',402:'speech_credit_exhausted'}.get(response.status,'speech_http_error')
            atomic_json(journal,{'identity':identity,'state':'failed','attempts':attempts,'error_code':code})
            if response.status!=429 and not 500<=response.status<600 or index+1==self.settings.attempts:
                raise failure(code,f'Speech generation failed with HTTP {response.status}; provider error body omitted.')
            self.sleep(min(2**index,8))


def inspect_translations(path,codes,voices):
    if not codes or len(set(codes))!=len(codes) or any(c not in DEFAULT_VOICES for c in codes):
        raise MediaError('unsupported_speech_language','Use distinct supported speech language codes.')
    try:
        report=json.loads(Path(path).read_text());jobs=[]
        if any(lang not in codes for lang in voices):
            raise MediaError('invalid_voice_override','A voice override refers to a language not requested.')
        for lang in codes:
            voice=voices.get(lang,DEFAULT_VOICES[lang])
            if voice not in ALL_VOICES:raise MediaError('unsupported_speech_voice','Unknown voice override.')
            data=report['languages'][lang]
            if data['status']!='translated' and not data.get('units'):
                jobs.append({'language':lang,'voice':voice,'id':'translation-failed','text':'','source_window_envelope':None,
                             'preflight_error':'Source translation failed for this language.'})
                continue
            seen=set()
            for unit in data['units']:
                ident=unit['id'];text=unit.get('translation','')
                if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident) or ident in seen:raise ValueError
                seen.add(ident)
                if unit['status']!='translated':
                    jobs.append({'language':lang,'voice':voice,'id':ident,'text':'','source_window_envelope':unit.get('source_window_envelope'),
                                 'preflight_error':'This source translation unit failed or was skipped.'})
                    continue
                if not isinstance(text,str) or not text.strip() or len(text)>1000:
                    raise MediaError('invalid_speech_text','A translated unit exceeds the short-input character limit.')
                jobs.append({'language':lang,'voice':voice,'id':ident,'text':text,'source_window_envelope':unit['source_window_envelope']})
        if not jobs:raise ValueError
        return jobs
    except MediaError:raise
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise MediaError('invalid_speech_input','Expected complete labelled translation results.') from exc


def synthesize_run(jobs,out,client):
    out=Path(out)
    report={'status':'synthesizing','languages':{},'content_verified':False,'fit_to_timeline':False,'dubbing_complete':False,
            'limitations':['Generated audio and durations only; back-transcription QA, final source alignment, fitting and loudness normalization remain separate stages.']}
    for job in jobs:
        report['languages'].setdefault(job['language'],{'status':'synthesizing','voice':job['voice'],'units':[]})
    fatal=threading.Event()
    def work(job):
        if job.get('preflight_error'):return job,None,MediaError('source_translation_failed',job['preflight_error'])
        if fatal.is_set():return job,None,MediaError('not_attempted','Shared speech credential, credit or endpoint failed.')
        try:return job,client.speak(job['text'],job['language'],job['voice']),None
        except MediaError as exc:
            if exc.code in {'invalid_speech_key','speech_forbidden','speech_endpoint_missing','speech_credit_exhausted'}:fatal.set()
            return job,None,exc
        except (OSError,ValueError):
            return job,None,MediaError('speech_processing_failed','Cannot process or save the speech result; inspect its request journal before retrying.')
    # Shared client pacer and semaphore bound request starts/concurrency even
    # when two languages synthesize concurrently.
    with ThreadPoolExecutor(max_workers=client.settings.workers) as pool:
        futures=[pool.submit(work,j) for j in jobs]
        for future in as_completed(futures):
            job,result,error=future.result()
            row={'id':job['id'],'text':job['text'],'source_window_envelope':job['source_window_envelope']}
            if error:
                row.update(status='failed',error={'code':error.code,'message':str(error)},attempts=getattr(error,'attempts',[]),
                           estimated_new_cost_if_billed_inr=getattr(error,'estimated_new_cost_if_billed_inr','0'))
            else:
                target=out/job['language']/(job['id']+'.wav');blob=Path(result['audio_path']).read_bytes()
                if target.exists() and target.read_bytes()!=blob:
                    row.update(status='failed',error={'code':'output_exists_changed','message':'Existing unit audio differs; refusing overwrite.'})
                else:
                    if not target.exists():atomic_bytes(target,blob)
                    row.update(status='synthesized',result={**result,'audio_path':str(target)})
            entry=report['languages'][job['language']];entry['units'].append(row)
            atomic_json(out/'synthesis_report.json',report)
    order={(j['language'],j['id']):i for i,j in enumerate(jobs)}
    for lang,entry in report['languages'].items():
        entry['units'].sort(key=lambda u:order[(lang,u['id'])])
        entry['status']='synthesized' if all(u['status']=='synthesized' for u in entry['units']) else 'partial_failure'
    report['status']='synthesized' if all(e['status']=='synthesized' for e in report['languages'].values()) else 'partial_failure'
    report['estimated_new_cost_inr']=str(sum((Decimal(u['result']['estimated_new_cost_inr']) if 'result' in u else Decimal(u.get('estimated_new_cost_if_billed_inr','0'))
                                            for e in report['languages'].values() for u in e['units']),Decimal(0)))
    report['cost_note']='Character-rate estimate for new attempts; failures may not be billed. Actual provider billing unconfirmed.'
    report['rate_control']={'requests_per_minute':client.settings.requests_per_minute,'workers':client.settings.workers,'events':client.pacer.events}
    atomic_json(out/'synthesis_report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Estimate speech generation by default; live execution is explicit.')
    parser.add_argument('--translations',type=Path,required=True)
    parser.add_argument('--langs',default='hi,ta,bn')
    parser.add_argument('--voice',action='append',default=[],help='Override a voice as language=Voice.')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--attempts',type=int,default=1)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--requests-per-minute',type=float,default=10)
    args=parser.parse_args(argv)
    try:
        voices={}
        for value in args.voice:
            lang,sep,voice=value.partition('=')
            if not sep or lang in voices:raise MediaError('invalid_voice_override','Use unique language=Voice values.')
            voices[lang]=voice
        settings=SpeechSettings(attempts=args.attempts,workers=args.workers,requests_per_minute=args.requests_per_minute)
        jobs=inspect_translations(args.translations,args.langs.split(','),voices)
        cost=sum((Decimal(len(j['text']))*RATE for j in jobs),Decimal(0))
        if not args.execute:
            print(json.dumps({'status':'dry_run','hosted_calls':0,'units':len(jobs),'characters':sum(len(j['text']) for j in jobs),
                              'estimated_first_pass_cost_inr':str(cost),'estimated_all_attempts_cost_inr':str(cost*settings.attempts)}));return 0
        if args.out is None:raise MediaError('missing_speech_output','Supply --out for hosted execution.')
        ffmpeg=tool_path('ffmpeg')
        client=SpeechClient(load_key(args.env_file),args.out/'.speech_cache',ffmpeg,settings)
        report=synthesize_run(jobs,args.out,client)
        print(json.dumps({'status':report['status'],'estimated_new_cost_inr':report['estimated_new_cost_inr'],'report':str(args.out/'synthesis_report.json')}))
        return int(report['status']!='synthesized')
    except (MediaError,OSError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','filesystem_error'),
                          'message':str(exc) if isinstance(exc,MediaError) else 'Cannot access speech files.'}));return 1


if __name__=='__main__':raise SystemExit(main())
