from copy import deepcopy
from bodhan_dub.quality_targets import summarize


def result():
    data={'segments':[{'id':'u1','status':'rendered','speed':1.1,'qa':{'cer':.3,'status':'needs_review'}}],
        'video':{'duration_error_seconds':.0003},'speech_loudness_lufs':-16.1}
    return {'requested_languages':['hi','ta','bn'],'languages':{lang:deepcopy(data) for lang in ['hi','ta','bn']},
        'fit_validation':{'all_units_covered_once':True,'speed_bounds_met':True,'source_sync_within_300ms_verified':False},
        'input':{'duration_seconds':329},'latency_seconds':15,'dubbing_complete':True,'execution_scope':'saved_audio_render'}


def test_offline_speed_does_not_certify_hosted_runtime_or_source_sync():
    report=result();checks=summarize(report)
    assert checks['runtime']['status']==checks['segment_sync']['status']=='unverified'
    assert checks['content_fidelity']['status']=='reported' and checks['content_fidelity']['groups_flagged_by_recognition']==3
    assert checks['coverage']['status']==checks['speed_change']['status']=='pass'
    report['execution_scope']='full_hosted_pipeline';report['latency_seconds']=1801
    assert summarize(report)['runtime']['status']=='miss'
    report['dubbing_complete']=False
    assert summarize(report)['runtime']['status']=='unverified'


def test_missing_language_measurement_and_cer_are_not_passes():
    report=result();del report['languages']['bn'];del report['languages']['ta']['segments'][0]['qa']['cer']
    report['languages']['hi']['speech_loudness_lufs']=-18
    checks=summarize(report)
    assert checks['coverage']['status']=='unverified'
    assert checks['content_fidelity']['status']=='incomplete'
    assert checks['loudness']['status']=='miss'
