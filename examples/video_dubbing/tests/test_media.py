"""Integration checks use actual local media tools and synthetic fixtures."""

import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from bodhan_dub.__main__ import main
from bodhan_dub.media import MediaError, extract_audio, inspect_video


@pytest.fixture(scope='module')
def tools():
    found = tuple(shutil.which(name) for name in ('ffmpeg', 'ffprobe'))
    if not all(found):
        pytest.skip('Install ffmpeg and ffprobe for real media integration tests.')
    return found


def make_clip(path: Path, tools, *, audio=True, delay=0, duration=4):
    cmd = [tools[0], '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', f'color=c=black:s=32x32:r=25:d={duration}']
    if audio:
        cmd += ['-itsoffset', str(delay), '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=24000:duration={duration-delay}', '-c:a', 'pcm_s16le']
    cmd += ['-c:v', 'ffv1', str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize('extension', ['.mp4', '.mov', '.mkv', '.webm'])
def test_missing_file(extension, tmp_path):
    with pytest.raises(MediaError, match='does not exist'):
        inspect_video(tmp_path / ('missing' + extension), 'unused')


def test_reject_non_video_extension(tmp_path):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'not video')
    with pytest.raises(MediaError) as error:
        inspect_video(path, 'unused')
    assert error.value.code == 'unsupported_format'


def test_corrupt_video(tmp_path, tools):
    path = tmp_path / 'broken.mp4'
    path.write_bytes(b'broken')
    with pytest.raises(MediaError) as error:
        inspect_video(path, tools[1])
    assert error.value.code == 'invalid_media'


def test_no_audio(tmp_path, tools):
    path = tmp_path / 'silent.mkv'
    make_clip(path, tools, audio=False)
    with pytest.raises(MediaError) as error:
        inspect_video(path, tools[1], allow_short=True)
    assert error.value.code == 'missing_audio'


def test_short_fixture_requires_opt_in(tmp_path, tools):
    path = tmp_path / 'short.mkv'
    make_clip(path, tools)
    with pytest.raises(MediaError) as error:
        inspect_video(path, tools[1])
    assert error.value.code == 'unsupported_duration'
    assert inspect_video(path, tools[1], allow_short=True).duration_seconds == pytest.approx(4)


@pytest.mark.parametrize('extension', ['mp4', 'mov', 'mkv', 'webm'])
def test_supported_containers(tmp_path, tools, extension):
    path = tmp_path / f'fixture.{extension}'
    codecs = ['-c:v', 'libvpx-vp9', '-c:a', 'libopus'] if extension == 'webm' else ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac']
    subprocess.run([tools[0], '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', 'color=c=black:s=32x32:r=25:d=2',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000:duration=2', *codecs, str(path)],
                   check=True, capture_output=True, timeout=30)
    info = inspect_video(path, tools[1], allow_short=True)
    result = extract_audio(info, tmp_path / 'output.wav', tools[0])
    assert abs(result['duration_seconds'] - info.duration_seconds) < 0.01


@pytest.mark.parametrize('delay', [0, 1])
def test_extraction_preserves_duration_and_leading_gap(tmp_path, tools, delay):
    source, target = tmp_path / 'with spaces.mkv', tmp_path / 'audio.wav'
    make_clip(source, tools, delay=delay)
    original = source.read_bytes()
    info = inspect_video(source, tools[1], allow_short=True)
    result = extract_audio(info, target, tools[0])
    assert result['duration_seconds'] == pytest.approx(4, abs=0.01)
    assert result['sample_rate'] == 16000
    assert result['channels'] == 1
    assert source.read_bytes() == original
    if delay:
        with wave.open(str(target)) as audio:
            assert not any(audio.readframes(8000)), 'Delayed source audio must retain leading silence.'
            audio.setpos(24000)
            assert any(audio.readframes(8000)), 'Speech-region samples must not be discarded.'
    with pytest.raises(MediaError) as error:
        extract_audio(info, target, tools[0])
    assert error.value.code == 'output_exists'


def test_prepare_command_and_non_overwrite(tmp_path, tools):
    source, out = tmp_path / 'input.mkv', tmp_path / 'result'
    make_clip(source, tools)
    args = ['--input', str(source), '--langs', 'hi,ta,bn', '--out', str(out), '--prepare-only', '--allow-short-input', '--ffmpeg', tools[0], '--ffprobe', tools[1]]
    assert main(args) == 0
    report = json.loads((out / 'report.json').read_text())
    assert report['status'] == 'prepared' and not report['dubbing_complete']
    assert report['model_calls'] == 0
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_bad_input_writes_failure_report(tmp_path, tools):
    out = tmp_path / 'result'
    assert main(['--input', str(tmp_path / 'missing.mp4'), '--langs', 'hi', '--out', str(out), '--prepare-only', '--ffmpeg', tools[0], '--ffprobe', tools[1]]) == 1
    report = json.loads((out / 'report.json').read_text())
    assert report['status'] == 'failed'
    assert report['error']['code'] == 'missing_input'


def test_no_full_dubbing_success_claim(tmp_path):
    assert main(['--input', 'input.mp4', '--langs', 'hi', '--out', str(tmp_path)]) == 1


@pytest.mark.parametrize('timeout', ['0', '-1', 'nan', 'inf'])
def test_reject_invalid_timeout(tmp_path, timeout):
    with pytest.raises(SystemExit) as error:
        main(['--input', 'input.mp4', '--langs', 'hi', '--out', str(tmp_path), '--prepare-only', '--media-timeout', timeout])
    assert error.value.code == 2
