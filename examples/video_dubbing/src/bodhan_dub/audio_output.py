"""Local PCM fitting, loudness measurement, pause-aware placement and video mux."""
import io
import json
import math
from pathlib import Path
import re
import subprocess
import wave

import numpy as np

from .media import MediaError
from .synthesis import atomic_bytes,inspect_wav
from .timing import fit_speed

RATE=24000


def command(args,timeout=180):
    try:result=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:raise MediaError('local_render_failed','Local media command could not finish.') from exc
    if result.returncode:raise MediaError('local_render_failed','Local media command failed: '+result.stderr[-600:])
    return result


def pcm_read(path):
    blob=Path(path).read_bytes();info=inspect_wav(blob,maximum_seconds=900)
    with wave.open(io.BytesIO(blob),'rb') as audio:pcm=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').copy()
    return pcm,info


def pcm_write(path,pcm):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as audio:
        audio.setparams((1,2,RATE,0,'NONE','not compressed'));audio.writeframes(np.asarray(pcm,dtype='<i2').tobytes())
    atomic_bytes(path,buffer.getvalue())


def trim_quiet_edges(pcm):
    active=np.flatnonzero(np.abs(pcm.astype(np.int32))>32)
    if not len(active):raise MediaError('silent_fitted_audio','Generated clip is effectively silent.')
    start=max(0,int(active[0])-.1*RATE);end=min(len(pcm),int(active[-1])+1+.1*RATE)
    start,end=int(start),int(end)
    return pcm[start:end],{'leading_seconds':start/RATE,'trailing_seconds':(len(pcm)-end)/RATE,
                           'policy':'Only near-digital-silence edges below about -60 dBFS, retaining 100 ms context. Internal pauses retained.'}


def measure_loudness(path,ffmpeg):
    result=command([ffmpeg,'-nostdin','-hide_banner','-i',str(path),'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'])
    chunks=re.findall(r'\{[^{}]+\}',result.stderr)
    try:
        data=json.loads(chunks[-1]);values={k:float(data[k]) for k in ['input_i','input_tp','input_lra','input_thresh','target_offset']}
        if not all(math.isfinite(v) for v in values.values()):raise ValueError
        return values
    except (ValueError,KeyError,IndexError):
        try:
            result=command([ffmpeg,'-nostdin','-hide_banner','-i',str(path),'-af','apad=pad_dur=2,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'])
            chunks=re.findall(r'\{[^{}]+\}',result.stderr)
            data=json.loads(chunks[-1]);values={k:float(data[k]) for k in ['input_i','input_tp','input_lra','input_thresh','target_offset']}
            if not all(math.isfinite(v) for v in values.values()):raise ValueError
            return values
        except (ValueError,KeyError,IndexError) as exc:
            raise MediaError('invalid_loudness_measurement','Cannot obtain finite speech loudness.') from exc


def process_clip(source,folder,available,ffmpeg):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    pcm,original=pcm_read(source);trimmed,trim=trim_quiet_edges(pcm)
    fit=fit_speed(len(trimmed)/RATE,available)
    if fit['status']!='fits':return {**fit,'status':'skipped','reason':'Generated speech cannot fit without exceeding 1.2x speed. Translation/condensation recovery deferred.',
        'original_audio':original,'trim':trim},None
    pcm_write(folder/'trimmed.wav',trimmed)
    speed=fit['speed'];tempo=folder/'tempo.wav'
    for attempt in range(2):
        command([ffmpeg,'-nostdin','-v','error','-y','-i',str(folder/'trimmed.wav'),'-af',f'atempo={speed:.9f}',
                 '-ar','24000','-ac','1','-c:a','pcm_s16le',str(tempo)])
        fitted,_=pcm_read(tempo)
        if len(fitted)<=round(available*RATE):break
        speed=min(1.2,speed*1.006)
    if len(fitted)>round(available*RATE):
        return {'status':'skipped','reason':'Measured tempo output still exceeds the slot; refusing speech truncation.',
                'original_audio':original,'trim':trim,'speed':speed},None
    measured=measure_loudness(tempo,ffmpeg)
    output=folder/'normalized.wav'
    filt=('loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:print_format=json'
          f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
          f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}")
    command([ffmpeg,'-nostdin','-v','error','-y','-i',str(tempo),'-af',filt,'-ar','24000','-ac','1','-c:a','pcm_s16le',str(output)])
    final,info=pcm_read(output);loudness=measure_loudness(output,ffmpeg)
    if len(final)>round(available*RATE):raise MediaError('normalized_duration_overflow','Loudness output unexpectedly exceeds its playback slots.')
    return {'status':'fitted','speed':speed,'original_audio':original,'trim':trim,'fitted_audio':info,
            'loudness_lufs':loudness['input_i'],'loudness_target_met':abs(loudness['input_i']+16)<=1,
            'true_peak_dbfs':loudness['input_tp'],'path':str(output)},final


def place_pcm(pcm,slots,break_samples=()):
    """Place whole generated pieces. Low energy is never a word-boundary proof."""
    ends=list(break_samples)+[len(pcm)]
    if not len(pcm) or any(type(x) is not int for x in ends) or ends!=sorted(set(ends)) or ends[0]<=0 or ends[-1]!=len(pcm):
        raise MediaError('invalid_phrase_boundaries','Expected ordered sample boundaries from independently generated speech pieces.')
    capacities=[round(s['end_seconds']*RATE)-round(s['start_seconds']*RATE) for s in slots]
    if any(c<=0 for c in capacities) or len(pcm)>sum(capacities):raise MediaError('audio_does_not_fit','Fitted audio exceeds available playback slots.')
    # Dynamic programming can leave a short slot empty, but never split a piece.
    states={0:[]};points=[0]+ends
    for slot,capacity in zip(slots,capacities):
        following={}
        for start,path in states.items():
            for end in range(start,len(points)):
                length=points[end]-points[start]
                if length>capacity:break
                candidate=path+[(slot,points[start],points[end])]
                if end not in following:following[end]=candidate
        states=following
    if len(ends) not in states:
        raise MediaError('phrase_boundary_required','A source pause would cut inside generated speech. Regenerate complete text pieces; do not cut the waveform.')
    placements=[]
    for slot,a,b in states[len(ends)]:
        if a==b:continue
        start=round(slot['start_seconds']*RATE)
        placements.append({'start_sample':start,'end_sample':start+b-a,'audio_start_sample':a,'audio_end_sample':b,
            'start_seconds':start/RATE,'end_seconds':(start+b-a)/RATE})
    return placements,[]


def background_track(source,out,duration,gaps,ffmpeg):
    out=Path(out)
    command([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-ar','24000','-ac','1','-af','apad',
             '-t',str(duration),'-c:a','pcm_s16le',str(out)])
    original,_=pcm_read(out);frames=round(duration*RATE)
    result=np.zeros(frames,dtype=np.int16)
    for gap in gaps:
        a=max(0,round(gap['start_seconds']*RATE));b=min(frames,len(original),round(gap['end_seconds']*RATE))
        chunk=original[a:b].astype(np.float64);fade=min(120,len(chunk)//2)
        if fade:
            chunk[:fade]*=np.linspace(0,1,fade);chunk[-fade:]*=np.linspace(1,0,fade)
        result[a:b]=chunk.astype(np.int16)
    return result


def mux_video(source,audio,out,info,ffmpeg,ffprobe,timeout=300):
    command([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-i',str(audio),'-map',f'0:{info.video_stream_index}',
             '-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','128k','-t',str(info.duration_seconds),
             '-metadata','title=AI-dubbed using Bodhan','-metadata','comment=AI-generated dubbed speech; see report.json for quality limitations.',
             '-movflags','+faststart',str(out)],timeout)
    result=command([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(out)])
    data=json.loads(result.stdout);duration=float(data['format']['duration'])
    if not any(s.get('codec_type')=='audio' for s in data['streams']):raise MediaError('mux_missing_audio','Output video has no audio stream.')
    return {'duration_seconds':duration,'duration_error_seconds':duration-info.duration_seconds,
            'duration_target_met':abs(duration-info.duration_seconds)<=.1,'video_codec_copied':True,'ai_dubbed_label':'MP4 title and comment metadata'}
