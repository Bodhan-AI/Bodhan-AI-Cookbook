import json
import random
import wave

import pytest

from bodhan_dub.boundaries import BoundarySettings, boundary_plan, prepare_boundaries
from bodhan_dub.media import MediaError


@pytest.mark.parametrize('changes',[{'context_ms':0},{'context_ms':2001},{'context_ms':1.5},{'max_window_ms':30001},{'target_core_ms':30000},{'minimum_pause_ms':0}])
def test_reject_invalid_boundary_settings(changes):
    with pytest.raises(MediaError):
        BoundarySettings(**changes)


def test_context_merge_prevents_clipped_isolated_start():
    raw=[{'start_sample':16000,'end_sample':32000},{'start_sample':36000,'end_sample':48000}]
    plan=boundary_plan(64000,raw)
    assert len(plan['speech_regions'])==1
    assert plan['speech_regions'][0]['start_sample']==8000
    assert plan['speech_regions'][0]['end_sample']==56000
    assert len(plan['speech_regions'][0]['source_region_ids'])==2


@pytest.mark.parametrize('seconds',[0.0000625,1,29,30,31,100,900])
def test_coverage_without_any_vad_speech(seconds):
    frames=int(seconds*16000)
    plan=boundary_plan(frames,[])
    windows=plan['asr_windows']
    assert windows[0]['core_start_sample']==0 and windows[-1]['core_end_sample']==frames
    assert sum(w['core_end_sample']-w['core_start_sample'] for w in windows)==frames
    assert all(w['end_sample']-w['start_sample']<=480000 for w in windows)
    assert all(a['core_end_sample']==b['core_start_sample'] for a,b in zip(windows,windows[1:]))
    assert all(a['end_sample']>b['start_sample'] for a,b in zip(windows,windows[1:]))


def test_pause_preferred_over_mid_speech_cut():
    plan=boundary_plan(60*16000,[{'start_sample':0,'end_sample':24*16000},{'start_sample':26*16000,'end_sample':60*16000}])
    first=plan['asr_windows'][0]
    assert first['cut_reason']=='vad_pause'
    assert 24*16000 < first['core_end_sample'] < 26*16000


def test_randomized_source_coverage_and_group_coverage():
    rng=random.Random(48)
    for _ in range(100):
        frames=rng.randint(1,900*16000)
        points=sorted(set([0,frames]+[rng.randrange(frames) for _ in range(40)]))
        raw=[{'start_sample':a,'end_sample':b} for i,(a,b) in enumerate(zip(points,points[1:])) if i%2==0]
        plan=boundary_plan(frames,raw)
        windows=plan['asr_windows']
        assert windows[0]['start_sample']==0 and windows[-1]['end_sample']==frames
        assert all(w['start_sample']<=w['core_start_sample']<w['core_end_sample']<=w['end_sample'] for w in windows)
        assert sum(w['core_end_sample']-w['core_start_sample'] for w in windows)==frames
        assert all(a['core_end_sample']==b['core_start_sample'] for a,b in zip(windows,windows[1:]))
        assert all(w['end_sample']-w['start_sample']<=480000 for w in windows)
        for region in raw:
            assert any(g['start_sample']<=region['start_sample'] and g['end_sample']>=region['end_sample'] for g in plan['speech_regions'])


def test_exported_window_cores_reconstruct_original_samples(tmp_path):
    frames=70*16000
    # Nonconstant sample pattern catches offsets/duplication, not just duration.
    pattern=bytes(range(256))*((frames*2+255)//256)
    pcm=pattern[:frames*2]
    with wave.open(str(tmp_path/'source_audio.wav'),'wb') as source:
        source.setparams((1,2,16000,0,'NONE','not compressed'));source.writeframes(pcm)
    (tmp_path/'speech_regions.json').write_text(json.dumps({'sample_rate':16000,'total_samples':frames,'speech_regions':[{'start_sample':0,'end_sample':frames}]}))
    plan=prepare_boundaries(tmp_path)
    reconstructed=[]
    for w in plan['asr_windows']:
        with wave.open(str(tmp_path/w['path'])) as audio:
            assert audio.getnframes()<=480000
            audio.setpos(w['core_start_sample']-w['start_sample'])
            reconstructed.append(audio.readframes(w['core_end_sample']-w['core_start_sample']))
    assert b''.join(reconstructed)==pcm
    with pytest.raises(MediaError):
        prepare_boundaries(tmp_path)
