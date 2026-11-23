"""Unicode-preserving subtitle layout with estimated phrase timing."""
import math
import re

from .media import MediaError


def timestamp(seconds,vtt=False):
    if not math.isfinite(seconds) or seconds<0:raise MediaError('invalid_subtitle_time','Subtitle time must be finite and nonnegative.')
    value=round(seconds*1000)
    hours,value=divmod(value,3600000);minutes,value=divmod(value,60000);whole,ms=divmod(value,1000)
    return f'{hours:02d}:{minutes:02d}:{whole:02d}{"." if vtt else ","}{ms:03d}'


def wrap_cues(text,width=42):
    words=text.split();lines=[];line=''
    for word in words:
        if line and len(line)+len(word)+1>width:lines.append(line);line=''
        while len(word)>width:
            if line:lines.append(line);line=''
            lines.append(word[:width]);word=word[width:]
        if word:line=(line+' '+word).strip()
    if line:lines.append(line)
    return ['\n'.join(lines[i:i+2]) for i in range(0,len(lines),2)]


def cues_for_intervals(text,intervals,unit_id):
    if not intervals:return []
    if not isinstance(text,str) or not text.strip():raise MediaError('empty_subtitle_text','Cannot caption empty text.')
    words=text.split();total=sum(b-a for a,b in intervals)
    if total<=0 or any(not 0<=a<b for a,b in intervals):raise MediaError('invalid_subtitle_intervals','Caption intervals must have positive durations.')
    result=[];cursor=0;elapsed=0
    # Assign consecutive words to each audible fragment; no cue crosses a protected gap.
    for n,(a,b) in enumerate(intervals):
        elapsed+=b-a
        cut=len(words) if n==len(intervals)-1 else max(cursor,min(len(words),round(len(words)*elapsed/total)))
        chunk=' '.join(words[cursor:cut]);cursor=cut
        if not chunk:continue
        pieces=wrap_cues(chunk);weights=[len(re.sub(r'\s','',p)) for p in pieces];denom=sum(weights);used=0
        for piece,weight in zip(pieces,weights):
            start=a+(b-a)*used/denom;used+=weight;end=a+(b-a)*used/denom
            if round(end*1000)<=round(start*1000):raise MediaError('subtitle_too_dense','Caption cannot have a positive millisecond duration.')
            result.append({'unit_id':unit_id,'start_seconds':start,'end_seconds':end,'text':piece,
                           'characters_per_second':weight/(end-start),'timing_resolution':'estimated_proportional_text_timing'})
    if re.sub(r'\s','',text)!=re.sub(r'\s','',''.join(c['text'] for c in result)):
        raise MediaError('subtitle_text_changed','Subtitle layout changed translated characters.')
    return result


def serialize(cues,vtt=False):
    lines=['WEBVTT',''] if vtt else []
    previous=0
    for index,cue in enumerate(cues,1):
        start,end=cue['start_seconds'],cue['end_seconds']
        if round(start*1000)<round(previous*1000) or round(end*1000)<=round(start*1000):raise MediaError('overlapping_subtitles','Subtitles must be ordered and non-overlapping.')
        previous=end
        # Prevent model-produced markup from being interpreted by subtitle renderers.
        text=cue['text'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        lines.extend([str(index),f'{timestamp(start,vtt)} --> {timestamp(end,vtt)}',text,''])
    return '\n'.join(lines)+'\n'
