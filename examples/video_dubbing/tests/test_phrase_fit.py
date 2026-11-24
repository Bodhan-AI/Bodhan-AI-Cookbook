from copy import deepcopy
import io
import json
import shutil
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from bodhan_dub.audio_output import RATE,place_pcm,pcm_read
from bodhan_dub.fit_recovery import Budget
from bodhan_dub.fit_validation import validate_fit_manifest
from bodhan_dub.media import MediaError
from bodhan_dub.phrase_fit import complete_phrases,fit_phrases,phrase_partitions
from bodhan_dub.synthesis import SpeechClient,SpeechSettings
from bodhan_dub.transcription import Response


@pytest.mark.parametrize('text',[
    'आज वे सात शब्द हैं। अपनी नोटबुक खोलें और शीर्षक लिखें।',
    'இது ஒரு வாக்கியம். உங்கள் நோட்டுப் புத்தகத்தைத் திறக்கவும்.',
    'প্রথম বাক্য। আপনার খাতা খুলুন।',
    'First heading: A complete sentence. Another sentence.',
    'Dr. Rao explains the angle. Open your notebook.',
])
def test_complete_text_partition_never_uses_word_count_or_audio_energy(text):
    atoms=complete_phrases(text)
    assert ''.join(atoms)==text
    assert all(atom.strip() for atom in atoms)
    if 'अपनी' in text:assert any(atom.startswith('अपनी नोटबुक') for atom in atoms)
    if 'Dr.' in text:assert atoms[0].startswith('Dr. Rao')


def test_unannotated_quiet_point_inside_word_is_always_rejected():
    # A low-energy consonant inside a word is not proof of a linguistic boundary.
    samples=np.full(3*RATE,1000,dtype=np.int16);samples[RATE:RATE+240]=0
    slots=[{'start_seconds':0,'end_seconds':1.5},{'start_seconds':3,'end_seconds':4.5}]
    with pytest.raises(MediaError) as error:place_pcm(samples,slots)
    assert error.value.code=='phrase_boundary_required'
    assert phrase_partitions([3*RATE],[int(1.5*RATE)]*2)==[]


@pytest.mark.parametrize('tail',['अपनी नोटबुक खोलें।','अपनी नोटबुक खोलें और '+('पूरी परिभाषा ध्यान से लिखें '*8)+'।'])
def test_real_media_phrase_generation_preserves_sentences_and_proves_pause_boundaries(tmp_path,monkeypatch,tail):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg required')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',lambda *_:pytest.fail('No network'))
    text='आज वे सात शब्द हैं। '+tail;calls=[]
    def transport(request):
        body=json.loads(request.body);spoken=body['input'];calls.append(spoken)
        seconds=.8 if spoken.startswith('आज') else 1.3
        stream=io.BytesIO()
        with wave.open(stream,'wb') as w:
            w.setparams((1,2,RATE,0,'NONE','not compressed'));w.writeframes((np.sin(np.arange(round(seconds*RATE))*2*np.pi*220/RATE)*1000).astype('<i2').tobytes())
        return Response(200,stream.getvalue())
    client=SpeechClient('fake',tmp_path/'cache',ffmpeg,SpeechSettings(attempts=1,workers=1),transport=transport,
        pacer=SimpleNamespace(wait=lambda:None,observe=lambda _:None,events=[]))
    item={'id':'u1','language':'hi','translation':text,'voice':'Kavya','source_window_envelope':{'start_seconds':0,'end_seconds':5},
          'english':'Two sentences. Open your notebook.','original_english':'Two sentences. Open your notebook.'}
    timing={'speech_slots':[{'start_seconds':0,'end_seconds':1},{'start_seconds':3,'end_seconds':5}]}
    solution,reason=fit_phrases(item,timing,tmp_path/'phrases',client,Budget(tmp_path/'budget.json','1'),ffmpeg)
    assert solution and reason is None and ''.join(calls)==text
    assert len(calls)==2
    assert calls[1].startswith('अपनी नोटबुक')
    assert [p['text'] for p in solution['fit']['independent_pieces']]==calls
    manifest={'all_units_fitted':True,'timeline':{'duration_seconds':5,'units':[{'id':'u1'}],
        'protected_gaps':[{'start_seconds':1,'end_seconds':3}]},'languages':{'hi':{'units':[solution]}}}
    checked=validate_fit_manifest(manifest)
    assert checked['no_waveform_cuts_inside_generated_pieces']
    assert solution['placements'][1]['audio_start_sample'] in solution['fit']['approved_break_samples']
    broken=deepcopy(manifest);broken['languages']['hi']['units'][0]['fit']['approved_break_samples'][0]+=100
    with pytest.raises(MediaError):validate_fit_manifest(broken)
    # Keeping the word intact is insufficient: it cannot migrate before the pause.
    broken=deepcopy(manifest);pieces=broken['languages']['hi']['units'][0]['fit']['independent_pieces']
    pieces[0]['text']+='अपनी ';pieces[1]['text']=pieces[1]['text'].removeprefix('अपनी ')
    with pytest.raises(MediaError) as error:validate_fit_manifest(broken)
    assert error.value.code=='unsafe_phrase_boundary'
    # The renderer must reject an old split even if total samples and speed are valid.
    broken=deepcopy(manifest);bad=broken['languages']['hi']['units'][0]
    bad['fit'].pop('independent_pieces');bad['fit'].pop('approved_break_samples')
    with pytest.raises(MediaError,match='pause cuts'):validate_fit_manifest(broken)



def test_long_internal_pause_can_extend_whole_phrases_without_moving_next_start():
    from bodhan_dub.phrase_fit import borrow_internal_pause_tail
    timing = {'speech_slots':[{'start_seconds':1,'end_seconds':12},{'start_seconds':17,'end_seconds':19}],
              'protected_gap_cores':[{'start_seconds':12,'end_seconds':17}], 'available_seconds':13}
    adjusted = borrow_internal_pause_tail(timing)
    assert timing['speech_slots'][0]['end_seconds'] == 12
    assert adjusted['speech_slots'] == [{'start_seconds':1,'end_seconds':14.25},{'start_seconds':17,'end_seconds':19}]
    assert adjusted['protected_gap_cores'] == [{'start_seconds':14.25,'end_seconds':17}]
    assert adjusted['internal_pause_recovery'][0]['retained_seconds'] == 2.75
