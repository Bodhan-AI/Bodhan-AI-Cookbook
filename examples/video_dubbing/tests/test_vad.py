import json
import os
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from bodhan_dub.__main__ import main
from bodhan_dub.media import MediaError
from bodhan_dub.vad import VADSettings, detect_speech, timeline_regions


def write_wav(path, samples=b'\x00\x00' * 16000, rate=16000, channels=1):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(samples)


@pytest.mark.parametrize('threshold', [0, 1, -0.1, 1.1, float('nan'), float('inf')])
def test_invalid_threshold(threshold):
    with pytest.raises(MediaError):
        VADSettings(threshold=threshold)


@pytest.mark.parametrize('setting', [{'min_speech_ms': 0}, {'min_silence_ms': -1}, {'speech_pad_ms': 10001}, {'speech_pad_ms': 1.5}])
def test_invalid_durations(setting):
    with pytest.raises(MediaError):
        VADSettings(**setting)


@pytest.mark.parametrize('spans', [[{'start': -1, 'end': 2}], [{'start': 2, 'end': 2}], [{'start': 0, 'end': 16001}],
                                   [{'start': 1.0, 'end': 2}], [{'start': 0, 'end': 100}, {'start': 99, 'end': 120}], [{}]])
def test_reject_invalid_model_timeline(spans):
    with pytest.raises(MediaError):
        timeline_regions(spans, 16000)


def test_complement_preserves_every_sample():
    speech, gaps = timeline_regions([{'start': 1600, 'end': 6400}, {'start': 8000, 'end': 14400}], 16000)
    ordered = sorted(speech + gaps, key=lambda x: x['start_sample'])
    assert ordered[0]['start_sample'] == 0
    assert ordered[-1]['end_sample'] == 16000
    assert all(a['end_sample'] == b['start_sample'] for a, b in zip(ordered, ordered[1:]))
    assert speech[0]['start_seconds'] == 0.1
    assert sum(r['end_sample'] - r['start_sample'] for r in ordered) == 16000


@pytest.mark.parametrize('rate,channels', [(24000, 1), (16000, 2)])
def test_reject_wrong_wav_format(tmp_path, rate, channels):
    path = tmp_path / 'bad-format.wav'
    write_wav(path, rate=rate, channels=channels)
    with pytest.raises(MediaError) as error:
        detect_speech(path)
    assert error.value.code == 'invalid_vad_audio'


def test_reject_empty_wav(tmp_path):
    path = tmp_path / 'empty.wav'
    write_wav(path, b'')
    with pytest.raises(MediaError):
        detect_speech(path)


def test_real_model_silence_and_no_network(tmp_path, monkeypatch):
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError('Detection must use packaged weights without network access.')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    path = tmp_path / 'silence.wav'
    write_wav(path)
    result = detect_speech(path)
    assert result['speech_regions'] == []
    assert len(result['non_speech_regions']) == 1
    assert result['non_speech_regions'][0]['end_sample'] == 16000
    assert result['warnings']


@pytest.fixture
def speech_with_pause(tmp_path):
    source = os.environ.get('BODHAN_TEST_SPEECH_WAV')
    ffmpeg = shutil.which('ffmpeg')
    if not source or not Path(source).is_file() or not ffmpeg:
        pytest.skip('Set BODHAN_TEST_SPEECH_WAV to a permitted speech sample and install ffmpeg for positive VAD integration tests.')
    normalized = tmp_path / 'normalized.wav'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', source, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(normalized)], check=True, timeout=30)
    with wave.open(str(normalized)) as audio:
        data = audio.readframes(audio.getnframes())
    length = len(data)//2
    path = tmp_path / 'controlled-pauses.wav'
    silence = b'\x00\x00' * 16000
    write_wav(path, silence + data + silence * 2 + data + silence)
    return path, length


def test_real_model_known_pause_boundaries(speech_with_pause):
    path, length = speech_with_pause
    result = detect_speech(path)
    speech = result['speech_regions']
    assert len(speech) == 2
    # Known placement, not word alignment. Permit detector framing and padding.
    for span, start in zip(speech, [16000, 48000 + length]):
        assert abs(span['start_seconds'] - start/16000) <= 0.3
        assert abs(span['end_seconds'] - (start+length)/16000) <= 0.3
    assert result['summary']['non_speech_seconds'] >= 3


def test_detection_cli_reports_honest_scope(speech_with_pause, tmp_path):
    path, length = speech_with_pause
    # Audio-only fixture in a real video, so this also exercises extraction.
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffprobe:
        pytest.skip('ffprobe required')
    video = tmp_path/'speech.mkv'
    duration = 4 + 2*length/16000
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i',f'color=c=black:s=32x32:r=25:d={duration}',
                    '-i',str(path),'-c:v','ffv1','-c:a','pcm_s16le',str(video)],check=True,timeout=30)
    out = tmp_path/'run'
    assert main(['--input',str(video),'--langs','hi','--out',str(out),'--detect-speech-only','--allow-short-input',
                 '--ffmpeg',ffmpeg,'--ffprobe',ffprobe]) == 0
    report = json.loads((out/'report.json').read_text())
    assert report['status'] == 'speech_detected'
    assert report['model_calls'] == 0 and report['local_vad_runs'] == 1
    assert not report['dubbing_complete']
    assert len(json.loads((out/'speech_regions.json').read_text())['speech_regions']) == 2
