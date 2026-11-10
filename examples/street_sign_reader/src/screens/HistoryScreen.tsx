// File: src/screens/HistoryScreen.tsx
import React, { useState, useEffect, useRef } from 'react';
import { HistoryScanItem } from '../models/SignboardTypes';
import { getStoredHistory, clearHistory, deleteScanFromHistory } from '../services/secureStorage';
import { History, Play, Pause, Trash2, ArrowRight, Volume2 } from 'lucide-react';

interface HistoryScreenProps {
  onReplayScan?: (item: HistoryScanItem) => void;
  onGoToScan: () => void;
}

export const HistoryScreen: React.FC<HistoryScreenProps> = ({ onReplayScan, onGoToScan }) => {
  const [history, setHistory] = useState<HistoryScanItem[]>([]);
  const [playingScanId, setPlayingScanId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setHistory(getStoredHistory());

    // Cleanup audio playback when component unmounts
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
        audioRef.current = null;
      }
    };
  }, []);

  const handlePlayAudio = (item: HistoryScanItem, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();

    if (!item.audioWavUrl) return;

    // Notify parent if listener provided
    if (onReplayScan) {
      onReplayScan(item);
    }

    // Toggle: If the clicked item is already playing, pause it
    if (playingScanId === item.id) {
      if (audioRef.current) {
        audioRef.current.pause();
      }
      setPlayingScanId(null);
      return;
    }

    // Abort and stop any previously playing audio immediately
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }

    // Instantiate and play the newly clicked audio
    const newAudio = new Audio(item.audioWavUrl);
    audioRef.current = newAudio;
    setPlayingScanId(item.id);

    newAudio.onended = () => {
      setPlayingScanId(null);
      audioRef.current = null;
    };

    newAudio.onerror = (err) => {
      console.warn('Audio playback error:', err);
      setPlayingScanId(null);
      audioRef.current = null;
    };

    newAudio.play().catch((err) => {
      console.warn('Playback interrupted:', err);
      setPlayingScanId(null);
      audioRef.current = null;
    });
  };

  const handleDeleteScan = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();

    // If the scan being deleted is currently playing, abort audio immediately
    if (playingScanId === id && audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
      setPlayingScanId(null);
    }

    const updated = deleteScanFromHistory(id);
    setHistory(updated);
  };

  const handleClear = () => {
    if (confirm('Are you sure you want to clear all scan history?')) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
        audioRef.current = null;
        setPlayingScanId(null);
      }
      clearHistory();
      setHistory([]);
    }
  };

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <History size={20} color="var(--brand)" />
          <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--ink)' }}>
            Recent Scan History
          </div>
        </div>

        {history.length > 0 && (
          <button onClick={handleClear} className="btn btn-ghost" style={{ color: '#dc2626' }}>
            <Trash2 size={16} />
            Clear All
          </button>
        )}
      </div>

      {history.length === 0 ? (
        <div
          className="card"
          style={{
            padding: '48px 20px',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '12px'
          }}
        >
          <div
            style={{
              width: '56px',
              height: '56px',
              borderRadius: '50%',
              backgroundColor: 'var(--sunken-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--ink-3)'
            }}
          >
            <History size={28} />
          </div>
          <div style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--ink)' }}>
            No Scans Yet
          </div>
          <div style={{ fontSize: '0.84rem', color: 'var(--ink-3)', maxWidth: '340px' }}>
            Scanned signboards will appear here for instant offline audio replay.
          </div>
          <button onClick={onGoToScan} className="btn btn-brand" style={{ marginTop: '8px' }}>
            Start Scanning
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {history.map((item) => {
            const isPlaying = playingScanId === item.id;

            return (
              <div
                key={item.id}
                onClick={(e) => handlePlayAudio(item, e)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '14px',
                  padding: '12px 16px',
                  borderRadius: 'var(--r-lg)',
                  backgroundColor: isPlaying ? 'var(--brand-wash)' : 'var(--surface)',
                  border: `1px solid ${isPlaying ? 'var(--brand)' : 'var(--rule)'}`,
                  cursor: item.audioWavUrl ? 'pointer' : 'default',
                  transition: 'all 0.15s ease',
                  boxShadow: isPlaying ? '0 0 12px rgba(255, 85, 0, 0.18)' : 'var(--shadow-sm)'
                }}
                onMouseEnter={(e) => {
                  if (!isPlaying) e.currentTarget.style.borderColor = 'var(--brand)';
                }}
                onMouseLeave={(e) => {
                  if (!isPlaying) e.currentTarget.style.borderColor = 'var(--rule)';
                }}
              >
                <img
                  src={item.thumbnailUrl}
                  alt={item.brandName}
                  style={{
                    width: '64px',
                    height: '64px',
                    borderRadius: 'var(--r-md)',
                    objectFit: 'cover',
                    border: '1px solid var(--rule)'
                  }}
                />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className="badge badge-brand">{item.targetLanguage.toUpperCase()}</span>
                    {isPlaying && (
                      <span className="badge badge-ok" style={{ animation: 'pulse 1.5s infinite' }}>
                        Playing
                      </span>
                    )}
                    <div
                      style={{
                        fontSize: '0.94rem',
                        fontWeight: 600,
                        color: 'var(--ink)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis'
                      }}
                    >
                      {item.brandName || 'Unlabeled Signboard'}
                    </div>
                  </div>

                  <div
                    style={{
                      fontSize: '0.78rem',
                      color: 'var(--ink-2)',
                      marginTop: '2px',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis'
                    }}
                  >
                    {item.servicesSummary}
                  </div>

                  <div style={{ fontSize: '0.70rem', color: 'var(--ink-3)', marginTop: '4px' }}>
                    {new Date(item.timestamp).toLocaleDateString()} •{' '}
                    {new Date(item.timestamp).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit'
                    })}
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {item.audioWavUrl && (
                    <button
                      className="btn btn-ghost btn-icon"
                      title={isPlaying ? 'Pause Audio' : 'Play Cached Audio'}
                      onClick={(e) => handlePlayAudio(item, e)}
                      style={{
                        backgroundColor: isPlaying ? 'var(--brand)' : 'var(--sunken)',
                        color: isPlaying ? '#ffffff' : 'var(--brand)',
                        borderRadius: 'var(--r-full)',
                        width: '34px',
                        height: '34px'
                      }}
                    >
                      {isPlaying ? <Pause size={16} /> : <Play size={16} />}
                    </button>
                  )}

                  {/* Individual Delete Button */}
                  <button
                    className="btn btn-ghost btn-icon"
                    title="Delete this scan"
                    onClick={(e) => handleDeleteScan(item.id, e)}
                    style={{ color: 'var(--ink-3)' }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = '#dc2626')}
                    onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--ink-3)')}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
