// ─────────────────────────────────────────────────────────────
// useAudioRecorder — Records audio for STT
// Records in WAV format, chunks at 30s (Bodhan STT limit)
// ─────────────────────────────────────────────────────────────

import { useState, useRef, useCallback } from 'react';
import { Audio } from 'expo-av';

interface AudioRecorderState {
  isRecording: boolean;
  /** URI of the last recorded audio file */
  recordingUri: string | null;
  /** Duration of the recording in milliseconds */
  durationMs: number;
  error: string | null;
}

interface AudioRecorderActions {
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<string | null>;
  cancelRecording: () => Promise<void>;
}

const MAX_DURATION_MS = 30_000; // Bodhan API limit: 30 seconds

export function useAudioRecorder(): AudioRecorderState & AudioRecorderActions {
  const [state, setState] = useState<AudioRecorderState>({
    isRecording: false,
    recordingUri: null,
    durationMs: 0,
    error: null,
  });

  const recordingRef = useRef<Audio.Recording | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

  const cleanup = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startRecording = useCallback(async () => {
    try {
      // Request permissions
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        setState(prev => ({ ...prev, error: 'Microphone permission denied' }));
        return;
      }

      // Configure audio mode for recording
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      // Create and start recording
      const { recording } = await Audio.Recording.createAsync(
        {
          isMeteringEnabled: true,
          android: {
            extension: '.wav',
            outputFormat: Audio.AndroidOutputFormat.DEFAULT,
            audioEncoder: Audio.AndroidAudioEncoder.DEFAULT,
            sampleRate: 16000,
            numberOfChannels: 1,
            bitRate: 256000,
          },
          ios: {
            extension: '.wav',
            outputFormat: Audio.IOSOutputFormat.LINEARPCM,
            audioQuality: Audio.IOSAudioQuality.HIGH,
            sampleRate: 16000,
            numberOfChannels: 1,
            bitRate: 256000,
            linearPCMBitDepth: 16,
            linearPCMIsBigEndian: false,
            linearPCMIsFloat: false,
          },
          web: {
            mimeType: 'audio/wav',
            bitsPerSecond: 256000,
          },
        }
      );

      recordingRef.current = recording;
      startTimeRef.current = Date.now();

      setState({
        isRecording: true,
        recordingUri: null,
        durationMs: 0,
        error: null,
      });

      // Update duration timer
      timerRef.current = setInterval(() => {
        const elapsed = Date.now() - startTimeRef.current;
        setState(prev => ({ ...prev, durationMs: elapsed }));

        // Auto-stop at 30 seconds
        if (elapsed >= MAX_DURATION_MS) {
          stopRecording();
        }
      }, 100);
    } catch (error) {
      setState(prev => ({
        ...prev,
        isRecording: false,
        error: `Recording failed: ${(error as Error).message}`,
      }));
    }
  }, []);

  const stopRecording = useCallback(async (): Promise<string | null> => {
    cleanup();

    const recording = recordingRef.current;
    if (!recording) return null;

    try {
      await recording.stopAndUnloadAsync();
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false });

      const uri = recording.getURI();
      recordingRef.current = null;

      setState(prev => ({
        ...prev,
        isRecording: false,
        recordingUri: uri,
      }));

      return uri;
    } catch (error) {
      setState(prev => ({
        ...prev,
        isRecording: false,
        error: `Stop recording failed: ${(error as Error).message}`,
      }));
      return null;
    }
  }, [cleanup]);

  const cancelRecording = useCallback(async () => {
    cleanup();

    const recording = recordingRef.current;
    if (recording) {
      try {
        await recording.stopAndUnloadAsync();
      } catch {}
      recordingRef.current = null;
    }

    setState({
      isRecording: false,
      recordingUri: null,
      durationMs: 0,
      error: null,
    });
  }, [cleanup]);

  return {
    ...state,
    startRecording,
    stopRecording,
    cancelRecording,
  };
}

