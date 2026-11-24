import json
from pathlib import Path
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.review import create_review, main


def make_run(folder, spans=None):
    folder.mkdir()
    spans = spans if spans is not None else [{'start_sample':1600,'end_sample':8000}]
    (folder/'speech_regions.json').write_text(json.dumps({'total_samples':16000,'sample_rate':16000,'speech_regions':spans}))
    with wave.open(str(folder/'source_audio.wav'),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        out.writeframes(b'\x00\x00'*16000)


def test_review_embeds_canonical_timing_and_preserves_source(tmp_path):
    run=tmp_path/'run';make_run(run)
    before={name:(run/name).read_bytes() for name in ['speech_regions.json','source_audio.wav']}
    page=create_review(run)
    html=page.read_text()
    payload=json.loads(html.split('<script id="review-data" type="application/json">')[1].split('</script>')[0])
    assert len(payload['speech'])==1 and len(payload['gaps'])==2
    assert payload['speech'][0]['start_seconds']==0.1
    assert 'src="review_audio.mp3"' in html and '__REVIEW_DATA__' not in html
    assert all((run/name).read_bytes()==data for name,data in before.items())
    with pytest.raises(MediaError) as error:
        create_review(run)
    assert error.value.code=='output_exists'


def test_no_speech_review_is_valid(tmp_path):
    run=tmp_path/'run';make_run(run,[])
    assert main(['--run',str(run)])==0
    assert (run/'review.html').exists()


@pytest.mark.parametrize('spans',[[{'start_sample':-1,'end_sample':100}],[{'start_sample':0,'end_sample':17000}],[{'start_sample':10,'end_sample':100},{'start_sample':90,'end_sample':200}]])
def test_review_rejects_invalid_timeline(tmp_path,spans):
    run=tmp_path/'run';make_run(run,spans)
    with pytest.raises(MediaError):
        create_review(run)
    assert not (run/'review.html').exists()


def test_review_rejects_audio_mismatch(tmp_path):
    run=tmp_path/'run';make_run(run)
    data=json.loads((run/'speech_regions.json').read_text());data['total_samples']=16001
    (run/'speech_regions.json').write_text(json.dumps(data))
    with pytest.raises(MediaError) as error:
        create_review(run)
    assert error.value.code=='invalid_review_audio'


def test_review_rejects_missing_artifacts(tmp_path):
    assert main(['--run',str(tmp_path)])==1


def test_protected_review_reuses_audio_and_keeps_raw_page(tmp_path):
    run=tmp_path/'run';make_run(run)
    raw=create_review(run)
    original=raw.read_bytes()
    data=json.loads((run/'speech_regions.json').read_text())
    data['settings']={'context_ms':500}
    data['speech_regions'][0]['source_region_ids']=['speech-0001']
    (run/'protected_regions.json').write_text(json.dumps(data))
    protected=create_review(run,protected=True)
    assert protected.name=='review_protected.html'
    assert raw.read_bytes()==original
    payload=json.loads(protected.read_text().split('<script id="review-data" type="application/json">')[1].split('</script>')[0])
    assert payload['mode']=='protected' and payload['protectionContextMs']==500
    assert payload['speech'][0]['source_region_ids']==['speech-0001']
