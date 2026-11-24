import json
import wave

import pytest

from bodhan_dub.media import MediaError
from bodhan_dub.window_review import create_window_review


def make_window_run(run, seconds=2):
    folder=run/'asr_windows';folder.mkdir()
    frames=seconds*16000
    with wave.open(str(folder/'window-0001.wav'),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'));out.writeframes(b'\x00\x00'*frames)
    data={'total_samples':frames,'sample_rate':16000,'asr_windows':[{'start_sample':0,'end_sample':frames,
          'core_start_sample':0,'core_end_sample':frames,'cut_reason':'end_of_recording'}]}
    (run/'protected_regions.json').write_text(json.dumps(data))
    return data


def test_actual_window_file_and_display_match(tmp_path):
    make_window_run(tmp_path)
    page=create_window_review(tmp_path)
    html=page.read_text()
    data=json.loads(html.split('<script type="application/json" id="window-data">')[1].split('</script>')[0])
    assert data['windows'][0]['file']=='asr_windows/window-0001.wav'
    assert data['windows'][0]['duration']==2
    assert '__WINDOW_DATA__' not in html
    with pytest.raises(MediaError):
        create_window_review(tmp_path)


def test_reject_over_limit_window(tmp_path):
    make_window_run(tmp_path,31)
    with pytest.raises(MediaError):
        create_window_review(tmp_path)


def test_reject_wrong_wav_duration(tmp_path):
    data=make_window_run(tmp_path)
    data['asr_windows'][0]['end_sample']-=1
    data['asr_windows'][0]['core_end_sample']-=1
    (tmp_path/'protected_regions.json').write_text(json.dumps(data))
    with pytest.raises(MediaError):
        create_window_review(tmp_path)


def test_reject_core_coverage_gap(tmp_path):
    data=make_window_run(tmp_path)
    data['asr_windows'][0]['core_start_sample']=10
    (tmp_path/'protected_regions.json').write_text(json.dumps(data))
    with pytest.raises(MediaError):
        create_window_review(tmp_path)


def test_reject_window_link_outside_folder(tmp_path):
    make_window_run(tmp_path)
    path=tmp_path/'asr_windows/window-0001.wav'
    external=tmp_path/'outside.wav'
    path.rename(external);path.symlink_to(external)
    with pytest.raises(MediaError):
        create_window_review(tmp_path)


def test_missing_manifest(tmp_path):
    with pytest.raises(MediaError):
        create_window_review(tmp_path)
