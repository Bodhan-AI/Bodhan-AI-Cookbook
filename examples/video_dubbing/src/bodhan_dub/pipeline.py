"""One-command orchestration; all native language generation stays with Bodhan."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import shutil
import time

from . import transcription as asr, translation as mt, synthesis as tts
from . import reconciliation as rec, grouping, verification as qa
from .boundaries import BoundarySettings, prepare_boundaries
from .configuration import voice_configuration, glossary_configuration
from .corrections import correct
from .fit_grouping import connected_groups
from .fit_recovery import Budget, local_attempt, run_fit
from .fit_validation import validate_fit_manifest
from .media import MediaError, describe, extract_audio, inspect_video, tool_path
from .render import render_saved, validate_languages
from .speech_repair import assemble
from .phrase_fit import complete_phrases
from .timing import source_timeline
from .transcription import atomic_json
from .vad import detect_speech, VADSettings
from .meaning_checks import normal_source
from .translation_repair import before_speech,recover_translation_units
from .quality_targets import summarize as quality_targets
from .source_recovery import recover_source,boundary_recovery
from .source_punctuation import punctuate_source


def stage_call(budget, name, allowance, operation, cost):
    """Reserve before a hosted stage; uncertain failures retain their allowance."""
    index=budget.reserve(name, allowance)
    value=operation()
    budget.settle(index, cost(value))
    return value


def deterministic_draft(windows):
    """Keep source words and apply only jointly valid exact-overlap deletions."""
    offered=rec.join_candidates(windows)
    decisions=[b['choices'][0]['decision'] for b in offered]
    for index,boundary in enumerate(offered):
        exact=sorted((c for c in boundary['choices'] if c['decision']['reason']=='overlap_duplicate'),
            key=lambda c:c['decision']['trim_left']+c['decision']['trim_right'],reverse=True)
        for choice in exact:
            trial=list(decisions);trial[index]=choice['decision']
            try:rec.compile_joins(windows,{'joins':trial,'sentence_ends':[],'commas':[],'issues':[]})
            except MediaError:continue
            decisions=trial;break
    plan=rec.compile_joins(windows,{'joins':decisions,'sentence_ends':[],'commas':[],'issues':[]})
    last={}
    for sentence in plan['sentences']:
        for span in sentence['spans']:last[span['window_id']]=span['end']-1
    plan=rec.compile_joins(windows,{'joins':decisions,'sentence_ends':[
        {'window_id':ident,'token':index,'mark':'.'} for ident,index in last.items()],'commas':[],'issues':[]})
    result=rec.validate_plan(windows,plan)
    result['planning_fallback']='Deterministic exact overlaps only; wording uncertainty remains audited.'
    return result


def speech_pieces(text, target_characters=180):
    """Pack neighboring complete phrases up to a short synthesis target."""
    pending = ''
    for phrase in complete_phrases(text):
        if pending and len(pending) + len(phrase) > target_characters:
            yield pending
            pending = ''
        pending += phrase
    if pending:
        yield pending


def speech_jobs(report, voices):
    jobs=[];parents=[]
    for lang,data in report['languages'].items():
        if data.get('status')!='translated':continue
        for unit in data.get('units',[]):
            if unit['status']!='translated':continue
            children=[];voice=voices.get(lang,tts.DEFAULT_VOICES[lang])
            for index,text in enumerate(speech_pieces(unit['translation']),1):
                child=unit['id']+f'-part-{index:02d}';children.append(child)
                jobs.append({'id':child,'text':text,'voice':voice,'language':lang,'source_window_envelope':unit['source_window_envelope']})
            parents.append({'id':unit['id'],'text':unit['translation'],'children':children,'voice':voice,
                            'language':lang,'source_window_envelope':unit['source_window_envelope']})
    return jobs,{'parents':parents}


def run_pipeline(args):
    out=args.out.expanduser().resolve();started=time.monotonic()
    cache=args.cache_dir.expanduser().resolve() if getattr(args,'cache_dir',None) else out/'.cache'
    validate_languages(args.langs.split(','))
    langs=args.langs.split(',');voices=voice_configuration(args.voices,langs);glossary=glossary_configuration(args.glossary)
    if args.code_mixed and 'en' in langs:raise MediaError('unsupported_code_mixed_language','Code-mixed mode does not support English as a target.')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise MediaError('output_exists','Choose a new or empty output directory.')
    ffmpeg=tool_path('ffmpeg',args.ffmpeg);ffprobe=tool_path('ffprobe',args.ffprobe)
    info=inspect_video(args.input,ffprobe,args.media_timeout,args.allow_short_input)
    out.mkdir(parents=True,exist_ok=True)
    report={'status':'running','dubbing_complete':False,'requested_languages':langs,'completed_stages':[],
            'input':describe(info),'languages':{},'execution_scope':'saved_audio_render' if args.from_run else ('full_pipeline_with_shared_cache' if getattr(args,'cache_dir',None) else 'full_hosted_pipeline'),
            'model_policy':'Bodhan only for ASR, translation and speech; LLM receives English text only.'}
    budget=None
    def save(stage):
        report['completed_stages'].append(stage);report['latency_seconds']=round(time.monotonic()-started,3)
        if budget:report['conservative_new_cost_inr']=str(budget.spent)
        atomic_json(out/'report.json',report)
        event={'event':'stage_completed','stage':stage,'status':report['status'],'elapsed_seconds':report['latency_seconds']}
        with (out/'events.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(event)+'\n')
        print(json.dumps(event),flush=True)
    try:
        if args.from_run:
            if args.fit_manifest is None:raise MediaError('missing_fit_manifest','Saved-run rendering requires its validated fit manifest.')
            rendered=render_saved(args.input,args.from_run,out/'_render',langs,ffmpeg=ffmpeg,ffprobe=ffprobe,
                                  allow_short=args.allow_short_input,fit_manifest=args.fit_manifest)
        else:
            # Validate all credentials/config before the first paid request. Never print them.
            keys=(asr.load_key(args.env_file),mt.load_key(args.env_file),tts.load_key(args.env_file))
            llm=rec.load_settings(args.env_file)
            budget=Budget(out/'budget.json',Decimal(args.max_cost_inr))
            stages=out/'_stages';run=stages/'run';run.mkdir(parents=True)
            extract_audio(info,stages/'source_audio.wav',ffmpeg,args.media_timeout)
            detector=detect_speech(stages/'source_audio.wav',VADSettings(args.vad_threshold,args.vad_min_speech_ms,args.vad_min_silence_ms,args.vad_pad_ms))
            atomic_json(stages/'speech_regions.json',detector)
            if not detector['speech_regions']:raise MediaError('no_speech_detected','No speech detected; no hosted requests were sent.')
            # Boundary exporter expects analysis media and detection together.
            prepare_boundaries(stages,BoundarySettings(context_ms=1000,target_core_ms=13000,max_window_ms=15000))
            save('media_and_boundaries')
            _,estimate=asr.inspect_windows(stages)
            client=asr.TranscriptionClient(keys[0],cache/'asr',asr.RetrySettings(attempts=3,concurrency=1,retry_ambiguous_once=True))
            raw=stage_call(budget,'source_asr',Decimal(estimate['estimated_first_pass_cost_inr'])*3,
                lambda:asr.transcribe_run(stages,client),lambda r:r['estimated_new_cost_inr'])
            # Empty recognition is acceptable only where VAD also found no speech.
            selected=[];silent=[]
            for row in raw['windows']:
                text=row.get('result',{}).get('text','')
                overlaps=any(row['start_seconds']<r['end_sample']/16000 and row['end_seconds']>r['start_sample']/16000 for r in detector['speech_regions'])
                if not text.strip() and not overlaps:
                    silent.append({'window_id':row['id'],'reason':'No VAD speech and no recognized text.'});continue
                if row['status']!='transcribed':raise MediaError('incomplete_source_asr','Source speech recognition failed; no invented replacement was used.')
                selected.append({'id':row['id'],'text':text,'start_seconds':row['start_seconds'],'end_seconds':row['end_seconds']})
            report['non_speech_windows']=silent
            selected,recovery= recover_source(selected,stages,client,budget)
            report['source_quality_recovery']=recovery
            windows=rec.source_windows(selected)
            try:draft=rec.reconcile(windows,llm,cache/'reconciliation')
            except MediaError as exc:
                draft=deterministic_draft(windows);report['reconciliation_warning']=exc.code
            if draft.get('planning_fallback'):
                draft=punctuate_source(windows,draft,llm,cache/'punctuation')
            atomic_json(run/'reconciliation.json',draft)
            corrected=correct(windows,draft,recovery=boundary_recovery(stages,client,budget),max_recovery_calls=2,protected_terms=tuple(glossary))
            atomic_json(run/'corrections.final.json',corrected)
            if corrected.get('blocked_window_ids'):
                raise MediaError('unresolved_source_conflict','Source overlap recovery remains unresolved; no translation or speech calls started.')
            rows=grouping.grouping_input(corrected)
            try:plan,metadata=grouping.llm_plan(rows,grouping.GroupSettings(),llm,cache/'grouping')
            except MediaError as exc:plan=grouping.fallback_plan(rows,grouping.GroupSettings());metadata={'fallback_reason':exc.code}
            grouped=grouping.build_groups(corrected,detector,plan)
            timeline=source_timeline(grouped['units'],corrected['source_windows'],detector)
            grouped['units'],events=connected_groups(grouped['units'],timeline)
            grouped.update(planning=metadata,fit_grouping_events=events)
            atomic_json(run/'speech_units.en.json',grouped)
            atomic_json(out/'transcript.en.json',{'segments':grouped['units'],'timing_resolution':'source_envelopes_not_word_aligned'})
            save('source_transcription_and_grouping')
            atomic_json(run/'translation_policy.json',{'glossary':glossary,'code_mixed':args.code_mixed})
            settings=mt.TranslationSettings(attempts=3,max_output_tokens=8192)
            translator=mt.TranslationClient(keys[1],cache/'translation',settings,glossary=glossary,code_mixed=args.code_mixed)
            translated=stage_call(budget,'translation',Decimal(len(langs)*settings.max_output_tokens*3)*mt.RATE,
                lambda:mt.translate_run([{**u,'text':normal_source(u['text'])} for u in grouped['units']],langs,run/'translations',translator),
                lambda r:r['known_new_cost_inr'] if all(x.get('known_new_cost_inr') is not None for x in r['languages'].values()) else r['conservative_new_cost_upper_bound_inr'])
            save('translation')
            translated=recover_translation_units(grouped['units'],translated,translator,budget,run/'translations')
            translated=before_speech(grouped['units'],translated,args.env_file,budget,run/'translations',reference_glossary=glossary,cache_root=cache/'meaning')
            eligible = {
                lang: data for lang, data in translated['languages'].items()
                if data['status'] == 'translated' and len(data['units']) == len(grouped['units'])
            }
            for lang in langs:
                if lang not in eligible:
                    report['languages'][lang] = {
                        'status': 'failed',
                        'reason': 'Translation or meaning verification incomplete; see translation stage journals.',
                        'unresolved_units': [row['id'] for row in translated['languages'].get(lang, {}).get('units', [])
                                             if row['status'] != 'translated'],
                    }
            save('translation_meaning_verification')
            jobs,parents=speech_jobs({**translated, 'languages': eligible},voices)
            if not jobs:raise MediaError('no_usable_translation','No language produced usable translated text.')
            speech=tts.SpeechClient(keys[2],cache/'speech',ffmpeg,tts.SpeechSettings(attempts=3,workers=2),budget=budget)
            timeline = source_timeline(grouped['units'], corrected['source_windows'], detector)
            timings = {unit['id']: unit for unit in timeline['units']}
            recognizer = asr.TranscriptionClient(keys[0], cache/'asr',
                asr.RetrySettings(attempts=1, concurrency=2), allowed_languages=set(eligible))
            ready = {}
            children = {}
            futures = []
            for parent in parents['parents']:
                key = (parent['language'], parent['id'])
                ready[key] = []
                for child in parent['children']:
                    children[parent['language'], child] = parent

            def check_group(parent, pieces):
                lang, ident = parent['language'], parent['id']
                folder = run/'groups'/lang/ident
                assembled_group = assemble({'parents': [parent]},
                    {'languages': {lang: {'units': pieces}}}, folder)
                unit = assembled_group['languages'][lang]['units'][0]
                audio = unit['result']['audio_path']
                fitted, _ = local_attempt({'audio_path': audio}, timings[ident],
                    out/'_fit/local'/lang/ident/'base', ffmpeg)
                rows = qa.inspect_synthesis(folder/'synthesis_report.json')
                requests = qa.prepare(rows, folder/'verification', ffmpeg)
                amount = Decimal(qa.estimate(rows)['one_attempt_cost_inr']) + Decimal('.00001')
                checked = stage_call(budget, 'speech_verification', amount,
                    lambda: qa.verify(rows, requests, folder/'verification', recognizer, amount, workers=2),
                    lambda result: result['estimated_new_cost_inr'])
                return lang, ident, checked, fitted

            with ThreadPoolExecutor(max_workers=1) as verification_pool:
                def piece_finished(lang, row):
                    parent = children[lang, row['id']]
                    pieces = ready[lang, parent['id']]
                    pieces.append(row)
                    if len(pieces) == len(parent['children']) and all(piece['status'] == 'synthesized' for piece in pieces):
                        futures.append(verification_pool.submit(check_group, parent, pieces))

                generated = tts.synthesize_run(jobs, run/'parts', speech, on_piece=piece_finished)
                assembled=assemble(parents,generated,run/'synthesis')
                save('speech_generation')
            usable=[]
            for lang in langs:
                data=assembled['languages'].get(lang,{})
                if translated['languages'][lang]['status']=='translated' and data.get('status')=='synthesized' and len(data.get('units',[]))==len(grouped['units']):usable.append(lang)
                elif lang in eligible:report['languages'][lang]={'status':'failed','reason':'Synthesis incomplete; see stage journals.'}
            if not usable:raise MediaError('no_complete_language','No language has complete source coverage; no incomplete video was finalized.')
            assembled['languages']={l:assembled['languages'][l] for l in usable};atomic_json(run/'synthesis/synthesis_report.json',assembled)
            early_fits = {}
            checks = {'languages': {}, 'requests': []}
            planned_cost = Decimal(0)
            for future in futures:
                lang, ident, checked, fitted = future.result()
                if lang in usable:
                    planned_cost += Decimal(checked['planned_cost_inr'])
                    checks['languages'].setdefault(lang, {'units': []})['units'].extend(checked['languages'][lang]['units'])
                    checks['requests'].extend(checked['requests'])
                    if fitted:
                        early_fits[lang, ident] = fitted
            order = {unit['id']: i for i, unit in enumerate(grouped['units'])}
            for data in checks['languages'].values():
                data['units'].sort(key=lambda unit: order[unit['id']])
            checks['content_check_complete'] = all(r['status'] == 'transcribed' for r in checks['requests'])
            checks['completed_requests'] = len(checks['requests'])
            checks['estimated_new_cost_inr'] = str(sum((Decimal(r['new_estimated_cost_inr']) for r in checks['requests']), Decimal(0)))
            checks['planned_cost_inr'] = str(planned_cost)
            checks['status'] = 'pass' if all(unit['status'] == 'pass' for data in checks['languages'].values() for unit in data['units']) else 'needs_recovery'
            atomic_json(run/'verification/verification_report.json', checks)
            save('content_verification')
            remaining=budget.limit-budget.spent
            if remaining<=0:raise MediaError('fit_budget_exceeded','No allowance remains for fitting recovery.')
            fitted=run_fit(run,out/'_fit',args.env_file,remaining,ffmpeg=ffmpeg,provider_cache=cache,early_fits=early_fits)
            # Separate fitting ledger is included once in the total budget.
            index=budget.reserve('fit_recovery',fitted['new_estimated_cost_inr']);budget.settle(index,fitted['new_estimated_cost_inr'])
            complete=[l for l,x in fitted['languages'].items() if all(u['status']=='fitted' for u in x['units'])]
            for lang in usable:
                if lang not in complete:report['languages'][lang]={'status':'failed','reason':'Bounded fitting recovery exhausted. No incomplete dubbed video was finalized.',
                    'unresolved_units':[u['id'] for u in fitted['languages'][lang]['units'] if u['status']!='fitted']}
            if not complete:raise MediaError('fit_not_complete','No language fitted completely; all recovery artifacts are retained.')
            accepted={**fitted,'languages':{l:fitted['languages'][l] for l in complete},'all_units_fitted':True}
            validate_fit_manifest(accepted);atomic_json(out/'_fit/render_manifest.json',accepted);save('validated_fit')
            rendered=render_saved(args.input,run,out/'_render',complete,ffmpeg=ffmpeg,ffprobe=ffprobe,
                allow_short=args.allow_short_input,fit_manifest=out/'_fit/render_manifest.json')
        for lang in langs:
            if lang in rendered['languages']:
                shutil.move(str(out/'_render'/lang),out/lang);report['languages'][lang]=rendered['languages'][lang]
        shutil.copyfile(out/'_render/transcript.en.json',out/'transcript.en.json')
        report.update(status=rendered['status'],dubbing_complete=rendered['dubbing_complete'],
            limitations=rendered['limitations'],fit_validation=rendered.get('fit_validation'),
            background_policy=rendered.get('background_policy'),
            render_report=str(out/'_render/report.json'))
        if any(v['status']=='failed' for v in report['languages'].values()):report.update(status='partial_failure',dubbing_complete=False)
        save('videos_and_subtitles')
    except (MediaError,OSError,ValueError,KeyError,TypeError,ArithmeticError) as exc:
        report.update(status='failed',dubbing_complete=False,error={'code':getattr(exc,'code','pipeline_stage_failed'),
            'message':str(exc) if isinstance(exc,MediaError) else 'A stage failed; inspect saved journals. No silent provider fallback.'})
        save('failure_recorded')
    report['cost_per_input_minute_per_language_inr']=str((budget.spent/Decimal(str(info.duration_seconds/60))/len(langs))) if budget else '0'
    report['quality_targets']=quality_targets(report)
    runtime=report['quality_targets']['runtime']['status']
    report['runtime_under_30_minutes']=(runtime=='pass') if runtime in {'pass','miss'} else None
    atomic_json(out/'report.json',report)
    return report
