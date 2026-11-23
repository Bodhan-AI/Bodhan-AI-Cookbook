from copy import deepcopy
import numpy as np
import pytest

from bodhan_dub.audio_output import pcm_write
from bodhan_dub.fit_grouping import connected_groups
from bodhan_dub.fit_validation import validate_fit_manifest
from bodhan_dub.media import MediaError
from bodhan_dub.synthesis import inspect_wav


def example(texts,gap=0):
    units=[];times=[]
    for n,text in enumerate(texts,1):
        units.append({'id':f'u{n}','text':text,'source_spans':[{'window_id':f'w{n}','start':0,'end':1}],
            'source_window_envelope':{'start_seconds':n*10,'end_seconds':n*10+12}})
        times.append({'id':f'u{n}','start_seconds':n*10+gap,'end_seconds':(n+1)*10})
    return units,{'units':times}


def test_joins_a_split_technical_compound_without_changing_words():
    units,t=example(['Adjacent. Yes. Linear.','PAIR. No.'])
    groups,events=connected_groups(units,t)
    assert len(groups)==1 and groups[0]['text']=='Adjacent. Yes. Linear PAIR. No.'
    assert groups[0]['source_unit_ids']==['u1','u2'] and events


def test_joins_grammatical_continuation_but_not_an_instruction():
    units,t=example(['Name adjacent angles.','that are not a linear pair.','Write it.'])
    groups,_=connected_groups(units,t)
    assert len(groups)==2 and groups[0]['text']=='Name adjacent angles that are not a linear pair.'
    units,t=example(['Write it.','TWO more terms.'])
    assert len(connected_groups(units,t)[0])==2


def test_no_join_across_long_source_pause():
    units,t=example(['Linear.','PAIR.'],gap=2)
    assert len(connected_groups(units,t)[0])==2


def test_final_fit_validation_checks_samples_speed_gaps_and_completeness(tmp_path):
    path=tmp_path/'normalized.wav';pcm_write(path,np.full(2400,1000,dtype=np.int16));info=inspect_wav(path.read_bytes())
    unit={'id':'u1','status':'fitted','normalized_audio_sha256':info['sha256'],'fit':{'path':str(path),'speed':1.1},
        'timing':{'speech_slots':[{'start_seconds':1,'end_seconds':2}]},
        'placements':[{'start_sample':24000,'end_sample':26400,'audio_start_sample':0,'audio_end_sample':2400}]}
    manifest={'all_units_fitted':True,'timeline':{'units':[{'id':'u1'}],'duration_seconds':3,
        'protected_gaps':[{'start_seconds':0,'end_seconds':1}]},'languages':{'hi':{'units':[unit]}}}
    assert validate_fit_manifest(manifest)['checked_units']==1
    bad=deepcopy(manifest);bad['languages']['hi']['units'][0]['fit']['speed']=1.3
    with pytest.raises(MediaError):validate_fit_manifest(bad)
    bad=deepcopy(manifest);bad['languages']['hi']['units'][0]['placements'][0]['audio_end_sample']=2300
    with pytest.raises(MediaError):validate_fit_manifest(bad)
    bad=deepcopy(manifest);bad['all_units_fitted']=False
    with pytest.raises(MediaError):validate_fit_manifest(bad)
