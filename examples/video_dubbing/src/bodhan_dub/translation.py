"""Bodhan-only document translation with unit labels, reuse and partial reports."""
import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
import unicodedata
import xml.etree.ElementTree as ET

from filelock import FileLock, Timeout as LockTimeout

from .media import MediaError
from .request_pacing import RequestPacer, PacedTransport
from .transcription import Request, atomic_json, http_transport

MODEL='indic-translate'
ENDPOINT='https://api.bodhan.ai/v1/chat/completions'
RATE=Decimal('0.20')/10000
LANGUAGES={'as':'Assamese','bn':'Bengali','brx':'Bodo','doi':'Dogri','en':'English','gu':'Gujarati',
 'hi':'Hindi','kn':'Kannada','kok':'Konkani','ks':'Kashmiri','ks-Deva':'Kashmiri (Devanagari)',
 'mai':'Maithili','ml':'Malayalam','mni':'Manipuri','mni-Beng':'Manipuri (Bengali)',
 'mr':'Marathi','ne':'Nepali','or':'Odia','pa':'Punjabi','sa':'Sanskrit','sat':'Santali',
 'sd':'Sindhi','sd-Arab':'Sindhi (Arabic)','ta':'Tamil','te':'Telugu','ur':'Urdu'}
SCRIPTS={'hi':(0x900,0x97f),'mr':(0x900,0x97f),'ne':(0x900,0x97f),'sa':(0x900,0x97f),
 'brx':(0x900,0x97f),'doi':(0x900,0x97f),'mai':(0x900,0x97f),'kok':(0x900,0x97f),'ks-Deva':(0x900,0x97f),
 'bn':(0x980,0x9ff),'as':(0x980,0x9ff),'mni-Beng':(0x980,0x9ff),'ta':(0xb80,0xbff),
 'te':(0xc00,0xc7f),'kn':(0xc80,0xcff),'ml':(0xd00,0xd7f),'gu':(0xa80,0xaff),
 'pa':(0xa00,0xa7f),'or':(0xb00,0xb7f),'ur':(0x600,0x6ff),'ks':(0x600,0x6ff),
 'sd-Arab':(0x600,0x6ff),'mni':(0xabc0,0xabff)}


@dataclass(frozen=True)
class TranslationSettings:
    attempts: int = 3
    max_output_tokens: int = 4096
    timeout_seconds: float = 90
    requests_per_minute: float = 10

    def __post_init__(self):
        if type(self.attempts) is not int or not 1<=self.attempts<=3:
            raise MediaError('invalid_translation_settings','Use one to three total attempts.')
        if type(self.max_output_tokens) is not int or not 256<=self.max_output_tokens<=16384:
            raise MediaError('invalid_translation_settings','Use an output budget from 256 to 16384 tokens.')
        if type(self.timeout_seconds) not in {int,float} or not math.isfinite(self.timeout_seconds) or not 0<self.timeout_seconds<=300:
            raise MediaError('invalid_translation_settings','Use a finite positive timeout of at most 300 seconds.')
        RequestPacer(self.requests_per_minute)


def load_key(env_file=None):
    value=os.environ.get('BODHAN_TRANSLATE_KEY')
    if not value and env_file is not None:
        try:
            for line in Path(env_file).read_text().splitlines():
                name,sep,body=line.strip().removeprefix('export ').partition('=')
                if sep and name.strip()=='BODHAN_TRANSLATE_KEY':
                    parts=shlex.split(body,comments=True)
                    if len(parts)==1:value=parts[0];break
        except (OSError,ValueError) as exc:
            raise MediaError('invalid_env_file','Cannot read the explicitly selected environment file.') from exc
    if not value or not value.strip() or value.startswith('<'):
        raise MediaError('missing_translation_key','Set BODHAN_TRANSLATE_KEY or explicitly select its environment file.')
    return value


def languages(codes):
    if not isinstance(codes,list) or not codes or any(not isinstance(c,str) or c not in LANGUAGES for c in codes) or len(set(codes))!=len(codes):
        raise MediaError('unsupported_translation_language','Use distinct supported translation language codes.')
    return codes


def inspect_units(path):
    try:
        data=json.loads(Path(path).read_text())
        if data['status']!='grouped_for_translation' or data['validation']['all_corrected_words_preserved_in_order'] is not True:
            raise ValueError
        rows=data['units']
        if not isinstance(rows,list) or not 1<=len(rows)<=500:raise ValueError
        ids=set()
        for row in rows:
            ident=row['id'];text=row['text']
            if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident) or ident in ids:raise ValueError
            ids.add(ident)
            if not isinstance(text,str) or not text.strip() or len(text)>10000:raise ValueError
            if row['status'] not in {'ready_for_translation','skipped'}:raise ValueError
            envelope=row['source_window_envelope'];a,b=envelope['start_seconds'],envelope['end_seconds']
            if any(type(n) not in {int,float} or not math.isfinite(n) for n in [a,b]) or not 0<=a<b<=900:raise ValueError
        if sum(len(r['text']) for r in rows)>60000:
            raise MediaError('translation_batch_too_large','Split this document before translation; it exceeds the bounded source batch.')
        return rows
    except MediaError:raise
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        raise MediaError('invalid_translation_input','Expected validated source-backed speech groups.') from exc


def document(units):
    return '<bodhan_segments>\n'+'\n'.join('<segment id="'+u['id']+'">'+html.escape(u['text'])+'</segment>' for u in units)+'\n</bodhan_segments>'


def parse_document(text,units):
    if not isinstance(text,str) or len(text)>500000 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise MediaError('invalid_translation_structure','Unusable labelled translation document.')
    text=text.strip()
    if text.startswith('```'):
        lines=text.splitlines()
        if lines[0].strip() not in {'```','```xml','```html'} or lines[-1].strip()!='```':
            raise MediaError('invalid_translation_structure','Unexpected translation wrapper.')
        text='\n'.join(lines[1:-1])
    try:root=ET.fromstring(text)
    except ET.ParseError as exc:
        raise MediaError('invalid_translation_structure','Translation did not preserve the unit labels; no unlabelled text accepted.') from exc
    if root.tag!='bodhan_segments' or root.attrib or (root.text or '').strip():
        raise MediaError('invalid_translation_structure','Unexpected translated document structure.')
    expected={u['id'] for u in units};counts=Counter();values={};errors={}
    for segment in root:
        ident=segment.attrib.get('id')
        if segment.tag!='segment' or set(segment.attrib)!={'id'} or ident not in expected or len(segment) or (segment.tail or '').strip():
            raise MediaError('invalid_translation_structure','Unknown, nested, or unlabelled translation content.')
        counts[ident]+=1;values[ident]=(segment.text or '').strip()
    for ident in expected:
        if counts[ident]!=1 or not values.get(ident):
            errors[ident]='missing_duplicate_or_empty_translation';values.pop(ident,None)
    return values,errors


def digit_groups(text):
    normalized=''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)
    return Counter(re.findall(r'\d+',normalized))


def glossary_text(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum() or unicodedata.category(c).startswith('M'))


def glossary_matches(target,terms):
    """Do not let a longer term satisfy a distinct shorter term inside it."""
    target=glossary_text(target);values={glossary_text(v) for v in terms};spans={}
    for value in values:
        spans[value]=[(i,i+len(value)) for i in range(len(target)-len(value)+1) if target.startswith(value,i)]
    return {value:[(x,y) for x,y in ranges if not any(a<=x and y<=b for longer,other in spans.items() if len(longer)>len(value) for a,b in other)]
        for value,ranges in spans.items()}


def glossary_present(target,terms):
    return all(bool(spans) for spans in glossary_matches(target,terms).values())


def translation_quality(source,target,lang,literal_numbers=True):
    if not target or not any(c.isalpha() for c in target):return 'unusable_translation'
    letters=[c.casefold() for c in target if c.isalpha()]
    if len(letters)>=20 and Counter(letters).most_common(1)[0][1]/len(letters)>.8:return 'degenerate_translation'
    if lang!='en' and re.sub(r'\W+','',source).casefold()==re.sub(r'\W+','',target).casefold() and len(source.split())>=3:
        return 'translation_echoed_english'
    if lang in SCRIPTS and len(source.split())>=3:
        a,b=SCRIPTS[lang]
        if not any(a<=ord(c)<=b for c in target if c.isalpha()):return 'missing_target_script'
    if literal_numbers and digit_groups(source)!=digit_groups(target):return 'literal_number_mismatch'
    return None


class TranslationClient:
    def __init__(self,key,cache,settings=None,transport=http_transport,sleep=time.sleep,pacer=None,glossary=None,code_mixed=False,literal_numbers=True,english_context=None):
        if not key or not key.strip() or key.startswith('<'):
            raise MediaError('missing_translation_key','A translation key is required for live execution.')
        self._key=key;self.cache=Path(cache);self.settings=settings or TranslationSettings();self.sleep=sleep
        self.pacer=pacer or RequestPacer(self.settings.requests_per_minute)
        self.transport=PacedTransport(transport,self.pacer)
        self.glossary=glossary or {};self.code_mixed=bool(code_mixed)
        self.literal_numbers=bool(literal_numbers)
        self.english_context=english_context or ""
        if not isinstance(self.english_context,str) or len(self.english_context)>50000:raise MediaError("invalid_translation_context","Use bounded English source context.")

    def translate(self,units,lang):
        languages([lang])
        if self.code_mixed and lang in {'en','ks-Deva','sd-Arab'}:raise MediaError('unsupported_code_mixed_target','Bodhan does not support code-mixed output for this target.')
        identity={'schema':1,'model':MODEL,'endpoint':ENDPOINT,'target':lang,
                  'units':[{k:u[k] for k in ['id','text']} for u in units]}
        if self.glossary:identity['glossary']=self.glossary
        if self.english_context:identity['english_context']=self.english_context
        if not self.literal_numbers:identity['literal_number_check']=False
        if self.code_mixed:identity['script']='codemix';identity['endpoint']='https://api.bodhan.ai/translate'
        context=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        self.cache.mkdir(parents=True,exist_ok=True)
        folder=self.cache/context
        folder.mkdir(exist_ok=True)
        try:
            with FileLock(str(folder/'document.lock'),timeout=self.settings.timeout_seconds*self.settings.attempts+300):
                results={};pending=[]
                for u in units:
                    p=folder/(u['id']+'.json')
                    if p.exists():
                        try:
                            item=json.loads(p.read_text())
                            if item['id']!=u['id'] or item['source_text']!=u['text'] or item['target_language']!=lang or translation_quality(u['text'],item['translation'],lang,literal_numbers=self.literal_numbers):raise ValueError
                        except (OSError,ValueError,KeyError,TypeError) as exc:
                            raise MediaError('invalid_translation_cache','Inspect the translation cache before deliberate resubmission.') from exc
                        results[u['id']]={**item,'cache_hit':True}
                    else:pending.append(u)
                if not pending:return {'units':results,'errors':{},'attempts':[],'cache_hits':len(results),'known_new_cost_inr':'0','conservative_new_cost_upper_bound_inr':'0'}
                request_identity={'pending_ids':[u['id'] for u in pending],'max_output_tokens':self.settings.max_output_tokens}
                request_digest=hashlib.sha256(json.dumps(request_identity,sort_keys=True).encode()).hexdigest()
                journal=folder/('request-'+request_digest+'.json')
                if journal.exists():
                    try:state=json.loads(journal.read_text())['state']
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        raise MediaError('invalid_translation_cache','Translation request journal is unreadable.') from exc
                    if state!='failed':
                        error=MediaError('previous_translation_attempt','Previous request was uncertain or invalid; inspect it before any paid repeat.')
                        error.previous_state=state
                        error.partial_result={'units':results,'errors':{u['id']:'previous_translation_attempt' for u in pending},'known_new_cost_inr':'0','conservative_new_cost_upper_bound_inr':'0'}
                        raise error
                translated,errors,attempts,usage=self._request(pending,lang,journal)
                for u in pending:
                    if u['id'] not in translated:continue
                    target=translated[u['id']]
                    problem=translation_quality(u['text'],target,lang,literal_numbers=self.literal_numbers)
                    if problem:errors[u['id']]=problem;continue
                    terms={source:targets[lang] for source,targets in self.glossary.items() if lang in targets and source.casefold() in u['text'].casefold()}
                    if not glossary_present(target,terms.values()):errors[u['id']]='glossary_term_mismatch';continue
                    item={'id':u['id'],'source_text':u['text'],'translation':target,'target_language':lang,'model':MODEL}
                    atomic_json(folder/(u['id']+'.json'),item)
                    results[u['id']]={**item,'cache_hit':False}
                known=str(Decimal(usage['completion_tokens'])*RATE) if usage and 'completion_tokens' in usage else None
                return {'units':results,'errors':errors,'attempts':attempts,'provider_usage':usage,'cache_hits':sum(x['cache_hit'] for x in results.values()),
                        'known_new_cost_inr':known,'conservative_new_cost_upper_bound_inr':str(Decimal(len(attempts)*self.settings.max_output_tokens)*RATE)}
        except LockTimeout as exc:
            raise MediaError('translation_cache_busy','Another translation owns this source document.') from exc

    def _request(self,pending,lang,journal):
        context_terms={source:targets[lang] for source,targets in self.glossary.items() if lang in targets}
        request_units=pending
        if context_terms:
            context={'id':'__glossary_context','text':'Terminology context; use these target terms consistently: '+json.dumps(context_terms,ensure_ascii=False)}
            request_units=[context]+pending
        if self.english_context:request_units=[{'id':'__source_context','text':self.english_context}]+request_units
        if self.code_mixed:
            endpoint='https://api.bodhan.ai/translate'
            body={'text':document(request_units),'target_language':lang,'script':'codemix','max_output_tokens':self.settings.max_output_tokens}
        else:
            endpoint=ENDPOINT
            body={'model':MODEL,'messages':[{'role':'user','content':f'Translate the following text into {LANGUAGES[lang]}:\n\n'+document(request_units)}],
                  'max_output_tokens':self.settings.max_output_tokens}
        request=Request(endpoint,{'Authorization':'Bearer '+self._key,'Content-Type':'application/json'},json.dumps(body,ensure_ascii=False).encode(),self.settings.timeout_seconds)
        attempts=[];usage=None
        def failure(code,message):
            error=MediaError(code,message);error.attempts=list(attempts)
            error.known_new_cost_inr=str(Decimal(usage['completion_tokens'])*RATE) if usage and 'completion_tokens' in usage else None
            error.conservative_new_cost_upper_bound_inr=str(Decimal(len(attempts)*self.settings.max_output_tokens)*RATE)
            return error
        for index in range(self.settings.attempts):
            atomic_json(journal,{'state':'in_flight','attempts':attempts,'next_attempt':index+1})
            started=time.monotonic()
            try:response=self.transport(request)
            except Exception as exc:
                attempts.append({'attempt':index+1,'status':'unknown','billing':'unknown'})
                atomic_json(journal,{'state':'unknown','attempts':attempts})
                raise failure('ambiguous_translation_call','No trustworthy provider response; automatic paid repeats blocked.') from exc
            attempts.append({'attempt':index+1,'http_status':response.status,'latency_seconds':round(time.monotonic()-started,3),'billing':'unconfirmed'})
            if response.status==200:
                try:
                    data=json.loads(response.body)
                    if not isinstance(data,dict) or 'error' in data:raise ValueError
                    raw_usage=data.get('usage')
                    usage={k:raw_usage[k] for k in ['prompt_tokens','completion_tokens','total_tokens'] if type(raw_usage.get(k)) is int and raw_usage[k]>=0} if isinstance(raw_usage,dict) else None
                    if self.code_mixed:
                        if data.get('truncated') is not False:raise MediaError('truncated_translation','Code-mixed output is truncated or lacks completion status.')
                        if data.get('target_language')!=lang or data.get('script')!='codemix':raise ValueError
                        content=data['translation']
                    else:
                        choice=data['choices'][0]
                        if choice['finish_reason']!='stop' or data.get('truncated') is True:
                            raise MediaError('truncated_translation','Translation did not finish; partial text is not accepted.')
                        content=choice['message']['content']
                    if not isinstance(content,str):raise ValueError
                    translated,errors=parse_document(content.replace(self._key,'[REDACTED]'),request_units)
                    translated.pop('__glossary_context',None);errors.pop('__glossary_context',None)
                    translated.pop('__source_context',None);errors.pop('__source_context',None)
                    raw_usage=data.get('usage')
                    usage={k:raw_usage[k] for k in ['prompt_tokens','completion_tokens','total_tokens'] if type(raw_usage.get(k)) is int and raw_usage[k]>=0} if isinstance(raw_usage,dict) else None
                except (ValueError,KeyError,TypeError,IndexError,MediaError) as exc:
                    atomic_json(journal,{'state':'invalid','attempts':attempts,'error_code':getattr(exc,'code','invalid_translation_response'),'usage':usage})
                    raise failure(getattr(exc,'code','invalid_translation_response'),'Unusable or incomplete translation response; no automatic paid repeat.') from exc
                atomic_json(journal,{'state':'complete','attempts':attempts,'usage':usage,'parse_errors':errors})
                return translated,errors,attempts,usage
            code={401:'invalid_translation_key',403:'translation_forbidden',404:'translation_endpoint_missing'}.get(response.status,'translation_http_error')
            atomic_json(journal,{'state':'failed','attempts':attempts,'error_code':code})
            if response.status!=429 and not 500<=response.status<600 or index+1==self.settings.attempts:
                raise failure(code,f'Translation failed with HTTP {response.status}; provider error body omitted.')
            self.sleep(min(2**index,8))


def translate_run(units,codes,out,client):
    languages(codes)
    out=Path(out)
    report={'status':'translating','model':MODEL,'languages':{},'dubbing_complete':False,
            'timing_note':'Source window bounds retained; final alignment is not established.',
            'limitations':['Native script and literal digits are checked; word-number meaning and translation accuracy are not proven by these checks.',
                          'Document context supports terminology consistency; explicit glossary enforcement remains pending.']}
    active=[u for u in units if u['status']=='ready_for_translation'];fatal=None
    for lang in codes:
        entry={'status':'translating','units':[]}
        if fatal:
            entry.update(status='failed',error={'code':'not_attempted','message':fatal})
        elif not active:
            entry.update(status='failed',error={'code':'no_usable_source','message':'Every source unit was skipped.'})
        else:
            try:
                result=client.translate(active,lang)
                entry.update({k:v for k,v in result.items() if k!='units'})
                for unit in units:
                    row={'id':unit['id'],'source_text':unit['text'],'source_window_envelope':unit['source_window_envelope']}
                    if unit['status']=='skipped':row.update(status='skipped',reason=unit.get('skip_reason'))
                    elif unit['id'] in result['units']:row.update(status='translated',**result['units'][unit['id']])
                    else:row.update(status='failed',reason=result['errors'].get(unit['id'],'missing_translation'))
                    entry['units'].append(row)
                entry['status']='translated' if all(u['status']=='translated' for u in entry['units']) else 'partial_failure'
            except MediaError as exc:
                entry.update(status='failed',error={'code':exc.code,'message':str(exc)},attempts=getattr(exc,'attempts',[]),known_new_cost_inr=getattr(exc,'known_new_cost_inr',None),
                             conservative_new_cost_upper_bound_inr=getattr(exc,'conservative_new_cost_upper_bound_inr','0'),previous_request_state=getattr(exc,'previous_state',None))
                if exc.code in {'invalid_translation_key','translation_forbidden','translation_endpoint_missing'}:fatal='Shared translation credential/endpoint failed; further requests not started.'
        report['languages'][lang]=entry
        atomic_json(out/f'translation.{lang}.json',entry)
        atomic_json(out/'translation_report.json',report)
    report['status']='translated' if all(x['status']=='translated' for x in report['languages'].values()) else 'partial_failure'
    report['known_new_cost_inr']=str(sum((Decimal(r.get('known_new_cost_inr') or '0') for r in report['languages'].values()),Decimal(0)))
    report['conservative_new_cost_upper_bound_inr']=str(sum((Decimal(r.get('conservative_new_cost_upper_bound_inr','0')) for r in report['languages'].values()),Decimal(0)))
    report['billing_note']='Known cost uses provider-reported output tokens. Upper bound assumes every attempted call billed the requested token allowance; actual billing unconfirmed.'
    atomic_json(out/'translation_report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Estimate labelled translation by default; hosted execution is explicit.')
    parser.add_argument('--units',type=Path,required=True)
    parser.add_argument('--langs',default='hi,ta,bn')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--attempts',type=int,default=1)
    parser.add_argument('--max-output-tokens',type=int,default=4096)
    parser.add_argument('--requests-per-minute',type=float,default=10)
    args=parser.parse_args(argv)
    try:
        rows=inspect_units(args.units);codes=languages(args.langs.split(','))
        settings=TranslationSettings(attempts=args.attempts,max_output_tokens=args.max_output_tokens,requests_per_minute=args.requests_per_minute)
        estimate={'units':len(rows),'languages':codes,'main_requests':len(codes),
                  'requested_output_token_cap_per_language':settings.max_output_tokens,
                  'first_attempt_token_cap_cost_inr':str(Decimal(len(codes)*settings.max_output_tokens)*RATE),
                  'all_attempts_token_cap_cost_inr':str(Decimal(len(codes)*settings.max_output_tokens*settings.attempts)*RATE)}
        if not args.execute:
            print(json.dumps({'status':'dry_run','hosted_calls':0,'estimate':estimate}));return 0
        if args.out is None:raise MediaError('missing_translation_output','Supply --out for live execution.')
        client=TranslationClient(load_key(args.env_file),args.out/'.translation_cache',settings)
        report=translate_run(rows,codes,args.out,client)
        print(json.dumps({'status':report['status'],'report':str(args.out/'translation_report.json')}))
        return int(report['status']!='translated')
    except (MediaError,OSError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code','filesystem_error'),
                          'message':str(exc) if isinstance(exc,MediaError) else 'Cannot access translation files.'}));return 1


if __name__=='__main__':raise SystemExit(main())
