import React, { useState, useRef } from 'react';
import { Mic, Square, Play, RotateCcw, Volume2, Sparkles, AlertCircle, Loader2 } from 'lucide-react';
import { WavAudioRecorder, RecordingResult } from '../../services/audioRecorder';
import { aiApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';

interface Props {
  languageCode: string;
  languageName: string;
  onTranscriptionSuccess: (originalText: string, englishText: string, audioPath?: string) => void;
}

type RecordingState =
  | 'ready'
  | 'recording'
  | 'recorded'
  | 'processing'
  | 'transcribed'
  | 'error';

export const AudioRecorderComponent: React.FC<Props> = ({
  languageCode,
  languageName,
  onTranscriptionSuccess,
}) => {
  const { showToast } = useToast();
  const [status, setStatus] = useState<RecordingState>('ready');
  const [seconds, setSeconds] = useState(0);
  const [recordingResult, setRecordingResult] = useState<RecordingResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const recorderRef = useRef<WavAudioRecorder | null>(null);
  const audioPlayerRef = useRef<HTMLAudioElement | null>(null);

  const startRecording = async () => {
    try {
      setErrorMessage(null);
      setSeconds(0);
      const recorder = new WavAudioRecorder();
      recorderRef.current = recorder;
      await recorder.start((sec) => setSeconds(sec));
      setStatus('recording');
    } catch (err: any) {
      console.error('Microphone permission error', err);
      setErrorMessage(err.message || 'Microphone access denied. Please grant browser microphone permission.');
      setStatus('error');
      showToast('error', 'Microphone Error', 'Please enable microphone access in your browser.');
    }
  };

  const stopRecording = async () => {
    if (!recorderRef.current) return;
    try {
      const res = await recorderRef.current.stop();
      setRecordingResult(res);
      setAudioUrl(res.url);
      setStatus('recorded');
    } catch (err: any) {
      console.error('Stop recording error', err);
      setErrorMessage('Failed to finalize audio recording.');
      setStatus('error');
    }
  };

  const retakeRecording = () => {
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
    setRecordingResult(null);
    setAudioUrl(null);
    setSeconds(0);
    setStatus('ready');
    setErrorMessage(null);
  };

  const processWithBodhanAI = async () => {
    if (!recordingResult) return;

    setStatus('processing');
    setErrorMessage(null);

    try {
      showToast('info', 'Processing Audio', 'Sending to Bodhan AI Indic-Transcribe...');
      
      // 1. Transcribe audio using official Bodhan AI Indic-Transcribe
      const transcribeRes = await aiApi.transcribe(recordingResult.blob, languageCode);

      if (!transcribeRes.success && !transcribeRes.text) {
        throw new Error(transcribeRes.error || transcribeRes.details || 'Transcription failed from Bodhan AI.');
      }

      const transcriptionText = transcribeRes.text || 'No speech detected';
      const audioFilePath = transcribeRes.audio_file_path;

      // 2. Translate using Bodhan AI Indic-Translate
      showToast('info', 'Translating Observation', `Translating from ${languageName} to English...`);
      const translateRes = await aiApi.translate(transcriptionText, languageCode, 'en');

      const englishText = translateRes.translated_text || transcriptionText;

      setStatus('transcribed');
      showToast('success', 'Voice Processed', 'Transcription and translation successfully generated!');
      onTranscriptionSuccess(transcriptionText, englishText, audioFilePath);
    } catch (err: any) {
      console.error('Bodhan AI processing error', err);
      const msg = err.message || 'Failed to process voice recording with Bodhan AI.';
      setErrorMessage(msg);
      setStatus('error');
      showToast('error', 'Bodhan AI Error', msg);
    }
  };

  return (
    <div className="bg-white rounded-3xl p-6 sm:p-8 border border-emerald-900/10 shadow-sm text-center">
      {/* Language Indicator */}
      <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-[#EAF5EC] text-[#174D35] text-xs font-semibold mb-6">
        <Sparkles className="w-3.5 h-3.5 text-[#268653]" />
        <span>Target Language: {languageName} ({languageCode.toUpperCase()})</span>
      </div>

      {/* Main Microphone Action Center */}
      <div className="flex flex-col items-center justify-center my-4">
        {status === 'ready' && (
          <div className="flex flex-col items-center">
            <button
              onClick={startRecording}
              className="w-24 h-24 rounded-full bg-gradient-to-tr from-[#174D35] to-[#268653] hover:scale-105 active:scale-95 text-white flex items-center justify-center shadow-xl shadow-[#268653]/30 transition-all cursor-pointer group"
            >
              <Mic className="w-10 h-10 group-hover:scale-110 transition-transform" />
            </button>
            <p className="mt-4 text-base font-bold text-[#18251D]">Tap to Start Speaking</p>
            <p className="text-xs text-[#738076] mt-1 max-w-xs">
              Describe crop symptoms clearly in {languageName}. (Up to 30s)
            </p>
          </div>
        )}

        {status === 'recording' && (
          <div className="flex flex-col items-center">
            <div className="relative">
              <div className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-40"></div>
              <button
                onClick={stopRecording}
                className="relative w-24 h-24 rounded-full bg-red-600 hover:bg-red-700 text-white flex items-center justify-center shadow-xl shadow-red-500/30 transition-all cursor-pointer"
              >
                <Square className="w-9 h-9 fill-white" />
              </button>
            </div>
            <div className="mt-4 flex items-center gap-2 text-red-600 font-bold">
              <span className="w-2.5 h-2.5 rounded-full bg-red-600 animate-pulse"></span>
              <span>Recording... {seconds}s / 30s</span>
            </div>
            <p className="text-xs text-[#738076] mt-1">Tap stop when finished</p>
          </div>
        )}

        {status === 'recorded' && (
          <div className="flex flex-col items-center w-full max-w-md">
            <div className="w-full bg-[#F7F9F6] p-4 rounded-2xl border border-gray-200/80 mb-5 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-[#268653] text-white flex items-center justify-center">
                  <Volume2 className="w-5 h-5" />
                </div>
                <div className="text-left">
                  <div className="text-sm font-bold text-[#18251D]">Audio Clip Recorded</div>
                  <div className="text-xs text-[#738076]">{seconds} seconds • 16kHz PCM WAV</div>
                </div>
              </div>
              {audioUrl && (
                <audio ref={audioPlayerRef} controls src={audioUrl} className="h-9 max-w-[180px]" />
              )}
            </div>

            <div className="flex items-center gap-3 w-full">
              <button
                onClick={retakeRecording}
                className="flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50 transition-all"
              >
                <RotateCcw className="w-4 h-4" />
                Retake
              </button>
              <button
                onClick={processWithBodhanAI}
                className="flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl bg-gradient-to-r from-[#174D35] to-[#268653] text-white text-sm font-semibold hover:shadow-lg transition-all"
              >
                <Sparkles className="w-4 h-4" />
                Analyze with Bodhan AI
              </button>
            </div>
          </div>
        )}

        {status === 'processing' && (
          <div className="flex flex-col items-center py-6">
            <Loader2 className="w-12 h-12 text-[#268653] animate-spin mb-4" />
            <h4 className="text-base font-bold text-[#18251D]">Transcribing & Translating...</h4>
            <p className="text-xs text-[#738076] mt-1 max-w-xs">
              Sending audio to Bodhan AI Indic-Transcribe & Indic-Translate models.
            </p>
          </div>
        )}

        {status === 'transcribed' && (
          <div className="flex flex-col items-center w-full max-w-md">
            <div className="p-3 bg-emerald-50 text-[#174D35] rounded-xl text-xs font-semibold mb-4 w-full text-center">
              Voice recording analyzed! Scroll down to review and complete crop details.
            </div>
            <button
              onClick={retakeRecording}
              className="text-xs font-semibold text-[#268653] hover:underline flex items-center gap-1"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Record a different observation
            </button>
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-center max-w-md">
            <div className="p-4 bg-red-50 border border-red-200 rounded-2xl text-red-900 text-xs text-left mb-4 flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
              <div>
                <strong className="block font-semibold">Recording or AI Analysis Note</strong>
                {errorMessage}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={retakeRecording}
                className="px-4 py-2.5 rounded-xl bg-[#268653] text-white text-sm font-semibold hover:bg-[#174D35]"
              >
                Try Again
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
