// File: src/components/SampleSignsModal.tsx
import React, { useState } from 'react';
import { X, Sparkles, ArrowRight, Layers } from 'lucide-react';
import { SAMPLE_SIGNS, SampleSign } from '../assets/sampleSigns';

interface SampleSignsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectSample: (sample: SampleSign) => void;
}

export const SampleSignsModal: React.FC<SampleSignsModalProps> = ({
  isOpen,
  onClose,
  onSelectSample
}) => {
  const [selectedLangFilter, setSelectedLangFilter] = useState<string>('all');

  if (!isOpen) return null;

  const filteredSigns =
    selectedLangFilter === 'all'
      ? SAMPLE_SIGNS
      : SAMPLE_SIGNS.filter((s) => s.sourceLanguage === selectedLangFilter);

  const availableLanguages = [
    { code: 'all', name: 'All Signs' },
    ...Array.from(new Set(SAMPLE_SIGNS.map((s) => s.sourceLanguage))).map((code) => {
      const match = SAMPLE_SIGNS.find((s) => s.sourceLanguage === code);
      return { code, name: match ? match.sourceLanguageName : code };
    })
  ];

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.65)',
        backdropFilter: 'blur(4px)',
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px'
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          width: '100%',
          maxWidth: '640px',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: 'var(--shadow-lg)'
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-header">
          <div className="card-title">
            <Sparkles size={18} color="var(--brand)" />
            <span>Choose Evaluation Signs ({SAMPLE_SIGNS.length} Signs)</span>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-icon">
            <X size={18} />
          </button>
        </div>

        {/* Quick Filter Bar */}
        <div
          style={{
            padding: '10px 16px',
            borderBottom: '1px solid var(--rule)',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            overflowX: 'auto',
            backgroundColor: 'var(--sunken)'
          }}
        >
          {availableLanguages.map((lang) => {
            const isActive = selectedLangFilter === lang.code;
            return (
              <button
                key={lang.code}
                onClick={() => setSelectedLangFilter(lang.code)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 'var(--r-full)',
                  fontSize: '0.76rem',
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                  border: `1px solid ${isActive ? 'var(--brand)' : 'var(--rule)'}`,
                  backgroundColor: isActive ? 'var(--brand)' : 'var(--card)',
                  color: isActive ? '#ffffff' : 'var(--ink)'
                }}
              >
                {lang.name}
              </button>
            );
          })}
        </div>

        <div
          className="card-body"
          style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}
        >
          <div style={{ fontSize: '0.84rem', color: 'var(--ink-2)', marginBottom: '4px' }}>
            FR-15 Test Signs: One-tap end-to-end evaluation without camera access or API credits.
          </div>

          {filteredSigns.map((sample) => (
            <div
              key={sample.id}
              onClick={() => {
                onSelectSample(sample);
                onClose();
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                padding: '12px',
                border: '1px solid var(--rule)',
                borderRadius: 'var(--r-md)',
                backgroundColor: 'var(--sunken)',
                cursor: 'pointer',
                transition: 'all 0.15s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--brand)';
                e.currentTarget.style.backgroundColor = 'var(--brand-wash)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--rule)';
                e.currentTarget.style.backgroundColor = 'var(--sunken)';
              }}
            >
              <img
                src={sample.imageUrl}
                alt={sample.name}
                style={{
                  width: '90px',
                  height: '68px',
                  borderRadius: 'var(--r-sm)',
                  objectFit: 'cover',
                  border: '1px solid var(--rule)'
                }}
              />

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span className="badge badge-brand">{sample.sourceLanguageName}</span>
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: '0.92rem',
                      color: 'var(--ink)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis'
                    }}
                  >
                    {sample.name}
                  </div>
                </div>
                <div
                  style={{
                    fontSize: '0.76rem',
                    color: 'var(--ink-3)',
                    marginTop: '2px',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }}
                >
                  {sample.description}
                </div>
              </div>

              <ArrowRight size={18} color="var(--brand)" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
