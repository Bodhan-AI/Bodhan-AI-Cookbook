"""Offline final-media stage using existing Bodhan stage artifacts only."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import wave

import numpy as np

from .audio_output import (RATE,measure_loudness,mux_video,pcm_read,pcm_write,place_pcm,process_clip)
from .media import MediaError,describe,inspect_video,tool_path
from .subtitles import cues_for_intervals,serialize
from .synthesis import inspect_wav
from .timing import source_timeline
from .transcription import atomic_json

PRODUCTION_LANGUAGES={'en','hi','bn','mr','te','ta','gu','kn','ml','or','pa','as','ur'}


def validate_languages(langs):
    if not langs or len(set(langs))!=len(langs) or any(l not in PRODUCTION_LANGUAGES for l in langs):
        raise MediaError('unsupported_render_language','Request distinct Indic-Speak production-language codes.')


def render_saved(source,run,out,langs,repair=None,ffmpeg=None,ffprobe=None,allow_short=False,fit_manifest=None):
    validate_languages(langs);started=time.monotonic();run=Path(run).resolve();out=Path(out).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise MediaError('output_exists','Choose a new or empty final-output folder; prior runs are preserved.')
    if out==run or run in out.parents and out.name in {'synthesis','translations','verification'}:
        raise MediaError('invalid_render_output','Final output must not replace stage artifacts.')
    ffmpeg=ffmpeg or tool_path('ffmpeg');ffprobe=ffprobe or tool_path('ffprobe')
    info=inspect_video(Path(source),ffprobe,allow_short=allow_short)
    units=json.loads((run/'speech_units.en.json').read_text())['units']
    corrected=json.loads((run/'corrections.final.json').read_text())
    detector=json.loads((run.parent/'speech_regions.json').read_text())
    source_wav=run.parent/'source_audio.wav'
    with wave.open(str(source_wav),'rb') as audio:
        if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getnframes())!=(16000,1,2,detector['total_samples']):
            raise MediaError('source_audio_changed','Saved source analysis audio and detector metadata disagree.')
    timeline=source_timeline(units,corrected['source_windows'],detector)
    if abs(timeline['duration_seconds']-info.duration_seconds)>.01:
        raise MediaError('cached_video_mismatch','Cached analysis duration differs from the selected video.')
    synthesis=json.loads((run/'synthesis/synthesis_report.json').read_text())
    qa_path=run/'verification/verification_report.json'
    qa=json.loads(qa_path.read_text()) if qa_path.exists() else {'languages':{}}
    repairs=json.loads(Path(repair).read_text()) if repair else None
    fitted=json.loads(Path(fit_manifest).read_text()) if fit_manifest else None
    if fitted:
        from .fit_validation import validate_fit_manifest
        validation=validate_fit_manifest(fitted)
        if Path(fitted['source_run']).resolve()!=run:raise MediaError('fit_source_mismatch','Fitting manifest belongs to another source run.')
    out.mkdir(parents=True,exist_ok=True);work=out/'_work';work.mkdir()
    report={'status':'rendering','input':describe(info),'requested_languages':langs,'languages':{},'hosted_calls_this_stage':0,
        'new_cost_inr':'0','dubbing_complete':False,'ai_dubbed':True,'timeline':timeline,
        'quality_policy':'Keep existing translations and content-QA warnings. No paid recovery or translation editing in this stage.',
        'limitations':timeline['limitations']+[
            'Subtitles are proportionally timed phrases, not word-aligned captions.',
            'The original mixed audio is excluded. Gaps stay silent; source music/effects are not preserved without a separate voice-free background track.',
            'Clips requiring speed above 1.2x are skipped with reasons; their translated captions remain visible.',
            'Review per-segment content-QA and translation-meaning evidence; physical fit does not certify translation or naturalness.']}
    if fitted:report['fit_validation']=validation;report['fit_recovery_manifest']=str(Path(fit_manifest).resolve())
    atomic_json(out/'report.json',report)
    atomic_json(out/'transcript.en.json',{'language':'en','segments':timeline['units'],'timing_resolution':'estimated_not_word_aligned'})
    frames=round(info.duration_seconds*RATE)
    report['background_policy']={'original_audio_mixed':False,'gap_audio':'silence','clean_background_track_supplied':False,
        'reason':'VAD non-speech labels are not proof that the original track contains no English speech.'}
    log_path=out/'events.jsonl'
    def log(event):
        with log_path.open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
    for lang in langs:
        language_start=time.monotonic();folder=out/lang;folder.mkdir()
        entry={'status':'rendering','segments':[],'warnings':[],'completed_stages':[]};report['languages'][lang]=entry
        try:
            translated=json.loads((run/'translations'/f'translation.{lang}.json').read_text())
            translations={u['id']:u for u in translated['units']}
            native={u['id']:u for u in synthesis['languages'][lang]['units']}
            qas={u['id']:u for u in qa['languages'].get(lang,{}).get('units',[])}
            override={u['id']:u for u in repairs['languages'].get(lang,{}).get('units',[])} if repairs else {}
            solutions={u['id']:u for u in fitted['languages'].get(lang,{}).get('units',[])} if fitted else {}
            track=np.zeros(frames,dtype=np.int16);captions=[];previous_end=0
            for slot in timeline['units']:
                ident=slot['id'];original=next(u for u in units if u['id']==ident)
                row={'id':ident,'source_text':original['text'],'source_timing':slot,'status':'pending',
                     'qa':qas.get(ident,{'status':'not_available'}),'content_qa_status':'unresolved_or_historical','retries_this_stage':0}
                try:
                    solution=solutions.get(ident)
                    if solution:
                        if solution['original_english']!=original['text']:raise MediaError('fit_text_changed','Original English differs from the fitted source.')
                        slot=solution['timing'];text=solution['translation'];audio=Path(solution['audio_path'])
                        if inspect_wav(audio.read_bytes())['sha256']!=solution['audio_sha256']:raise MediaError('speech_audio_changed','Recovery source audio changed.')
                        fit=solution['fit'];pcm,fitted_info=pcm_read(fit['path'])
                        if fitted_info['sha256']!=solution['normalized_audio_sha256']:raise MediaError('fitted_audio_changed','Fitted recovery audio changed.')
                        row.update(translation=text,source_timing=slot,adapted_english=solution['english'],content_recovery=solution['content_recovery'],
                                   speech_source=str(audio),pause_compaction=solution.get('pause_compaction'))
                        if solution.get('qa'):row['qa']=solution['qa']
                    else:
                        target=translations[ident]
                        if target['status']!='translated':raise MediaError('source_translation_failed','Translation failed or was skipped.')
                        text=target['translation'];row['translation']=text
                        speech=override.get(ident,native.get(ident))
                        if not speech or speech['status']!='synthesized':raise MediaError('source_synthesis_failed','Speech generation failed or was skipped.')
                        if speech['text']!=text:raise MediaError('speech_reference_changed','Speech reference differs from the saved translation.')
                        audio=(Path(repair).resolve().parent/lang/(ident+'.wav')) if ident in override else run/'synthesis'/lang/(ident+'.wav')
                        if inspect_wav(audio.read_bytes())['sha256']!=speech['result']['audio']['sha256']:
                            raise MediaError('speech_audio_changed','Saved generated audio differs from its recorded digest.')
                        row['speech_source']=str(audio);row['used_repair_candidate']=ident in override
                        if ident in override:row['qa_note']='Original QA is historical; selected repair candidate has separate unresolved QA. See repair report.'
                        if slot['available_seconds']<=0:raise MediaError('no_source_speech_slot','No unprotected speech time is available.')
                        fit,pcm=process_clip(audio,work/lang/ident,slot['available_seconds'],ffmpeg)
                    row['fit']=fit
                    if fit['status']=='skipped':row.update(status='skipped',skip_reason=fit['reason'])
                    else:
                        if solution:placements,warnings=solution['placements'],solution.get('warnings',[])
                        else:placements,warnings=place_pcm(pcm,slot['speech_slots'],fit.get('approved_break_samples',()))
                        row['placements']=placements;row['warnings']=warnings
                        proposed_end=previous_end
                        for placement in placements:
                            a,b=placement['start_sample'],placement['end_sample']
                            if a<proposed_end or b>len(track):raise MediaError('audio_overlap','Planned speech overlaps or exceeds the video.')
                            proposed_end=b
                        row.update(status='rendered',speed=fit['speed'],loudness_lufs=fit['loudness_lufs'])
                    intervals=[(p['start_seconds'],p['end_seconds']) for p in row.get('placements',[])]
                    if not intervals:intervals=[(s['start_seconds'],s['end_seconds']) for s in slot['speech_slots']]
                    row['caption_only']=row['status']=='skipped'
                    cues=cues_for_intervals(text,intervals,ident)
                    if row['status']=='rendered':
                        for placement in row['placements']:
                            a,b=placement['start_sample'],placement['end_sample']
                            track[a:b]=pcm[placement['audio_start_sample']:placement['audio_end_sample']]
                        previous_end=proposed_end
                    captions.extend(cues);row['subtitle_cue_count']=len(cues)
                except (MediaError,OSError,KeyError,ValueError,TypeError) as exc:
                    row.update(status='skipped',skip_reason=str(exc) if isinstance(exc,MediaError) else 'Cannot read this unit or its media.',error_code=getattr(exc,'code','invalid_unit'))
                entry['segments'].append(row);log({'stage':'offline_render','language':lang,'unit_id':ident,'status':row['status'],'reason':row.get('skip_reason')})
                atomic_json(folder/f'segments.{lang}.json',{'language':lang,'segments':entry['segments']})
                atomic_json(out/'report.json',report)
            entry['completed_stages'].extend(['fit_audio','create_timeline'])
            speech_path=folder/f'speech.{lang}.wav';pcm_write(speech_path,track)
            entry['speech_loudness_lufs']=measure_loudness(speech_path,ffmpeg)['input_i'] if np.any(track) else None
            entry['speech_loudness_target_met']=entry['speech_loudness_lufs'] is not None and abs(entry['speech_loudness_lufs']+16)<=1
            # Never copy the original mixed track, even where VAD missed speech.
            pcm_write(folder/f'audio.{lang}.wav',track);entry['completed_stages'].append('assemble_audio')
            captions.sort(key=lambda c:c['start_seconds'])
            (folder/f'subtitles.{lang}.srt').write_text(serialize(captions),encoding='utf-8')
            (folder/f'subtitles.{lang}.vtt').write_text(serialize(captions,True),encoding='utf-8');entry['completed_stages'].append('subtitles')
            entry['subtitles']={'cue_count':len(captions),'timing':'estimated_phrase_timing','high_reading_rate_cues':sum(c['characters_per_second']>25 for c in captions)}
            entry['video']=mux_video(source,folder/f'audio.{lang}.wav',folder/f'video.{lang}.mp4',info,ffmpeg,ffprobe)
            entry['completed_stages'].append('mux_video')
            entry['rendered_units']=sum(s['status']=='rendered' for s in entry['segments'])
            entry['skipped_units']=sum(s['status']=='skipped' for s in entry['segments'])
            entry['status']='partial_failure' if entry['skipped_units'] else 'completed_with_warnings'
            entry['warnings'].extend(['Source sync is estimated, not verified to ±300 ms.','Content fidelity and translation findings remain in the source QA reports.'])
        except (MediaError,OSError,KeyError,ValueError,TypeError) as exc:
            entry.update(status='failed',error={'code':getattr(exc,'code','invalid_render_artifact'),
                'message':str(exc) if isinstance(exc,MediaError) else 'Cannot render this language from its saved artifacts.'})
        entry['latency_seconds']=round(time.monotonic()-language_start,3)
        atomic_json(folder/f'segments.{lang}.json',{'language':lang,'segments':entry['segments']})
        atomic_json(out/'report.json',report)
        print(json.dumps({'event':'language_rendered','language':lang,'status':entry['status'],'rendered_units':entry.get('rendered_units'),'skipped_units':entry.get('skipped_units')}),flush=True)
    report['status']='partial_failure' if any(e['status'] in {'failed','partial_failure'} for e in report['languages'].values()) else 'completed_with_warnings'
    report['dubbing_complete']=report['status']=='completed_with_warnings'
    report['media_outputs_created']=all((out/l/f'video.{l}.mp4').exists() and (out/l/f'subtitles.{l}.srt').exists() for l in langs)
    report['latency_seconds']=round(time.monotonic()-started,3)
    atomic_json(out/'report.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Render existing Bodhan artifacts into dubbed videos and subtitles offline.')
    p.add_argument('--input',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--langs',default='hi,ta,bn');p.add_argument('--out',type=Path,required=True)
    p.add_argument('--fit-manifest',type=Path)
    p.add_argument('--repair-synthesis',type=Path);p.add_argument('--allow-short-input',action='store_true')
    args=p.parse_args(argv)
    new_report=not (args.out/'report.json').exists()
    try:
        r=render_saved(args.input,args.run,args.out,args.langs.split(','),repair=args.repair_synthesis,allow_short=args.allow_short_input,fit_manifest=args.fit_manifest)
        print(json.dumps({'status':r['status'],'report':str(args.out/'report.json'),'media_outputs_created':r['media_outputs_created'],'new_hosted_calls':0}))
        return int(r['status']=='partial_failure')
    except (MediaError,OSError,ValueError,KeyError,TypeError) as exc:
        if new_report and (args.out/'report.json').exists():
            report=json.loads((args.out/'report.json').read_text())
            report.update(status='failed',error={'code':getattr(exc,'code','invalid_render_input'),'message':str(exc)},dubbing_complete=False)
            atomic_json(args.out/'report.json',report)
        print(json.dumps({'status':'failed','code':getattr(exc,'code','invalid_render_input'),
             'message':str(exc) if isinstance(exc,MediaError) else 'Cannot read rendering inputs.'}));return 1


if __name__=='__main__':raise SystemExit(main())
