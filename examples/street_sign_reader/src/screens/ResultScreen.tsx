// File: src/screens/ResultScreen.tsx
import React, { useState, useEffect } from 'react';
import { ProcessedScanResult, SemanticBlock, AppSettings } from '../models/SignboardTypes';
import { BoundingBoxOverlay } from '../components/BoundingBoxOverlay';
import { AudioPlaybackBar } from '../components/AudioPlaybackBar';
import { ContactActionCard } from '../components/ContactActionCard';
import { ImageCropperModal } from '../components/ImageCropperModal';
import { CompressedImageResult } from '../services/imageCompressor';
import { performBodhanSpeak } from '../services/bodhanClient';
import { RotateCcw, Volume2, Clock, Sparkles, Navigation, Info, Compass, ShieldCheck, Crop } from 'lucide-react';

interface ResultScreenProps {
  result: ProcessedScanResult;
  settings: AppSettings;
  onNewScan: () => void;
  onReCrop?: (compressed: CompressedImageResult) => void;
}

export const ResultScreen: React.FC<ResultScreenProps> = ({
  result,
  settings,
  onNewScan,
  onReCrop
}) => {
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null);
  const [activeAudioUrl, setActiveAudioUrl] = useState<string | undefined>(result.audioWavUrl);
  const [isSpeakingBlock, setIsSpeakingBlock] = useState<boolean>(false);
  const [isCropperOpen, setIsCropperOpen] = useState<boolean>(false);

  // Synchronize audio playback whenever result updates (e.g. after re-cropping or new scan)
  useEffect(() => {
    setActiveAudioUrl(result.audioWavUrl);
    setActiveBlockId(null);
  }, [result.id, result.audioWavUrl]);

  // Tap-to-Speak on individual semantic bounding box (FR-12)
  const handleSelectBlock = async (block: SemanticBlock) => {
    setActiveBlockId(block.id);
    const textToSpeak = block.translatedText || block.originalText;
    if (!textToSpeak.trim()) return;

    setIsSpeakingBlock(true);
    try {
      const { audioUrl } = await performBodhanSpeak(
        textToSpeak,
        settings.preferredVoice,
        settings.targetLanguage
      );
      setActiveAudioUrl(audioUrl);
    } catch (err) {
      console.warn('Tap to speak failed:', err);
    } finally {
      setIsSpeakingBlock(false);
    }
  };

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Top Controls Bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={onNewScan}
            className="btn"
            style={{
              background: 'linear-gradient(135deg, #ff5500 0%, #e04400 100%)',
              color: '#ffffff',
              fontWeight: 600,
              boxShadow: '0 4px 12px rgba(255, 85, 0, 0.35)',
              border: 'none'
            }}
          >
            <RotateCcw size={16} />
            New Scan
          </button>

          {result.imageUrl && onReCrop && (
            <button
              onClick={() => setIsCropperOpen(true)}
              className="btn"
              style={{
                background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.12), rgba(139, 92, 246, 0.16))',
                color: '#4f46e5',
                border: '1.5px solid rgba(99, 102, 241, 0.35)',
                fontWeight: 600
              }}
              title="Adjust crop or rotation for this signboard and re-scan"
            >
              <Crop size={16} />
              Re-Crop Sign
            </button>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {result.isGemmaSynthesized ? (
            <span
              className="badge"
              style={{
                background: 'linear-gradient(135deg, #4f46e5, #7c3aed)',
                color: '#ffffff',
                fontWeight: 700,
                border: 'none',
                boxShadow: '0 2px 8px rgba(99, 102, 241, 0.35)'
              }}
            >
              <Sparkles size={12} style={{ marginRight: '4px' }} />
              {result.extraContext ? result.extraContext.domainLabel.split(' ')[1] : 'Gemma Active'}
            </span>
          ) : (
            <span
              className="badge"
              style={{
                background: 'linear-gradient(135deg, #059669, #10b981)',
                color: '#ffffff',
                fontWeight: 700,
                border: 'none',
                boxShadow: '0 2px 8px rgba(16, 185, 129, 0.25)'
              }}
            >
              <ShieldCheck size={12} style={{ marginRight: '4px' }} />
              Pure Bodhan IndicTrans2 (Zero LLM)
            </span>
          )}
          <span className="badge badge-brand">
            <Clock size={12} />
            {result.processingTimeMs} ms Total
          </span>
          <span className="badge badge-ok">Bodhan Native</span>
        </div>
      </div>

      {/* Interactive AR Bounding Box Viewport */}
      <BoundingBoxOverlay
        imageUrl={result.imageUrl}
        imageDimensions={result.imageDimensions}
        semanticBlocks={result.cleaned.semanticBlocks}
        activeBlockId={activeBlockId}
        onSelectBlock={handleSelectBlock}
      />

      {/* Audio Playback Bar */}
      <AudioPlaybackBar
        audioUrl={activeAudioUrl}
        voiceName={result.targetVoice}
        languageCode={result.targetLanguage}
        autoPlay={settings.autoPlayAudio}
      />

      {/* Spoken Summary Card */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Volume2 size={18} color="var(--brand)" />
            <span>Spoken Audio Narration</span>
          </div>
          {isSpeakingBlock && <span className="badge badge-brand">Synthesizing Block...</span>}
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {/* Dynamic Primary Name according to Archetype (Street vs Transit vs Store) */}
          {result.transliteratedBrand && (
            <div>
              <div style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--ink-3)', textTransform: 'uppercase', marginBottom: '4px' }}>
                {result.cleaned.archetype === 'STREET_WAYFINDING'
                  ? '🛣️ Highway / Street Name'
                  : result.cleaned.archetype === 'TRANSIT_REGULATORY'
                  ? '🚆 Platform / Gate Name'
                  : '🏪 Store / Location Name'}
              </div>
              <div style={{ fontSize: '1.15rem', fontWeight: 700, color: 'var(--brand)', lineHeight: 1.5 }}>
                {result.transliteratedBrand}
              </div>
              {result.transliteratedBrand !== result.cleaned.brandName && (
                <div style={{ fontSize: '0.78rem', color: 'var(--ink-3)', marginTop: '2px' }}>
                  Original: {result.cleaned.brandName}
                </div>
              )}
            </div>
          )}

          {/* Dynamic Secondary Details according to Archetype */}
          {result.translatedServices && (
            <div>
              <div style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--ink-3)', textTransform: 'uppercase', marginBottom: '4px' }}>
                {result.cleaned.archetype === 'STREET_WAYFINDING'
                  ? '🧭 Destinations & Distances'
                  : result.cleaned.archetype === 'TRANSIT_REGULATORY'
                  ? '🚶 Station Guidance & Amenities'
                  : '📦 Services & Offerings'}
              </div>
              <div style={{ fontSize: '1.0rem', fontWeight: 600, color: 'var(--ink)', lineHeight: 1.6 }}>
                {result.translatedServices}
              </div>
              {result.translatedServices !== result.cleaned.servicesText && (
                <div style={{ fontSize: '0.78rem', color: 'var(--ink-3)', marginTop: '2px' }}>
                  Original: {result.cleaned.servicesText}
                </div>
              )}
            </div>
          )}

          {/* Critical Mandatory Notice Badge (Emergency, Casualty, Appointments) */}
          {(result.cleaned.mandatoryNotice || result.cleaned.commercialDetails?.mandatoryNotice) && (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: 'var(--r-sm)',
              backgroundColor: '#fee2e2',
              border: '1px solid #ef4444',
              fontSize: '0.82rem',
              fontWeight: 700,
              color: '#991b1b',
              alignSelf: 'flex-start'
            }}>
              🚨 {result.cleaned.mandatoryNotice || result.cleaned.commercialDetails?.mandatoryNotice}
            </div>
          )}

          {/* Operating Hours / Timings Badge */}
          {result.cleaned.commercialDetails?.timings && (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--r-sm)',
              backgroundColor: 'var(--brand-wash)',
              border: '1px solid var(--brand)',
              fontSize: '0.8rem',
              fontWeight: 600,
              color: 'var(--brand-ink)',
              alignSelf: 'flex-start'
            }}>
              🕒 {result.cleaned.commercialDetails.timings}
            </div>
          )}

          {/* Store / Clinic Address Chip (Captured as metadata without bloating spoken audio) */}
          {(result.cleaned.address || result.cleaned.commercialDetails?.address) && (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--r-sm)',
              backgroundColor: 'var(--sunken)',
              border: '1px solid var(--rule)',
              fontSize: '0.8rem',
              fontWeight: 500,
              color: 'var(--ink-2)',
              alignSelf: 'flex-start'
            }}>
              📍 Address: {result.cleaned.address || result.cleaned.commercialDetails?.address}
            </div>
          )}

          {/* Wayfinding Specific Destination Chips */}
          {result.cleaned.wayfindingDetails && result.cleaned.wayfindingDetails.destinations.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '2px' }}>
              {result.cleaned.wayfindingDetails.destinations.map((dest, i) => (
                <span
                  key={i}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    padding: '3px 8px',
                    borderRadius: 'var(--r-sm)',
                    backgroundColor: 'var(--ok-wash)',
                    border: '1px solid var(--ok)',
                    fontSize: '0.76rem',
                    fontWeight: 600,
                    color: '#065f46'
                  }}
                >
                  📍 {dest.name} {dest.distance ? `(${dest.distance})` : ''}
                </span>
              ))}
            </div>
          )}

          {/* Direction Guide */}
          {result.cleaned.directionGuide && (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: 'var(--r-sm)',
              backgroundColor: 'var(--brand-wash)',
              border: '1px solid var(--brand)',
              fontSize: '0.85rem',
              fontWeight: 600,
              color: 'var(--brand-ink)',
              alignSelf: 'flex-start'
            }}>
              🧭 {result.cleaned.directionGuide}
            </div>
          )}

            {/* Full Speech Script Preview */}
          <div style={{ borderTop: '1px solid var(--rule)', paddingTop: '10px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--ink-3)', textTransform: 'uppercase' }}>
                {result.isGemmaSynthesized ? 'Gemma Conversational Speech' : 'Bodhan Speech Script'}
              </div>
              {result.isGemmaSynthesized ? (
                <span
                  className="badge"
                  style={{
                    background: 'linear-gradient(135deg, #4f46e5, #7c3aed)',
                    color: '#ffffff',
                    fontWeight: 700,
                    border: 'none',
                    fontSize: '0.7rem'
                  }}
                >
                  ✨ Gemma Synthesized ({result.targetLanguage.toUpperCase()})
                </span>
              ) : (
                <span
                  className="badge badge-ok"
                  style={{
                    fontSize: '0.7rem',
                    fontWeight: 700
                  }}
                >
                  🌐 Bodhan IndicTrans2 ({result.targetLanguage.toUpperCase()})
                </span>
              )}
            </div>

            <div
              style={{
                fontSize: '0.96rem',
                fontWeight: 600,
                color: 'var(--ink)',
                lineHeight: 1.6,
                padding: result.isGemmaSynthesized ? '12px 14px' : '0',
                backgroundColor: result.isGemmaSynthesized ? 'var(--brand-wash)' : 'transparent',
                borderRadius: 'var(--r-md)',
                border: result.isGemmaSynthesized ? '1px solid var(--brand)' : 'none',
                fontStyle: result.isGemmaSynthesized ? 'normal' : 'italic'
              }}
            >
              "{result.masterSpeechScript}"
            </div>
          </div>
          <div style={{ fontSize: '0.76rem', color: 'var(--ink-3)', marginTop: '4px' }}>
            {result.isGemmaSynthesized
              ? 'Conversational script rendered into 24 kHz studio voice by Bodhan indic-speak.'
              : 'Joined semantic script synthesized into 24 kHz studio voice by Bodhan indic-speak.'}
          </div>
        </div>
      </div>

      {/* Contact Dialer Card (FR-11) */}
      <ContactActionCard phoneNumbers={result.cleaned.phoneNumbers} />

      {/* Semantic Blocks Deep Dive */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Sparkles size={18} color="var(--brand)" />
            <span>Detected Semantic Blocks</span>
          </div>
          <span className="badge badge-gray">{result.cleaned.semanticBlocks.length} Blocks</span>
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {result.cleaned.semanticBlocks.map((block) => (
            <div
              key={block.id}
              onClick={() => block.isClickable && handleSelectBlock(block)}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                justifyContent: 'space-between',
                padding: '12px 14px',
                borderRadius: 'var(--r-md)',
                backgroundColor: activeBlockId === block.id ? 'var(--brand-wash)' : 'var(--sunken)',
                border: `1px solid ${
                  activeBlockId === block.id ? 'var(--brand)' : 'var(--rule)'
                }`,
                cursor: block.isClickable ? 'pointer' : 'default',
                transition: 'all 0.15s ease'
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span
                    style={{
                      display: 'inline-block',
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      backgroundColor: block.badgeColor
                    }}
                  />
                  <span
                    style={{
                      fontSize: '0.72rem',
                      fontWeight: 700,
                      color: block.badgeColor,
                      textTransform: 'uppercase'
                    }}
                  >
                    {block.badgeLabel}
                  </span>
                </div>
                <div style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--ink)' }}>
                  {block.translatedText || block.originalText}
                </div>
                {block.translatedText && block.translatedText !== block.originalText && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--ink-3)' }}>
                    Original: {block.originalText}
                  </div>
                )}
              </div>

              {block.isClickable && (
                <button
                  className="btn btn-ghost btn-icon"
                  style={{ width: '28px', height: '28px' }}
                  title="Tap to speak this block"
                >
                  <Volume2 size={14} color="var(--brand)" />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Re-crop Modal Viewport */}
      {isCropperOpen && (
        <ImageCropperModal
          imageSource={result.imageUrl}
          onCropComplete={(croppedResult) => {
            setIsCropperOpen(false);
            onReCrop?.(croppedResult);
          }}
          onCancel={() => setIsCropperOpen(false)}
        />
      )}
    </div>
  );
};
