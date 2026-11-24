// ─────────────────────────────────────────────────────────────
// useAudioPlayer — Plays TTS audio files
// ─────────────────────────────────────────────────────────────

import { useState, useRef, useCallback, useEffect } from 'react';
import { Audio, AVPlaybackStatus } from 'expo-av';

interface AudioPlayerState {
  isPlaying: boolean;
  isLoading: boolean;
  durationMs: number;
  positionMs: number;
  error: string | null;
}

interface AudioPlayerActions {
  play: (uri: string) => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  stop: () => Promise<void>;
}

export function useAudioPlayer(): AudioPlayerState & AudioPlayerActions {
  const [state, setState] = useState<AudioPlayerState>({
    isPlaying: false,
    isLoading: false,
    durationMs: 0,
    positionMs: 0,
    error: null,
  });

  const soundRef = useRef<Audio.Sound | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      soundRef.current?.unloadAsync().catch(() => {});
    };
  }, []);

  const onPlaybackStatusUpdate = useCallback((status: AVPlaybackStatus) => {
    if (!status.isLoaded) {
      if (status.error) {
        setState(prev => ({ ...prev, error: status.error!, isPlaying: false, isLoading: false }));
      }
      return;
    }

    setState(prev => ({
      ...prev,
      isPlaying: status.isPlaying,
      isLoading: false,
      durationMs: status.durationMillis ?? 0,
      positionMs: status.positionMillis ?? 0,
    }));

    // Reset when playback finishes
    if (status.didJustFinish) {
      setState(prev => ({
        ...prev,
        isPlaying: false,
        positionMs: 0,
      }));
    }
  }, []);

  const play = useCallback(async (uri: string) => {
    try {
      // Unload any previous sound
      if (soundRef.current) {
        await soundRef.current.unloadAsync();
        soundRef.current = null;
      }

      setState(prev => ({ ...prev, isLoading: true, error: null }));

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: false,
        playsInSilentModeIOS: true,
      });

      const { sound } = await Audio.Sound.createAsync(
        { uri },
        { shouldPlay: true },
        onPlaybackStatusUpdate
      );

      soundRef.current = sound;
    } catch (error) {
      setState(prev => ({
        ...prev,
        isLoading: false,
        isPlaying: false,
        error: `Playback failed: ${(error as Error).message}`,
      }));
    }
  }, [onPlaybackStatusUpdate]);

  const pause = useCallback(async () => {
    try {
      await soundRef.current?.pauseAsync();
    } catch (error) {
      setState(prev => ({ ...prev, error: `Pause failed: ${(error as Error).message}` }));
    }
  }, []);

  const resume = useCallback(async () => {
    try {
      await soundRef.current?.playAsync();
    } catch (error) {
      setState(prev => ({ ...prev, error: `Resume failed: ${(error as Error).message}` }));
    }
  }, []);

  const stop = useCallback(async () => {
    try {
      if (soundRef.current) {
        await soundRef.current.stopAsync();
        await soundRef.current.unloadAsync();
        soundRef.current = null;
      }
      setState(prev => ({
        ...prev,
        isPlaying: false,
        positionMs: 0,
      }));
    } catch (error) {
      setState(prev => ({ ...prev, error: `Stop failed: ${(error as Error).message}` }));
    }
  }, []);

  return {
    ...state,
    play,
    pause,
    resume,
    stop,
  };
}

