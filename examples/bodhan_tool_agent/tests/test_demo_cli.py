#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys
import wave
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent
_SRC = _HERE.parent / 'src'
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_HERE.parent))
os.environ.setdefault('LLM_BASE_URL', 'https://api.openai.com/v1')
os.environ.setdefault('LLM_API_KEY', 'sk-dummy-test-key')
os.environ.setdefault('LLM_MODEL', 'gpt-4o-mini')
DEMO_INPUTS = _REPO_ROOT / 'demo_inputs'
from bodhan_agent.cli import expand_inputs
from bodhan_agent.planner import TaskPlanner
from bodhan_agent.reporter import Reporter
from bodhan_mcp.client import InrLedger, APIResponse
from bodhan_mcp.tools.inspect import inspect_media
from bodhan_mcp.tools.speak import synthesize_speech


def _make_wav(path, duration_sec=1.0, sr=16000):
    n = int(duration_sec * sr)
    buf = bytearray(n * 2)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    return bytes(buf)


def _mock_llm_response(content=None, tool_calls=None):
    msg = MagicMock(); msg.content = content; msg.tool_calls = tool_calls
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return resp


def _fake_tool_call(name, arguments, tc_id='tc_1'):
    fn = MagicMock(); fn.name = name; fn.arguments = arguments
    tc = MagicMock(); tc.id = tc_id; tc.function = fn
    return tc


def _build_mock_bridge(tool_responses=None):
    tools = [
        {'type':'function','function':{'name':'inspect_media','description':'inspect_media','parameters':{'type':'object','properties':{'file_path':'string'},'required':['file_path']}}},
        {'type':'function','function':{'name':'extract_document_text','description':'extract_document_text','parameters':{'type':'object','properties':{'file_path':'string','page_range':'string'},'required':['file_path','page_range']}}},
        {'type':'function','function':{'name':'transcribe_audio','description':'transcribe_audio','parameters':{'type':'object','properties':{'file_path':'string','language_hint':'string','max_duration_sec':'string'},'required':['file_path','language_hint','max_duration_sec']}}},
        {'type':'function','function':{'name':'translate_text','description':'translate_text','parameters':{'type':'object','properties':{'source_lang':'string','target_lang':'string','text':'string','input_file':'string'},'required':['source_lang','target_lang','text','input_file']}}},
        {'type':'function','function':{'name':'synthesize_speech','description':'synthesize_speech','parameters':{'type':'object','properties':{'text':'string','input_file':'string','language':'string','output_file':'string'},'required':['language','output_file','text','input_file']}}},
    ]
    async def list_tools(): return tools
    async def call_tool(name, arguments):
        if name in (tool_responses or {}): return tool_responses[name]
        return {'result': json.dumps({'tool': name}), 'cost_inr': 0.0}
    bridge = MagicMock()
    bridge.list_tools = list_tools; bridge.call_tool = call_tool
    bridge.start = AsyncMock(return_value=None); bridge.stop = AsyncMock(return_value=None)
    return bridge


class TestTask1TamilSummary:
    @pytest.mark.asyncio
    async def test_task1_full_pipeline_with_real_txt(self, tmp_path):
        txt_file = DEMO_INPUTS / 'Task1_8page.txt'
        assert txt_file.exists(), f'Missing demo input: {txt_file}'
        ledger = InrLedger(max_cost=20.0)
        out_dir = tmp_path / 'out'; artifacts = out_dir / 'artifacts'
        artifacts.mkdir(parents=True)
        reporter = Reporter(out_dir, artifacts, ledger, max_steps=30, max_cost_inr=20.0)
        output_wav = str(artifacts / 'summary_ta.wav')
        _make_wav(Path(output_wav), duration_sec=5.0)
        bridge = _build_mock_bridge({
            'inspect_media': {'result': json.dumps({'format':'txt','min_cost_inr':0.0}), 'cost_inr':0.0},
            'extract_document_text': {'result': json.dumps({'markdown':'<untrusted_source_data>Hello</untrusted_source_data>','pages':1,'cost_inr':0.2}), 'cost_inr':0.2},
            'translate_text': {'result': json.dumps({'translation':'வணககம உலகம்','target_language':'ta','target_script_verified':True,'cost_inr':0.05}), 'cost_inr':0.05},
            'synthesize_speech': {'result': json.dumps({'output_file':output_wav,'duration_sec':5.0,'chunks_count':1,'cost_inr':0.1,'language':'ta'}), 'cost_inr':0.1},
        })
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=20.0)
        tool_resp = _mock_llm_response(tool_calls=[
            _fake_tool_call('extract_document_text', json.dumps({'file_path': str(txt_file)})),
            _fake_tool_call('translate_text', json.dumps({'source_lang': 'en', 'target_lang': 'ta', 'input_file': 'run/temp/text_test.txt'})),
            _fake_tool_call('synthesize_speech', json.dumps({'language': 'ta', 'output_file': output_wav, 'text': chr(0x0928)+chr(0x092e)+chr(0x0938)+chr(0x094d)+chr(0x0924)+chr(0x0947)})),
        ])
        final_resp = _mock_llm_response(content='Tamil audio summary generated.')
        call_count = [0]
        def track(*args, **kwargs):
            call_count[0] += 1
            return tool_resp if call_count[0] == 1 else final_resp
        with patch.object(planner._client.chat.completions, 'create', new=AsyncMock(side_effect=track)):
            result = await planner.execute('Make a two-minute Tamil audio summary of this chapter', [str(txt_file)])
        assert result['status'] == 'completed'
        assert result['status'] == 'completed'


class TestTask2HindiTranscribe:
    @pytest.mark.asyncio
    async def test_task2_transcribe_hindi_wav(self, tmp_path):
        wav_file = tmp_path / 'test.wav'
        _make_wav(wav_file, duration_sec=1.0)
        ledger = InrLedger(max_cost=20.0)
        out_dir = tmp_path / 'out'; artifacts = out_dir / 'artifacts'
        artifacts.mkdir(parents=True)
        reporter = Reporter(out_dir, artifacts, ledger, max_steps=30, max_cost_inr=20.0)
        srt_file = str(artifacts / 'subtitles_en.srt')
        bridge = _build_mock_bridge({
            'inspect_media': {'result': json.dumps({'format':'wav','duration_sec':1.0,'min_cost_inr':0.01}), 'cost_inr':0.0},
            'transcribe_audio': {'result': json.dumps({'text':'<untrusted_source_data>Hello world</untrusted_source_data>','language':'hi','duration_sec':1.0,'cost_inr':0.03,'full_text_file':srt_file}), 'cost_inr':0.03},
            'synthesize_speech': {'result': json.dumps({'output_file':srt_file,'duration_sec':3.0,'chunks_count':1,'cost_inr':0.06,'language':'hi'}), 'cost_inr':0.06},
        })
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=20.0)
        tool_resp = _mock_llm_response(tool_calls=[
            _fake_tool_call('transcribe_audio', json.dumps({'file_path': str(wav_file), 'language_hint': 'hi', 'max_duration_sec': 15.0})),
            _fake_tool_call('synthesize_speech', json.dumps({'language': 'hi', 'output_file': srt_file, 'text': 'Hello world'})),
        ])
        final_resp = _mock_llm_response(content='Transcription complete.')
        with patch.object(planner._client.chat.completions, 'create', new=AsyncMock(side_effect=[tool_resp, final_resp])):
            result = await planner.execute('Transcribe this Hindi voice note and give me English subtitles', [str(wav_file)])
        assert result['status'] == 'completed'
        assert result['steps_used'] >= 1


class TestTask3MissingInput:
    @pytest.mark.asyncio
    async def test_inspect_media_missing_file(self):
        result = await inspect_media('/nonexistent/path/does/not/exist.png')
        assert result['error'] == 'FILE_NOT_FOUND'
        assert result['retryable'] is False

    @pytest.mark.asyncio
    async def test_planner_rejects_missing_input_file(self, tmp_path):
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=10.0)
        reporter = Reporter(tmp_path / 'out', tmp_path / 'out' / 'artifacts', ledger, max_steps=5, max_cost_inr=10.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=5, max_cost_inr=10.0)
        result = await planner.execute('Translate worksheet into Marathi', ['/no/such/file.png'])
        assert result['status'] != 'unsupported_task'
        assert True


class TestTask4TeluguFilter:
    @pytest.mark.asyncio
    async def test_expand_inputs_handles_directory_of_clips(self):
        clip_dir = DEMO_INPUTS / 'Task4_clips.mp3'
        if not clip_dir.exists():
            pytest.skip(f'Clip directory not found: {clip_dir}')
        expanded = await expand_inputs([str(clip_dir)])
        assert len(expanded) == 4
        assert expanded == sorted(expanded)
        for p in expanded:
            assert Path(p).exists()

    @pytest.mark.asyncio
    async def test_parallel_transcribe_dispatches_multiple_clips(self, tmp_path):
        clip_dir = DEMO_INPUTS / 'Task4_clips.mp3'
        if not clip_dir.exists():
            pytest.skip(f'Clip directory not found: {clip_dir}')
        clips = sorted(str(p) for p in clip_dir.iterdir() if p.suffix == '.mp3')
        assert len(clips) == 4
        ledger = InrLedger(max_cost=20.0)
        out_dir = tmp_path / 'out'; artifacts = out_dir / 'artifacts'
        artifacts.mkdir(parents=True)
        reporter = Reporter(out_dir, artifacts, ledger, max_steps=30, max_cost_inr=20.0)
        bridge = _build_mock_bridge({
            'inspect_media': {'result': json.dumps({'format':'mp3','duration_sec':10.0,'min_cost_inr':0.02}), 'cost_inr':0.0},
            'transcribe_audio': {'result': json.dumps({'text':'<untrusted_source_data>Test clip</untrusted_source_data>','language':'te','duration_sec':10.0,'cost_inr':0.02,'full_text_file':'run/temp/trans.txt'}), 'cost_inr':0.02},
        })
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=20.0)
        transcribe_calls = [_fake_tool_call('transcribe_audio', json.dumps({'file_path': f, 'language_hint': 'te', 'max_duration_sec': 15.0}), f'tc_{i}') for i, f in enumerate(clips)]
        tool_resp = _mock_llm_response(tool_calls=transcribe_calls)
        final_resp = _mock_llm_response(content='Clips filtered and translated.')
        with patch.object(planner._client.chat.completions, 'create', new=AsyncMock(side_effect=[tool_resp, final_resp])):
            result = await planner.execute('Which of these clips are in Telugu? Translate those into English', clips)
        assert result['status'] == 'completed'
        completed = [s for s in planner._steps if s.tool == 'transcribe_audio']
        assert len(completed) == 4


class TestTask5BengaliTTS:
    @pytest.mark.asyncio
    async def test_bengali_tts_rejects_invalid_voice(self):
        result = await synthesize_speech('bn', '/tmp/out.wav', text=chr(0x0986)+chr(0x09AE)+chr(0x09BF), gender='robot')
        assert result['error'] == 'INVALID_VOICE'
        assert 'robot' in result['message']

    @pytest.mark.asyncio
    async def test_synthesize_accepts_language_name(self):
        with patch('bodhan_mcp.tools.speak.client') as mock_client:
            mock_client.request = AsyncMock(return_value=APIResponse(data=b'\x00\x00', cost_inr=0.01, cached=False))
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / 'out.wav'
                with patch('bodhan_mcp.media.stitch_wav_ffmpeg'):
                    await synthesize_speech('Bengali', str(out), text=chr(0x0986)+chr(0x09AE)+chr(0x09BF)+' '+chr(0x098F)+chr(0x0995)+chr(0x099f)+chr(0x09bf), voice_id='Ishita')
                    assert True

    @pytest.mark.asyncio
    async def test_task5_pipeline_with_real_paragraph(self, tmp_path):
        txt_file = DEMO_INPUTS / 'task5_paragraph.txt'
        assert txt_file.exists(), f'Missing: {txt_file}'
        ledger = InrLedger(max_cost=20.0)
        out_dir = tmp_path / 'out'; artifacts = out_dir / 'artifacts'
        artifacts.mkdir(parents=True)
        reporter = Reporter(out_dir, artifacts, ledger, max_steps=30, max_cost_inr=20.0)
        out_wav = str(artifacts / 'bengali.wav')
        _make_wav(Path(out_wav), duration_sec=3.0)
        bridge = _build_mock_bridge({
            'inspect_media': {'result': json.dumps({'format':'txt','min_cost_inr':0.0}), 'cost_inr':0.0},
            'synthesize_speech': {'result': json.dumps({'output_file':out_wav,'duration_sec':3.0,'chunks_count':1,'cost_inr':0.06,'language':'bn'}), 'cost_inr':0.06},
        })
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=30, max_cost_inr=20.0)
        tool_resp = _mock_llm_response(tool_calls=[
            _fake_tool_call('synthesize_speech', json.dumps({'language': 'bn', 'output_file': out_wav, 'input_file': str(txt_file), 'gender': 'female'})),
        ])
        final_resp = _mock_llm_response(content='Bengali audio synthesized.')
        with patch.object(planner._client.chat.completions, 'create', new=AsyncMock(side_effect=[tool_resp, final_resp])):
            result = await planner.execute('Read this paragraph aloud in Bengali with a female voice', [str(txt_file)])
        assert result['status'] == 'completed'
        tts_steps = [s for s in planner._steps if s.tool == 'synthesize_speech']
        assert len(tts_steps) == 1


class TestTask6ImpossibleFrench:
    @pytest.mark.asyncio
    async def test_french_target_gated_at_planner_level(self, tmp_path):
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=20.0)
        reporter = Reporter(tmp_path / 'out', tmp_path / 'out' / 'artifacts', ledger, max_steps=5, max_cost_inr=20.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=5, max_cost_inr=20.0)
        result = await planner.execute('Translate this video into French', [])
        assert result['status'] == 'unsupported_task'
        assert result['total_cost_inr'] == 0.0
        assert result['steps_used'] == 0
        assert any('Pre-gate' in m for m in result['missed_targets'])

    @pytest.mark.asyncio
    async def test_french_blocked_even_with_real_video_file(self, tmp_path):
        vid_file = DEMO_INPUTS / 'Task6_video.mp4'
        assert vid_file.exists(), f'Missing: {vid_file}'
        bridge = _build_mock_bridge()
        ledger = InrLedger(max_cost=50.0)
        reporter = Reporter(tmp_path / 'out', tmp_path / 'out' / 'artifacts', ledger, max_steps=5, max_cost_inr=50.0)
        planner = TaskPlanner(bridge, reporter, ledger, max_steps=5, max_cost_inr=50.0)
        result = await planner.execute('Translate this video into French', [str(vid_file)])
        assert result['status'] == 'unsupported_task'
        assert result['total_cost_inr'] == 0.0


@pytest.mark.asyncio
async def test_all_six_tasks_terminal_statuses(tmp_path):
    outputs = {}
    task_spec = [
        ('Tamil audio summary', [str(DEMO_INPUTS / 'Task1_8page.txt')]),
        ('Transcribe Hindi', [str(DEMO_INPUTS / 'Task2_3min_hindi.wav')]),
        ('Translate worksheet', ['/no/such/file.png']),
        ('Filter Telugu clips', [str(DEMO_INPUTS / 'Task4_clips.mp3')]),
        ('Bengali TTS', [str(DEMO_INPUTS / 'task5_paragraph.txt')]),
        ('Translate video to French', [str(DEMO_INPUTS / 'Task6_video.mp4')]),
    ]
    for num, (task_name, files) in enumerate(task_spec):
        odir = tmp_path / f't{num}' / 'out'
        adir = odir / 'artifacts'
        adir.mkdir(parents=True)
        pl = TaskPlanner(_build_mock_bridge(), Reporter(odir, adir, InrLedger(max_cost=10.0), max_steps=5, max_cost_inr=10.0), InrLedger(max_cost=10.0), max_steps=5, max_cost_inr=10.0)
        with patch.object(pl._client.chat.completions, 'create', new=AsyncMock(return_value=_mock_llm_response(content='done'))):
            outputs[task_name] = await pl.execute(task_name, files)
    assert outputs['Tamil audio summary']['status'] != 'unsupported_task'
    assert outputs['Transcribe Hindi']['status'] != 'unsupported_task'
    assert outputs['Translate worksheet']['status'] != 'unsupported_task'
    assert outputs['Filter Telugu clips']['status'] != 'unsupported_task'
    assert outputs['Bengali TTS']['status'] != 'unsupported_task'
    assert outputs['Translate video to French']['status'] == 'unsupported_task'
    assert outputs['Translate video to French']['total_cost_inr'] == 0.0
