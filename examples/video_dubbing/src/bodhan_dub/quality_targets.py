"""Measured assignment targets, with unknowns kept separate from passes."""
import math


def finite(value):
    return type(value) in {int,float} and math.isfinite(value)


def summarize(report):
    requested=report.get('requested_languages',[])
    languages=report.get('languages',{})
    rows=[s for data in languages.values() for s in data.get('segments',[])]
    validation=report.get('fit_validation') or {}
    complete=bool(requested) and all(lang in languages and languages[lang].get('segments') for lang in requested)
    speeds=[s.get('speed') for s in rows if s.get('status')=='rendered']
    speed_ok=bool(speeds) and all(finite(x) and .85<=x<=1.2 for x in speeds) and validation.get('speed_bounds_met') is True
    speed_status='pass' if speed_ok else ('miss' if speeds and any(finite(x) and not .85<=x<=1.2 for x in speeds) else 'unverified')
    covered=complete and validation.get('all_units_covered_once') is True and all(
        s.get('status')=='rendered' or (s.get('status')=='skipped' and bool(s.get('skip_reason'))) for s in rows)
    measured=[s for s in rows if s.get('status')=='rendered']
    missing_cer=[s.get('id') for s in measured if not finite(s.get('qa',{}).get('cer'))]
    videos={lang:data.get('video',{}) for lang,data in languages.items()}
    length_ok=complete and all(finite(v.get('duration_error_seconds')) and abs(v['duration_error_seconds'])<=.1 for v in videos.values())
    length_status='pass' if length_ok else ('miss' if any(finite(v.get('duration_error_seconds')) and abs(v['duration_error_seconds'])>.1 for v in videos.values()) else 'unverified')
    loudness={lang:data.get('speech_loudness_lufs') for lang,data in languages.items()}
    loudness_ok=complete and all(finite(x) and -17<=x<=-15 for x in loudness.values())
    loudness_status='pass' if loudness_ok else ('miss' if any(finite(x) and not -17<=x<=-15 for x in loudness.values()) else 'unverified')
    elapsed=report.get('latency_seconds')
    reference_workload=(len(requested)==3 and 240<=report.get('input',{}).get('duration_seconds',0)<=360)
    hosted=report.get('execution_scope')=='full_hosted_pipeline'
    runtime_verified=hosted and reference_workload and report.get('dubbing_complete') is True and finite(elapsed)
    return {
        'segment_sync':{'status':'pass' if validation.get('source_sync_within_300ms_verified') is True else 'unverified',
            'target_start_error_seconds':.3,'reason':'Source positions are estimated from windows and VAD; matching those estimates is not independent acoustic verification.'},
        'speed_change':{'status':speed_status,'allowed_range':[.85,1.2],
            'measured_range':[min(speeds),max(speeds)] if speeds and all(finite(x) for x in speeds) else None,
            'independent_piece_bounds_validated':validation.get('speed_bounds_met') is True},
        'coverage':{'status':'pass' if covered else 'unverified','rendered_groups':sum(s.get('status')=='rendered' for s in rows),
            'skipped_groups':sum(s.get('status')=='skipped' for s in rows),'reason':'Coverage is checked against recognized source groups, not proof that ASR captured every spoken word.'},
        'content_fidelity':{'status':'reported' if measured and not missing_cer else 'incomplete',
            'groups_with_cer':len(measured)-len(missing_cer),'groups_without_cer':missing_cer,
            'groups_flagged_by_recognition':sum(s.get('qa',{}).get('status')!='pass' for s in measured),
            'reason':'Per-group Bodhan back-transcription CER is reported. It does not certify translation, pronunciation or naturalness.'},
        'total_length':{'status':length_status,'maximum_error_seconds':.1,
            'per_language_error_seconds':{lang:v.get('duration_error_seconds') for lang,v in videos.items()}},
        'loudness':{'status':loudness_status,'target_lufs':-16,'tolerance_lu':1,'per_language_lufs':loudness},
        'runtime':{'status':('pass' if elapsed<1800 else 'miss') if runtime_verified else 'unverified',
            'target_seconds':1800,'measured_seconds':elapsed,'execution_scope':report.get('execution_scope','unknown'),
            'reference_workload':reference_workload,
            'reason':'A saved-audio render or incomplete run does not establish the five-minute, three-language hosted target.'}
    }
