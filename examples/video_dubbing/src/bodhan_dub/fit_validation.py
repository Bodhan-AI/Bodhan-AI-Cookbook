"""Enforce physical fitting invariants before media output is accepted."""
import math

from .audio_output import RATE,pcm_read
from .media import MediaError


def validate_fit_manifest(manifest):
    if not manifest.get('all_units_fitted'):raise MediaError('fit_not_complete','Resolve every unit before final fitted output.')
    timeline=manifest['timeline'];expected=[u['id'] for u in timeline['units']];frames=round(timeline['duration_seconds']*RATE)
    checked=0
    for lang,data in manifest['languages'].items():
        if [u['id'] for u in data['units']]!=expected:raise MediaError('fit_coverage_changed','Fit output does not cover every source unit once.')
        previous_end=0
        for unit in data['units']:
            if unit['status']!='fitted':raise MediaError('fit_not_complete','A unit is not fitted.')
            speed=unit['fit']['speed']
            if type(speed) not in {int,float} or not math.isfinite(speed) or not .85<=speed<=1.2:
                raise MediaError('fit_speed_violation','A fitted clip exceeds allowed speed limits.')
            pcm,info=pcm_read(unit['fit']['path'])
            if info['sha256']!=unit['normalized_audio_sha256']:raise MediaError('fitted_audio_changed','Fitted PCM changed after validation.')
            approved=set();piece_cursor=0;text_cursor=0;pieces=unit['fit'].get('independent_pieces',[])
            text_boundaries=set()
            if pieces:
                from .phrase_fit import complete_phrases
                for phrase in complete_phrases(unit['translation']):
                    text_cursor+=len(phrase);text_boundaries.add(text_cursor)
                text_cursor=0
            for piece in pieces:
                piece_pcm,piece_info=pcm_read(piece['path'])
                if piece_info['sha256']!=piece['sha256'] or piece_cursor+len(piece_pcm)>len(pcm) or not (pcm[piece_cursor:piece_cursor+len(piece_pcm)]==piece_pcm).all():
                    raise MediaError('phrase_audio_changed','Independent speech piece does not match the assembled fitted audio.')
                piece_cursor+=len(piece_pcm);approved.add(piece_cursor)
                text_cursor+=len(piece['text'])
                if text_cursor not in text_boundaries:raise MediaError('unsafe_phrase_boundary','A playback piece ends inside a complete sentence or heading.')
            if pieces and piece_cursor!=len(pcm):raise MediaError('phrase_audio_incomplete','Independent pieces do not reconstruct the fitted audio.')
            if pieces and ''.join(p['text'] for p in pieces)!=unit['translation']:raise MediaError('phrase_text_changed','Independent phrase texts do not reconstruct the translation.')
            if any(not .85<=p['speed']<=1.2 for p in pieces):raise MediaError('fit_speed_violation','An independent phrase exceeds the speed bounds.')
            if set(unit['fit'].get('approved_break_samples',[]))!=approved-{len(pcm)}:
                raise MediaError('invalid_phrase_boundaries','Approved breaks lack independently generated piece evidence.')
            cursor=0
            gaps=unit['timing'].get('protected_gap_cores',timeline['protected_gaps'])
            for p in unit['placements']:
                a,b,x,y=[p[k] for k in ['start_sample','end_sample','audio_start_sample','audio_end_sample']]
                if x and x not in approved:raise MediaError('unsafe_speech_cut','A playback pause cuts an unverified position inside generated speech.')
                if any(type(v) is not int for v in [a,b,x,y]) or not previous_end<=a<b<=frames or x!=cursor or y-x!=b-a or not x<y<=len(pcm):
                    raise MediaError('invalid_fit_placement','Fitted audio overlaps, exceeds bounds or loses/duplicates samples.')
                if not any(round(s['start_seconds']*RATE)<=a and b<=round(s['end_seconds']*RATE) for s in unit['timing']['speech_slots']):
                    raise MediaError('fit_slot_violation','Fitted audio exceeds its allocated source slot.')
                for gap in gaps:
                    if gap['end_seconds']-gap['start_seconds']<1-1e-6:raise MediaError('fit_pause_violation','A protected long pause became shorter than one second.')
                    if a<round(gap['end_seconds']*RATE) and b>round(gap['start_seconds']*RATE):
                        raise MediaError('fit_pause_violation','Fitted speech enters a protected source-pause core.')
                previous_end=b;cursor=y
            if cursor!=len(pcm):raise MediaError('fit_audio_incomplete','Not all fitted samples were placed.')
            checked+=1
    return {'all_units_covered_once':True,'all_fitted_samples_placed_once':True,'no_speech_overlaps':True,
            'no_speech_in_protected_pause_cores':True,'speed_bounds_met':True,'no_waveform_cuts_inside_generated_pieces':True,'checked_units':checked,
            'source_sync_within_300ms_verified':False}
