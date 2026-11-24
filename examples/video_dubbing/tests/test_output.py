from copy import deepcopy
import json
from pathlib import Path
import shutil
import wave

import numpy as np
import pytest

from bodhan_dub.audio_output import (RATE,command,pcm_read,pcm_write,place_pcm,process_clip,trim_quiet_edges)
from bodhan_dub.media import MediaError
from bodhan_dub.render import render_saved,validate_languages
from bodhan_dub.subtitles import cues_for_intervals,serialize,timestamp,wrap_cues
from bodhan_dub.synthesis import inspect_wav
from bodhan_dub.timing import fit_speed,source_timeline,subtract_gaps


def detector(duration=30):
    return {'sample_rate':16000,'total_samples':round(duration*16000),
        'speech_regions':[{'start_sample':16000,'end_sample':10*16000},{'start_sample':12*16000,'end_sample':20*16000}],
        'non_speech_regions':[{'start_sample':0,'end_sample':16000},{'start_sample':10*16000,'end_sample':12*16000},
            {'start_sample':20*16000,'end_sample':round(duration*16000)}]}


def unit_data():
    return ([{'id':'unit-0001','text':'first second third fourth','source_spans':[{'window_id':'w1','start':0,'end':4}],
            'source_window_envelope':{'start_seconds':0,'end_seconds':20}}],
        [{'id':'w1','tokens':['first','second','third','fourth'],'start_seconds':0,'end_seconds':20}])


def test_estimated_timing_uses_source_evidence_and_protects_gaps():
    units,windows=unit_data();r=source_timeline(units,windows,detector())
    assert r['units'][0]['start_seconds']==1 and r['units'][0]['end_seconds']==20
    assert r['units'][0]['available_seconds']==17
    assert r['units'][0]['speech_slots']==[{'start_seconds':1,'end_seconds':10},{'start_seconds':12,'end_seconds':20}]
    assert not r['units'][0]['sync_within_300ms_verified']


def test_overlapping_provenance_estimates_resolve_without_overlapping_slots():
    units,windows=unit_data();a=deepcopy(units[0]);b=deepcopy(a);a['source_spans'][0]['end']=3
    b['id']='unit-0002';b['source_spans'][0]['start']=2
    r=source_timeline([a,b],windows,detector())['units']
    assert r[0]['end_seconds']==r[1]['start_seconds']


def test_next_window_can_estimate_an_earlier_start_at_a_shared_boundary():
    units, windows = unit_data()
    windows.append({'id': 'w2', 'tokens': ['next', 'thought'], 'start_seconds': 16, 'end_seconds': 21})
    units[0]['source_spans'][0]['start'] = 3
    next_unit = deepcopy(units[0])
    next_unit.update(id='unit-0002', text='next thought',
                     source_spans=[{'window_id': 'w2', 'start': 0, 'end': 2}])
    windows[0]['end_seconds'] = 19
    windows[0]['tokens'] = ['longword', 'longword', 'longword', 'end']
    first, second = source_timeline([units[0], next_unit], windows, detector())['units']
    assert 16 < first['start_seconds'] < first['end_seconds'] == second['start_seconds'] < second['end_seconds']


def test_reversed_or_invalid_source_references_rejected():
    units,windows=unit_data();units[0]['source_spans'][0]['end']=100
    with pytest.raises(MediaError):source_timeline(units,windows,detector())


def test_gap_subtraction_and_speed_limits():
    assert subtract_gaps(0,10,[(2,3),(5,8)])==[(0,2),(3,5),(8,10)]
    assert fit_speed(9,10)['speed']==1
    assert fit_speed(12,10)['speed']==1.2
    assert fit_speed(12.1,10)['status']=='cannot_fit'
    with pytest.raises(MediaError):fit_speed(float('nan'),10)


@pytest.mark.parametrize('text',['தமிழ் உரை '*30,'हिंदी में एक वाक्य '*20,'বাংলা বাক্য '*30,'X'*100])
def test_unicode_caption_layout_preserves_characters_and_stays_in_slots(text):
    cues=cues_for_intervals(text,[(1,9),(12,22)],'unit-1')
    assert ''.join(text.split())==''.join(''.join(c['text'].split()) for c in cues)
    assert all(c['end_seconds']<=9.000001 or c['start_seconds']>=12 for c in cues)
    assert all(len(line)<=42 for c in cues for line in c['text'].splitlines())
    assert all(len(c['text'].splitlines())<=2 for c in cues)
    assert 'WEBVTT' in serialize(cues,True)


def test_subtitle_format_and_overlapping_cues():
    assert timestamp(3661.234)=='01:01:01,234'
    assert timestamp(59.9996)=='00:01:00,000'
    with pytest.raises(MediaError):serialize([{'start_seconds':2,'end_seconds':3,'text':'a'},{'start_seconds':1,'end_seconds':4,'text':'b'}])
    assert '&lt;x&gt;' in serialize([{'start_seconds':0,'end_seconds':1,'text':'<x>'}])


def test_pcm_placement_preserves_each_sample_and_does_not_fill_source_gap():
    samples=np.arange(24000*3,dtype=np.int16);slots=[{'start_seconds':1,'end_seconds':3},{'start_seconds':6,'end_seconds':8}]
    with pytest.raises(MediaError,match='source pause'):place_pcm(samples,slots)
    placed,_=place_pcm(samples,slots,[36000])
    joined=np.concatenate([samples[p['audio_start_sample']:p['audio_end_sample']] for p in placed])
    assert np.array_equal(samples,joined)
    assert all(p['end_seconds']<=3 or p['start_seconds']>=6 for p in placed)
    with pytest.raises(MediaError):place_pcm(np.ones(5*RATE,dtype=np.int16),slots)


def test_quiet_edge_trim_retains_context_and_internal_pause():
    samples=np.concatenate([np.zeros(RATE),np.full(RATE,1000),np.zeros(RATE),np.full(RATE,1000),np.zeros(RATE)]).astype(np.int16)
    trimmed,meta=trim_quiet_edges(samples)
    assert len(trimmed)==round(3.2*RATE) and meta['leading_seconds']==.9
    assert np.sum(trimmed==0)>=RATE


def test_production_language_validation():
    validate_languages(['ta','hi','gu'])
    with pytest.raises(MediaError):validate_languages(['ta','ta'])
    with pytest.raises(MediaError):validate_languages(['unsupported'])


def test_real_tempo_and_loudness_and_unfittable_rejection(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    samples=(np.sin(np.arange(RATE*2)*2*np.pi*220/RATE)*2000).astype(np.int16)
    source=tmp_path/'source.wav';pcm_write(source,samples)
    r,fitted=process_clip(source,tmp_path/'fit',1.8,ffmpeg)
    assert r['status']=='fitted' and 1<=r['speed']<=1.2 and len(fitted)/RATE<=1.8
    assert r['loudness_target_met']
    r,fitted=process_clip(source,tmp_path/'skip',1,ffmpeg)
    assert r['status']=='skipped' and fitted is None


def make_saved_run(tmp_path,ffmpeg):
    source=tmp_path/'video.mp4'
    command([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10:d=30',
             '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=30','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-t','30',str(source)])
    detect=tmp_path/'detect';run=detect/'saved';run.mkdir(parents=True)
    with wave.open(str(detect/'source_audio.wav'),'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes((np.sin(np.arange(30*16000)*2*np.pi*440/16000)*1000).astype('<i2').tobytes())
    units,windows=unit_data()
    (detect/'speech_regions.json').write_text(json.dumps(detector()))
    (run/'speech_units.en.json').write_text(json.dumps({'units':units}))
    (run/'corrections.final.json').write_text(json.dumps({'source_windows':windows}))
    (run/'translations').mkdir();(run/'synthesis/hi').mkdir(parents=True)
    text='यह एक परीक्षण है।';pcm_write(run/'synthesis/hi/unit-0001.wav',(np.sin(np.arange(RATE*2)*2*np.pi*330/RATE)*1000).astype(np.int16))
    row={'id':'unit-0001','status':'synthesized','text':text,'result':{'audio':inspect_wav((run/'synthesis/hi/unit-0001.wav').read_bytes())}}
    (run/'synthesis/synthesis_report.json').write_text(json.dumps({'languages':{'hi':{'units':[row]}}}))
    (run/'translations/translation.hi.json').write_text(json.dumps({'units':[{'id':'unit-0001','status':'translated','translation':text}]}))
    return source,run


def test_30_second_offline_output_smoke_and_language_failure_isolation(tmp_path,monkeypatch):
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:pytest.skip('media tools required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('Offline rendering made an API call'))
    source,run=make_saved_run(tmp_path,ffmpeg)
    report=render_saved(source,run,tmp_path/'out',['hi','ta'],ffmpeg=ffmpeg,ffprobe=ffprobe,allow_short=True)
    assert report['status']=='partial_failure' and report['hosted_calls_this_stage']==0
    assert report['languages']['hi']['status']=='completed_with_warnings'
    assert report['languages']['hi']['video']['duration_target_met']
    assert report['languages']['ta']['status']=='failed'
    assert (tmp_path/'out/hi/video.hi.mp4').exists() and (tmp_path/'out/hi/subtitles.hi.srt').exists()
    assert (tmp_path/'out/transcript.en.json').exists()
    audio,_=pcm_read(tmp_path/'out/hi/audio.hi.wav');speech,_=pcm_read(tmp_path/'out/hi/speech.hi.wav')
    assert np.array_equal(audio,speech)
    # The source fixture contains audible sound even where its VAD says gap.
    assert not np.any(audio[10*RATE:12*RATE])
    assert report['background_policy']['original_audio_mixed'] is False
    assert not report['dubbing_complete']
    with pytest.raises(MediaError):render_saved(source,run,tmp_path/'out',['hi'],allow_short=True)


def test_full_length_pcm_track_can_exceed_per_synthesis_clip_limit(tmp_path):
    path=tmp_path/'full.wav'
    pcm_write(path,np.full(181*RATE,100,dtype=np.int16))
    data,info=pcm_read(path)
    assert len(data)==181*RATE and info['duration_seconds']==181


def test_cross_window_boundary_snaps_to_observed_long_pause():
    windows=[{'id':'left','tokens':['a','b'],'start_seconds':0,'end_seconds':15},
             {'id':'right','tokens':['c','d'],'start_seconds':13,'end_seconds':30}]
    units=[{'id':'one','text':'a b','source_spans':[{'window_id':'left','start':0,'end':2}],
            'source_window_envelope':{'start_seconds':0,'end_seconds':15}},
           {'id':'two','text':'c d','source_spans':[{'window_id':'right','start':0,'end':2}],
            'source_window_envelope':{'start_seconds':13,'end_seconds':30}}]
    data={'sample_rate':16000,'total_samples':30*16000,
          'speech_regions':[{'start_sample':0,'end_sample':14*16000},{'start_sample':20*16000,'end_sample':30*16000}],
          'non_speech_regions':[{'start_sample':14*16000,'end_sample':20*16000}]}
    rows=source_timeline(units,windows,data)['units']
    assert rows[0]['end_seconds']==14 and rows[1]['start_seconds']==20


def test_renderer_consumes_validated_fit_without_reprocessing_or_calls(tmp_path,monkeypatch):
    from bodhan_dub.fit_recovery import local_attempt
    from bodhan_dub.transcription import atomic_json
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:pytest.skip('media tools required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_a,**_k:pytest.fail('No API calls'))
    source,run=make_saved_run(tmp_path,ffmpeg);units,windows=unit_data();timeline=source_timeline(units,windows,detector())
    audio=run/'synthesis/hi/unit-0001.wav';info=inspect_wav(audio.read_bytes())
    item={'id':'unit-0001','language':'hi','original_english':units[0]['text'],'english':units[0]['text'],
          'translation':'यह एक परीक्षण है।','audio_path':str(audio),'audio_sha256':info['sha256'],'content_recovery':False}
    solution,_=local_attempt(item,timeline['units'][0],tmp_path/'fit',ffmpeg)
    manifest={'all_units_fitted':True,'timeline':timeline,'source_run':str(run.resolve()),'languages':{'hi':{'units':[solution]}}}
    path=tmp_path/'fit_manifest.json';atomic_json(path,manifest)
    original=Path(solution['fit']['path']).read_bytes()
    report=render_saved(source,run,tmp_path/'final',['hi'],ffmpeg=ffmpeg,ffprobe=ffprobe,allow_short=True,fit_manifest=path)
    assert report['languages']['hi']['rendered_units']==1 and report['languages']['hi']['skipped_units']==0
    assert report['fit_validation']['all_fitted_samples_placed_once'] and report['media_outputs_created']
    assert Path(solution['fit']['path']).read_bytes()==original
    changed=bytearray(original);changed[-2]^=1;Path(solution['fit']['path']).write_bytes(changed)
    with pytest.raises(MediaError):render_saved(source,run,tmp_path/'reject',['hi'],ffmpeg=ffmpeg,ffprobe=ffprobe,allow_short=True,fit_manifest=path)
    assert not (tmp_path/'reject').exists()
