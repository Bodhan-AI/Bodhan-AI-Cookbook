"""CPU-only speech-region detection, not ASR, diarization, or alignment."""

from dataclasses import asdict, dataclass
import importlib.metadata
import math
from pathlib import Path
import time
import wave

from .media import MediaError


SAMPLE_RATE = 16000


@dataclass(frozen=True)
class VADSettings:
    threshold: float = 0.5
    min_speech_ms: int = 150
    min_silence_ms: int = 300
    speech_pad_ms: int = 50

    def __post_init__(self):
        if not math.isfinite(self.threshold) or not 0 < self.threshold < 1:
            raise MediaError('invalid_vad_settings', 'VAD threshold must be strictly between zero and one.')
        for name in ('min_speech_ms', 'min_silence_ms', 'speech_pad_ms'):
            value = getattr(self, name)
            if type(value) is not int or value < 0 or value > 10000:
                raise MediaError('invalid_vad_settings', 'VAD duration settings must be integers from 0 to 10000 milliseconds.')
        if self.min_speech_ms == 0 or self.min_silence_ms == 0:
            raise MediaError('invalid_vad_settings', 'Minimum speech and silence must be positive.')


def timeline_regions(spans: list[dict], frames: int) -> tuple[list[dict], list[dict]]:
    """Validate model intervals and derive their complement; end is exclusive."""
    speech, gaps = [], []
    cursor = 0

    def region(start, end, identifier):
        return {'id': identifier, 'start_sample': start, 'end_sample': end,
                'start_seconds': start / SAMPLE_RATE, 'end_seconds': end / SAMPLE_RATE}

    for span in spans:
        if not isinstance(span, dict):
            raise MediaError('invalid_vad_output', 'Detector returned an invalid speech interval.')
        start, end = span.get('start'), span.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= cursor <= start < end <= frames:
            raise MediaError('invalid_vad_output', 'Detector returned overlapping, unordered, or out-of-range speech intervals.')
        if cursor < start:
            gaps.append(region(cursor, start, f'gap-{len(gaps)+1:04d}'))
        speech.append(region(start, end, f'speech-{len(speech)+1:04d}'))
        cursor = end
    if cursor < frames:
        gaps.append(region(cursor, frames, f'gap-{len(gaps)+1:04d}'))
    return speech, gaps


def detect_speech(source: Path, settings: VADSettings | None = None) -> dict:
    settings = settings or VADSettings()
    started = time.monotonic()
    try:
        with wave.open(str(source), 'rb') as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (SAMPLE_RATE, 1, 2, 'NONE'):
                raise MediaError('invalid_vad_audio', 'Speech detection requires 16 kHz mono PCM16 WAV from the preparation stage.')
            frames = audio.getnframes()
            if not 0 < frames <= SAMPLE_RATE * 900:
                raise MediaError('invalid_vad_audio', 'Speech-detection audio must be nonempty and at most 15 minutes.')
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise MediaError('invalid_vad_audio', 'The WAV samples are truncated.')
    except (wave.Error, EOFError, OSError) as exc:
        raise MediaError('invalid_vad_audio', 'Cannot read the prepared WAV for speech detection.') from exc
    try:
        import numpy as np
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError as exc:
        raise MediaError('missing_vad_dependency', 'Install the pinned recipe requirements to enable Silero CPU speech detection.') from exc
    try:
        # Explicit little-endian interpretation and amplitude scaling, not ASR.
        samples = torch.from_numpy(np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0)
        torch.set_num_threads(1)
        model = load_silero_vad().to('cpu')
        with torch.inference_mode():
            spans = get_speech_timestamps(samples, model, sampling_rate=SAMPLE_RATE,
                threshold=settings.threshold, min_speech_duration_ms=settings.min_speech_ms,
                min_silence_duration_ms=settings.min_silence_ms, speech_pad_ms=settings.speech_pad_ms,
                return_seconds=False)
    except Exception as exc:
        raise MediaError('vad_inference_failed', 'Silero could not process the audio; check the installed model/runtime and input format.') from exc
    speech, gaps = timeline_regions(spans, frames)
    duration = frames / SAMPLE_RATE
    speech_seconds = sum(r['end_sample'] - r['start_sample'] for r in speech) / SAMPLE_RATE
    return {'schema_version': 1, 'audio_path': str(source.resolve()), 'sample_rate': SAMPLE_RATE, 'total_samples': frames,
            'duration_seconds': duration, 'timeline_origin': 'first_video_frame', 'end_sample_is_exclusive': True,
            'detector': {'name': 'silero-vad', 'version': importlib.metadata.version('silero-vad'),
                         'torch_version': torch.__version__, 'device': 'cpu', 'settings': asdict(settings)},
            'speech_regions': speech, 'non_speech_regions': gaps,
            'summary': {'speech_region_count': len(speech), 'speech_seconds': speech_seconds,
                        'non_speech_seconds': (frames - sum(r['end_sample']-r['start_sample'] for r in speech))/SAMPLE_RATE,
                        'longest_speech_region_seconds': max((r['end_seconds']-r['start_seconds'] for r in speech), default=0)},
            'latency_seconds': round(time.monotonic()-started, 3),
            'warnings': ([] if speech else ['No speech detected; this may be a false negative and does not prove the input has no speech.']),
            'limitations': ['Regions are padded VAD estimates, not exact word/sentence timestamps or speaker labels.',
                            'Non-speech means not detected as speech, not verified silence or separated music.',
                            'Long regions are not yet cut into <=30-second transcription requests.',
                            'Detection accuracy on accents, music and overlaps is not established by timeline validation.']}
