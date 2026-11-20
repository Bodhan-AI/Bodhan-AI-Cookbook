"""Validated hosted Bodhan adapters and bounded text-only LLM judgments."""

import json
import logging
import math
import re
import time
import wave
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from hashlib import sha256
from http.client import HTTPException
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .budget import asr_cost, speak_cost, translation_cost

LOG = logging.getLogger(__name__)
BODHAN_URL = 'https://api.bodhan.ai'
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
CACHE_VERSION = 'bodhan-dub-provider-1'
LOOPBACK = {'localhost', '127.0.0.1', '::1'}
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}
FLASH_LITE = 'google/gemini-2.5-flash-lite'


class ProviderError(RuntimeError):
    def __init__(self, message, *, status=None, retryable=False, ambiguous=False, retry_after=None,
                 request_id=None, credit_exhausted=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after
        self.request_id = request_id
        self.credit_exhausted = credit_exhausted


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _json(response):
    try:
        result = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderError('Provider returned invalid JSON') from error
    if not isinstance(result, dict):
        raise ProviderError('Expected a JSON object from the provider')
    return result


def _safe_identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[\w.-]{1,120}', value, flags=re.ASCII) else None


def _endpoint(base, *, bodhan=False):
    parts = urlsplit(base.rstrip('/'))
    if (parts.username or parts.password or parts.query or parts.fragment or not parts.hostname
            or (parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in LOOPBACK))):
        raise ValueError('API base URLs require HTTPS or an HTTP loopback test fixture')
    if bodhan:
        if parts.hostname not in LOOPBACK | {'api.bodhan.ai'} or parts.path not in {'', '/v1'}:
            raise ValueError('Bodhan base URL must be the Bodhan API or a loopback test fixture')
        if not parts.path:
            return f'{base.rstrip("/")}/v1'
    return base.rstrip('/')


def _retry_after(value):
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        try:
            result = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, OverflowError):
            return None
    return max(0, result) if math.isfinite(result) else None


def _http_error(error):
    try:
        body = _json(error.read(32 * 1024))
    except (ProviderError, OSError, HTTPException):
        body = {}
    finally:
        error.close()
    provider_error = body.get('error')
    if not isinstance(provider_error, dict):
        provider_error = {'message': provider_error or body.get('detail')}
    code = provider_error.get('code')
    description = f'{code} {provider_error.get("message", "")}'.lower()
    exhausted = error.code in {402, 429} and (
        error.code == 402 or any(term in description for term in ('credit', 'budget', 'billing quota', 'insufficient_quota'))
    )
    detail = f'HTTP {error.code}'
    if _safe_identifier(code):
        detail += f' ({code})'
    if exhausted:
        detail += ' provider credit exhausted'
    headers = error.headers or {}
    return ProviderError(
        detail, status=error.code, retryable=error.code in TRANSIENT_HTTP and not exhausted,
        ambiguous=error.code == 408 or error.code >= 500,
        retry_after=_retry_after(headers.get('Retry-After')),
        request_id=_safe_identifier(headers.get('x-request-id') or provider_error.get('request_id')),
        credit_exhausted=exhausted,
    )


def _post(url, key, payload, content_type, timeout):
    request = Request(url, data=payload, method='POST', headers={
        'Authorization': f'Bearer {key}', 'Content-Type': content_type,
        'Accept': '*/*', 'User-Agent': 'bodhan-dub-cookbook/1',
    })
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ProviderError('Provider response exceeded the download limit', ambiguous=True)
            return body, _safe_identifier(response.headers.get('x-request-id'))
    except HTTPError as error:
        raise _http_error(error) from error
    except (URLError, TimeoutError, OSError, HTTPException) as error:
        raise ProviderError('Provider network response was incomplete', retryable=True, ambiguous=True) from error


def _wav_duration(audio):
    try:
        with wave.open(BytesIO(audio)) as source:
            return source.getnframes() / source.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as error:
        raise ProviderError('Expected PCM WAV audio') from error


def _multipart(audio, language):
    boundary = uuid4().hex
    fields = {'model': 'indic-transcribe', 'language': language}
    body = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            for key, value in fields.items()]
    body.extend([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
         'Content-Type: audio/wav\r\n\r\n').encode(),
        audio, f'\r\n--{boundary}--\r\n'.encode(),
    ])
    return b''.join(body), f'multipart/form-data; boundary={boundary}'


def _transcript(response, seconds):
    body = _json(response)
    if not isinstance(body.get('text'), str) or 'error' in body:
        raise ProviderError('Transcription response is missing text')
    result = {'text': body['text'].strip()}
    segments = body.get('segments')
    if segments:
        valid = isinstance(segments, list)
        output = []
        for segment in segments if valid else []:
            if not isinstance(segment, dict):
                valid = False
                break
            start, end, text = segment.get('start'), segment.get('end'), segment.get('text')
            if (type(start) not in {int, float} or type(end) not in {int, float}
                    or not 0 <= start < min(end, seconds) or not end <= seconds + 0.05
                    or not isinstance(text, str) or not text.strip()
                    or (output and start < output[-1]['start'])):
                valid = False
                break
            record = {'start': float(start), 'end': min(float(end), seconds), 'text': text.strip()}
            speaker = segment.get('speaker')
            if isinstance(speaker, str) and speaker.strip():
                record['speaker'] = speaker.strip()
            output.append(record)
        if valid:
            result['segments'] = output
        else:
            result['timing_warning'] = 'Provider segment timestamps were invalid. Using VAD/window bounds.'
    return result


def _chat_text(response):
    body = _json(response)
    try:
        choice = body['choices'][0]
        message = choice['message']['content']
    except (KeyError, IndexError, TypeError) as error:
        raise ProviderError('Chat response is missing message content') from error
    if not isinstance(message, str) or not message.strip() or 'error' in body:
        raise ProviderError('Chat response returned empty or invalid text')
    if body.get('truncated') or choice.get('finish_reason') not in (None, 'stop'):
        raise ProviderError('Chat completion ended before a complete response')
    return message.strip()


def _judge_result(response):
    text = _chat_text(response)
    if text.startswith('```') and text.endswith('```'):
        text = text.partition('\n')[2][:-3].strip()
    return _json(text)


def _usage(response):
    try:
        usage = _json(response).get('usage', {})
    except ProviderError:
        return {}
    return usage if isinstance(usage, dict) else {}


def _translation_charge(response):
    tokens = _usage(response).get('completion_tokens')
    return translation_cost(tokens) if type(tokens) is int and tokens >= 0 else None, None


def _audio_response(response):
    # Bodhan has returned WAV bytes with a different Content-Type.
    if response[:4] == b'RIFF' and response[8:12] == b'WAVE':
        try:
            with wave.open(BytesIO(response)) as source:
                frame_count = source.getnframes()
                frame_size = source.getnchannels() * source.getsampwidth()
                if frame_count > 0 and len(source.readframes(frame_count)) == frame_count * frame_size:
                    return response
        except (wave.Error, EOFError) as error:
            raise ProviderError('Speech response has an invalid PCM WAV container') from error
        raise ProviderError('Speech response has incomplete PCM WAV samples')
    if len(response) > 44 and (response.startswith((b'ID3', b'OggS', b'fLaC'))
                               or (response[0] == 0xff and (response[1] & 0xe0) == 0xe0)):
        return response
    raise ProviderError('Speech response is missing a supported audio container')


class Provider:
    def __init__(self, budget, *, transcribe_key, translate_key, speak_key, bodhan_url=BODHAN_URL,
                 llm_base_url=None, llm_model=None, llm_api_key=None, concurrency=3, endpoint_concurrency=1,
                 attempts=2, timeout=120, llm_in_usd_per_mtok=None, llm_out_usd_per_mtok=None,
                 llm_fx_inr_per_usd=110, llm_cap_inr=0.25):
        if not 1 <= attempts <= 3 or min(concurrency, endpoint_concurrency) < 1 or timeout <= 0:
            raise ValueError('Provider concurrency, attempts and timeout must be positive and bounded')
        self.budget = budget
        self.transcribe_key = transcribe_key
        self.translate_key = translate_key
        self.speak_key = speak_key
        self.bodhan_url = _endpoint(bodhan_url, bodhan=True)
        self.llm_url = _endpoint(llm_base_url) if llm_base_url else None
        self.llm_model = llm_model
        self.llm_api_key = llm_api_key
        self.concurrency = concurrency
        self.endpoint_concurrency = endpoint_concurrency
        self.attempts = attempts
        self.timeout = timeout
        self.openrouter = self.llm_url is not None and urlsplit(self.llm_url).hostname == 'openrouter.ai'
        self.llm_source = 'openrouter' if self.openrouter else 'llm'
        self._exhausted_accounts = set()
        self._cooldowns = {}
        defaults = (0.10, 0.40) if self.openrouter and llm_model == FLASH_LITE else (2.0, 8.0)
        self.llm_in = defaults[0] if llm_in_usd_per_mtok is None else llm_in_usd_per_mtok
        self.llm_out = defaults[1] if llm_out_usd_per_mtok is None else llm_out_usd_per_mtok
        self.llm_fx = llm_fx_inr_per_usd
        self.llm_cap = llm_cap_inr
        if any(not math.isfinite(value) or value < 0 for value in (self.llm_in, self.llm_out, self.llm_fx, self.llm_cap)):
            raise ValueError('LLM rates and spending allowance must be finite and nonnegative')

    @property
    def has_judge(self):
        return bool(self.llm_url and self.llm_model and self.llm_api_key)

    def _call(self, *, endpoint, url, api_key, payload, content_type, cache_key, stage, language, estimate,
              parser, pool=None, charge=None, category_cap=None):
        if not api_key:
            raise ProviderError(f'Missing API key for {endpoint}')
        try:
            with self.budget.slot(f'cache-{cache_key[:2]}', timeout=self.timeout * 4):
                cached = self.budget.cached(cache_key)
                if cached is not None:
                    self.budget.cache_hit(stage, language, cache_key)
                    return parser(cached)
                return self._dispatch(endpoint, url, api_key, payload, content_type, cache_key, stage, language,
                                      estimate, parser, pool, charge, category_cap)
        except TimeoutError as error:
            raise ProviderError(str(error)) from error

    @contextmanager
    def _endpoint_slot(self, endpoint):
        with self.budget.slot(f'endpoint-{endpoint}', self.endpoint_concurrency, timeout=self.timeout * 4):
            remaining = self._cooldowns.get(endpoint, 0) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            yield

    def _dispatch(self, endpoint, url, api_key, payload, content_type, cache_key, stage, language, estimate, parser,
                  pool, charge, category_cap):
        for attempt in range(1, self.attempts + 1):
            with (self._endpoint_slot(endpoint),
                  self.budget.slot('global', self.concurrency, timeout=self.timeout * 4)):
                account = 'llm' if endpoint == 'llm' else 'bodhan'
                if account in self._exhausted_accounts:
                    raise ProviderError(f'{account} provider credit exhausted', credit_exhausted=True)
                call_id = self.budget.reserve(estimate, stage=stage, language=language, key=cache_key, attempt=attempt,
                                              pool=pool, category_cap_inr=category_cap)
                started = time.monotonic()
                try:
                    raw, request_id = _post(url, api_key, payload, content_type, self.timeout)
                except ProviderError as error:
                    if error.credit_exhausted:
                        self._exhausted_accounts.add(account)
                    self.budget.finish(call_id, 'uncertain' if error.ambiguous else 'rejected',
                                       elapsed=time.monotonic() - started, detail=str(error), request_id=error.request_id)
                    wait = max(0.25 * 2 ** (attempt - 1), error.retry_after or 0)
                    if error.status == 429 and error.retryable:
                        wait = max(wait, 2.0 * attempt)
                        deadline = time.monotonic() + min(wait, 60)
                        self._cooldowns[endpoint] = max(self._cooldowns.get(endpoint, 0), deadline)
                    if not error.retryable or attempt == self.attempts or wait > 60:
                        raise
                    if error.ambiguous:
                        # The previous attempt may already have used this baseline slot.
                        pool = None
                    LOG.warning('%s %s attempt %s failed: %s', stage, language, attempt, error)
                else:
                    cost, usd = charge(raw) if charge else (None, None)
                    elapsed = time.monotonic() - started
                    status = 'estimated' if cost is None else 'measured'
                    try:
                        parsed = parser(raw)
                        if cost is not None and cost > estimate + 1e-9:
                            raise ProviderError('Provider-reported usage exceeded its pre-dispatch bound')
                    except ProviderError as error:
                        self.budget.finish(call_id, status, elapsed=elapsed, request_id=request_id,
                                           detail=str(error), cost_inr=cost, reported_usd=usd)
                        raise
                    self.budget.finish(call_id, status, elapsed=elapsed, request_id=request_id, cost_inr=cost,
                                       reported_usd=usd, response=raw)
                    return parsed
            time.sleep(wait)
        raise RuntimeError('Attempt loop exited unexpectedly')

    def _key(self, endpoint, params):
        return sha256(_json_bytes([CACHE_VERSION, endpoint, params])).hexdigest()

    def _asr_key_and_duration(self, audio, seconds, language):
        measured = _wav_duration(audio)
        if not 0 < measured <= 30 or not math.isfinite(seconds) or abs(measured - seconds) > 0.05:
            raise ProviderError('Transcription expects up to 30 seconds of WAV audio with valid duration')
        return self._key(self.bodhan_url, ['indic-transcribe', language, sha256(audio).hexdigest()]), measured

    def uncached_asr_cost(self, audio, seconds, language, *, stage='qa'):
        """Forecast a call without logging a cache hit or dispatching transcription."""
        key, measured = self._asr_key_and_duration(audio, seconds, language)
        return 0 if self.budget.cached(key) is not None else asr_cost(measured)

    def transcribe(self, audio, seconds, language, *, pool=None, stage='asr'):
        key, measured = self._asr_key_and_duration(audio, seconds, language)
        payload, content_type = _multipart(audio, language)
        return self._call(endpoint='asr', url=f'{self.bodhan_url}/audio/transcriptions', api_key=self.transcribe_key,
                          payload=payload, content_type=content_type, cache_key=key, stage=stage, language=language,
                          estimate=asr_cost(measured), parser=lambda raw: _transcript(raw, measured), pool=pool)

    def translate(self, text, language, *, max_tokens=None, pool=None, stage='translate'):
        if language == 'en':
            return text
        if not text.strip():
            raise ProviderError('Translation expects nonempty source text')
        max_tokens = max_tokens if max_tokens is not None else min(512, max(64, 2 * len(text)))
        if type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
            raise ValueError('Translation output cap must be between 1 and 2048 tokens')
        data = {'model': 'indic-translate', 'messages': [{'role': 'user', 'content': text}],
                'target_language_code': language, 'target_script': 'native', 'max_output_tokens': max_tokens}
        return self._call(endpoint='translate', url=f'{self.bodhan_url}/chat/completions', api_key=self.translate_key,
                          payload=_json_bytes(data), content_type='application/json', cache_key=self._key(self.bodhan_url, data),
                          stage=stage, language=language, estimate=translation_cost(max_tokens), parser=_chat_text,
                          pool=pool, charge=_translation_charge)

    def _speech_request(self, text, language, voice):
        return {'model': 'indic-speak', 'input': text, 'voice': voice, 'response_format': 'wav',
                'instructions': json.dumps({'lang': language})}

    def cached_speak(self, text, language, voice):
        data = self._speech_request(text, language, voice)
        return self.budget.cached(self._key(self.bodhan_url, data))

    def uncached_speak_cost(self, text, language, voice):
        return 0 if self.cached_speak(text, language, voice) is not None else speak_cost(text)

    def speak(self, text, language, voice, *, pool=None, stage='speak'):
        if not text.strip() or not voice:
            raise ProviderError('Synthesis expects nonempty text and a fixed voice')
        data = self._speech_request(text, language, voice)
        return self._call(endpoint='speak', url=f'{self.bodhan_url}/audio/speech', api_key=self.speak_key,
                          payload=_json_bytes(data), content_type='application/json', cache_key=self._key(self.bodhan_url, data),
                          stage=stage, language=language, estimate=speak_cost(text), parser=_audio_response, pool=pool)

    def judge(self, system, prompt, *, max_tokens=256, pool=None, stage='llm'):
        if not self.has_judge:
            return None
        # UTF-8 bytes plus a conservative chat-template allowance bound text token estimates.
        input_limit = len(system.encode()) + len(prompt.encode()) + 128
        if input_limit > 6500 or type(max_tokens) is not int or not 1 <= max_tokens <= 512:
            raise ProviderError('Text exceeds the bounded LLM prompt or output allowance')
        data = {'model': self.llm_model, 'messages': [{'role': 'system', 'content': system},
                                                     {'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens, 'temperature': 0}
        if self.openrouter:
            data['provider'] = {'max_price': {'prompt': self.llm_in, 'completion': self.llm_out}, 'require_parameters': True}
        estimate = (input_limit * self.llm_in + max_tokens * self.llm_out) * self.llm_fx / 1_000_000
        return self._call(endpoint='llm', url=f'{self.llm_url}/chat/completions', api_key=self.llm_api_key,
                          payload=_json_bytes(data), content_type='application/json', cache_key=self._key(self.llm_url, data),
                          stage=stage, language=self.llm_source, estimate=estimate, parser=_judge_result,
                          pool=pool, charge=self._llm_charge, category_cap=self.llm_cap)

    def _llm_charge(self, response):
        usage = _usage(response)
        usd = usage.get('cost') if self.openrouter else None
        if type(usd) in {float, int} and math.isfinite(usd) and usd >= 0:
            return usd * self.llm_fx, usd
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(prompt) is int and prompt >= 0 and type(completion) is int and completion >= 0:
            return (prompt * self.llm_in + completion * self.llm_out) * self.llm_fx / 1_000_000, None
        return None, None
