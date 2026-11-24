// File: src/screens/ScanScreen.tsx
import React, { useState } from 'react';
import { CameraViewfinder } from '../components/CameraViewfinder';
import { ImageDropzone } from '../components/ImageDropzone';
import { SampleSignsModal } from '../components/SampleSignsModal';
import { ImageCropperModal } from '../components/ImageCropperModal';
import { CompressedImageResult } from '../services/imageCompressor';
import { SampleSign } from '../assets/sampleSigns';
import { AppSettings } from '../models/SignboardTypes';
import { getLanguageByCode } from '../models/voiceCatalog';
import { Languages, Volume2, ShieldCheck, Zap } from 'lucide-react';

interface ScanScreenProps {
  settings: AppSettings;
  onOpenSettings: () => void;
  onProcessImage: (compressed: CompressedImageResult, sample?: SampleSign) => void;
  onUpdateSettings?: (updated: Partial<AppSettings>) => void;
}

export const ScanScreen: React.FC<ScanScreenProps> = ({
  settings,
  onOpenSettings,
  onProcessImage,
  onUpdateSettings
}) => {
  const [isCameraActive, setIsCameraActive] = useState<boolean>(false);
  const [isSampleModalOpen, setIsSampleModalOpen] = useState<boolean>(false);
  const [pendingCropImage, setPendingCropImage] = useState<string | null>(null);

  const selectedLang = getLanguageByCode(settings.targetLanguage);
  const isCropperEnabled = settings.enableImageCropper !== false;

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Target Language & Voice Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'var(--sunken)',
          border: '1px solid var(--rule)',
          borderRadius: 'var(--r-lg)',
          padding: '10px 16px',
          boxShadow: 'var(--shadow-sm)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Languages size={18} color="var(--brand)" />
          <div style={{ fontSize: '0.85rem' }}>
            Target Language: <strong>{selectedLang.name}</strong> ({selectedLang.nativeName})
          </div>
        </div>

        <button
          onClick={onOpenSettings}
          className="btn"
          style={{
            padding: '5px 12px',
            fontSize: '0.78rem',
            fontWeight: 600,
            color: 'var(--brand)',
            backgroundColor: 'var(--brand-wash)',
            border: '1px solid var(--brand-line)',
            borderRadius: 'var(--r-md)'
          }}
        >
          <Volume2 size={14} />
          {settings.preferredVoice} • Change
        </button>
      </div>

      {/* Main Viewport: Live Camera OR File Dropzone */}
      {isCameraActive ? (
        <CameraViewfinder
          onCapture={(compressed) => {
            setIsCameraActive(false);
            if (isCropperEnabled) {
              setPendingCropImage(compressed.dataUrl);
            } else {
              onProcessImage(compressed);
            }
          }}
          onCancel={() => setIsCameraActive(false)}
        />
      ) : (
        <ImageDropzone
          onImageReady={(compressed) => {
            if (isCropperEnabled) {
              setPendingCropImage(compressed.dataUrl);
            } else {
              onProcessImage(compressed);
            }
          }}
          onOpenLiveCamera={() => setIsCameraActive(true)}
          onOpenSampleSigns={() => setIsSampleModalOpen(true)}
          isCropperEnabled={isCropperEnabled}
          onToggleCropper={() => onUpdateSettings?.({ enableImageCropper: !isCropperEnabled })}
        />
      )}

      {/* Crop Modal Viewport Overlay */}
      {pendingCropImage && (
        <ImageCropperModal
          imageSource={pendingCropImage}
          onCropComplete={(croppedResult) => {
            setPendingCropImage(null);
            onProcessImage(croppedResult);
          }}
          onCancel={() => setPendingCropImage(null)}
        />
      )}

      {/* Architecture Highlights Pill Row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px'
        }}
      >
        <div
          className="card"
          style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: '10px' }}
        >
          <Zap size={18} color="var(--brand)" />
          <div style={{ fontSize: '0.78rem' }}>
            <div style={{ fontWeight: 600, color: 'var(--ink)' }}>3ms On-Device Cleaner</div>
            <div style={{ color: 'var(--ink-3)' }}>No LLM regional script token bugs</div>
          </div>
        </div>

        <div
          className="card"
          style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: '10px' }}
        >
          <ShieldCheck size={18} color="#16a34a" />
          <div style={{ fontSize: '0.78rem' }}>
            <div style={{ fontWeight: 600, color: 'var(--ink)' }}>100% BYOK Security</div>
            <div style={{ color: 'var(--ink-3)' }}>AES-256 encrypted hardware storage</div>
          </div>
        </div>
      </div>

      {/* Sample Signs Modal */}
      <SampleSignsModal
        isOpen={isSampleModalOpen}
        onClose={() => setIsSampleModalOpen(false)}
        onSelectSample={(sample) => {
          onProcessImage(
            {
              dataUrl: sample.imageUrl,
              base64Data: sample.imageUrl.split(',')[1] || '',
              blob: new Blob([]),
              width: sample.dimensions.width,
              height: sample.dimensions.height,
              originalSizeBytes: 42000,
              compressedSizeBytes: 38000,
              compressionRatio: '10%'
            },
            sample
          );
        }}
      />
    </div>
  );
};
