"""Bound request starts and honor provider cooldowns within one process."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import threading
import time

from .media import MediaError


class RequestPacer:
    def __init__(self, requests_per_minute=10, clock=time.monotonic,
                 sleep=time.sleep, wall_clock=time.time):
        if type(requests_per_minute) not in {int,float} or not math.isfinite(requests_per_minute) or not 0<requests_per_minute<=120:
            raise MediaError('invalid_rate_limit','Use a positive finite request rate of at most 120 per minute.')
        self.interval=60/requests_per_minute
        self.clock,self.sleep,self.wall_clock=clock,sleep,wall_clock
        self.next_start=0
        self.cooldown_until=0
        self.lock=threading.Lock()
        self.events=[]

    def wait(self):
        while True:
            with self.lock:
                now=self.clock()
                delay=max(0,self.next_start-now,self.cooldown_until-now)
                if delay>300:
                    raise MediaError('rate_limit_wait_too_long','Provider cooldown exceeds the five-minute automatic wait budget; resume later.')
                if not delay:
                    self.next_start=now+self.interval
                    return
                self.events.append({'event':'request_wait','seconds':round(delay,3)})
            # Never hold the lock while waiting: in-flight responses must be
            # able to extend the cooldown, which is rechecked on wakeup.
            self.sleep(delay)

    def observe(self,response):
        if response.status!=429:
            return
        headers={k.lower():v for k,v in response.headers.items()}
        value=headers.get('retry-after')
        delay=60.0
        if value is not None:
            try:
                candidate=float(value)
                if math.isfinite(candidate):delay=max(0,candidate)
            except (ValueError,TypeError):
                try:
                    date=parsedate_to_datetime(str(value))
                    if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                    delay=max(0,date.timestamp()-self.wall_clock())
                except (ValueError,TypeError,OverflowError):
                    pass
        with self.lock:
            self.cooldown_until=max(self.cooldown_until,self.clock()+delay)
            self.events.append({'event':'provider_cooldown','seconds':round(delay,3)})


class PacedTransport:
    def __init__(self,transport,pacer):
        self.transport,self.pacer=transport,pacer

    def __call__(self,request):
        self.pacer.wait()
        response=self.transport(request)
        self.pacer.observe(response)
        return response
