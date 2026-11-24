// File: src/components/AudioPlaybackBar.tsx
import React, { useState, useEffect, useRef } from 'react';
import { Play, Pause, RotateCcw, Volume2, Gauge } from 'lucide-react';

interface AudioPlaybackBarProps {
  audioUrl?: string;
  voiceName: string;
  languageCode: string;
  autoPlay?: boolean;
}

export const AudioPlaybackBar: React.FC<AudioPlaybackBarProps> = ({
  audioUrl,
  voiceName,
  languageCode,
  autoPlay = false
}) => {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1.0);

  useEffect(() => {
    if (audioUrl) {
      if (audioRef.current) {
        audioRef.current.pause();
      }
      setIsPlaying(false);
      setCurrentTime(0);
      const audio = new Audio(audioUrl);
      audioRef.current = audio;
      audio.playbackRate = playbackSpeed;

      audio.onloadedmetadata = () => {
        setDuration(audio.duration || 10);
      };

      audio.ontimeupdate = () => {
        setCurrentTime(audio.currentTime);
      };

      audio.onended = () => {
        setIsPlaying(false);
        setCurrentTime(0);
      };

      if (autoPlay) {
        audio.play().then(() => setIsPlaying(true)).catch(() => {});
      }

      return () => {
        audio.pause();
      };
    }
  }, [audioUrl]);

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
    } else {
      audioRef.current.play().then(() => setIsPlaying(true)).catch(() => {});
    }
  };

  const handleRestart = () => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = 0;
    audioRef.current.play().then(() => setIsPlaying(true)).catch(() => {});
  };

  const toggleSpeed = () => {
    const nextSpeed = playbackSpeed === 1.0 ? 1.25 : 1.0;
    setPlaybackSpeed(nextSpeed);
    if (audioRef.current) {
      audioRef.current.playbackRate = nextSpeed;
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const seekTo = parseFloat(e.target.value);
    if (audioRef.current) {
      audioRef.current.currentTime = seekTo;
      setCurrentTime(seekTo);
    }
  };

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  if (!audioUrl) return null;

  return (
    <div
      style={{
        backgroundColor: 'var(--sunken)',
        border: '1px solid var(--rule)',
        borderRadius: 'var(--r-lg)',
        padding: '14px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        boxShadow: 'var(--shadow-sm)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div
            style={{
              width: '28px',
              height: '28px',
              borderRadius: '50%',
              backgroundColor: 'var(--brand-wash)',
              color: 'var(--brand)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <Volume2 size={16} />
          </div>
          <div>
            <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--ink)' }}>
              Bodhan Indic-Speak Voice
            </div>
            <div style={{ fontSize: '0.72rem', color: 'var(--ink-3)' }}>
              Studio Voice: <strong>{voiceName}</strong> ({languageCode.toUpperCase()}) • 24 kHz WAV
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {/* Speed Toggle Button */}
          <button
            onClick={toggleSpeed}
            className="btn btn-ghost"
            style={{
              padding: '3px 8px',
              fontSize: '0.74rem',
              fontWeight: 700,
              borderRadius: 'var(--r-sm)',
              border: '1px solid var(--rule)'
            }}
            title="Toggle playback speed (1.0x / 1.25x)"
          >
            <Gauge size={12} />
            {playbackSpeed}×
          </button>

          {/* Replay */}
          <button
            onClick={handleRestart}
            className="btn btn-ghost btn-icon"
            style={{ width: '32px', height: '32px' }}
            title="Replay from start"
          >
            <RotateCcw size={15} />
          </button>
        </div>
      </div>

      {/* Progress & Waveform Bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* Play/Pause Button */}
        <button
          onClick={togglePlay}
          className="btn btn-brand"
          style={{
            width: '40px',
            height: '40px',
            padding: 0,
            borderRadius: '50%',
            flexShrink: 0
          }}
          aria-label={isPlaying ? 'Pause Audio' : 'Play Audio'}
        >
          {isPlaying ? <Pause size={18} /> : <Play size={18} style={{ marginLeft: '2px' }} />}
        </button>

        {/* Seek Slider */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <input
            type="range"
            min={0}
            max={duration || 10}
            step={0.1}
            value={currentTime}
            onChange={handleSeek}
            style={{
              width: '100%',
              accentColor: 'var(--brand)',
              cursor: 'pointer'
            }}
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '0.72rem',
              color: 'var(--ink-3)',
              fontFamily: "'JetBrains Mono', monospace"
            }}
          >
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>
      </div>
    </div>
  );
};
