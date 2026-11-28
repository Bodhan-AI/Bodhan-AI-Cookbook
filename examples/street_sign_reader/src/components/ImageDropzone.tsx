// File: src/components/ImageDropzone.tsx
import React, { useRef, useState } from 'react';
import { UploadCloud, Camera, Image as ImageIcon, AlertCircle, Video, Crop } from 'lucide-react';
import { validateImageFile, compressImage, CompressedImageResult } from '../services/imageCompressor';

interface ImageDropzoneProps {
  onImageReady: (compressed: CompressedImageResult) => void;
  onOpenLiveCamera: () => void;
  onOpenSampleSigns: () => void;
  isCropperEnabled?: boolean;
  onToggleCropper?: () => void;
}

export const ImageDropzone: React.FC<ImageDropzoneProps> = ({
  onImageReady,
  onOpenLiveCamera,
  onOpenSampleSigns,
  isCropperEnabled = true,
  onToggleCropper
}) => {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const nativeCameraRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isCompressing, setIsCompressing] = useState<boolean>(false);

  const processFile = async (file: File) => {
    setErrorMessage(null);
    const validation = validateImageFile(file);
    if (!validation.valid) {
      setErrorMessage(validation.error || 'Invalid file');
      return;
    }

    setIsCompressing(true);
    try {
      const compressed = await compressImage(file);
      onImageReady(compressed);
    } catch (err: any) {
      setErrorMessage(`Processing failed: ${err.message}`);
    } finally {
      setIsCompressing(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      processFile(e.dataTransfer.files[0]);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* Error Banner */}
      {errorMessage && (
        <div
          style={{
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: 'var(--r-md)',
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            color: '#dc2626',
            fontSize: '0.85rem'
          }}
        >
          <AlertCircle size={18} />
          <div>{errorMessage}</div>
        </div>
      )}

      {/* Main Dropzone Card */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        style={{
          border: `2px dashed ${isDragging ? 'var(--brand)' : 'var(--rule)'}`,
          backgroundColor: isDragging ? 'var(--brand-wash)' : 'var(--sunken)',
          borderRadius: 'var(--r-xl)',
          padding: '40px 20px',
          textAlign: 'center',
          cursor: 'pointer',
          transition: 'all 0.2s ease',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px'
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".jpg,.jpeg,.png,.heic,.heif,.webp,image/*"
          style={{ display: 'none' }}
          onChange={(e) => {
            if (e.target.files && e.target.files[0]) {
              processFile(e.target.files[0]);
            }
          }}
        />

        {/* Dedicated Native Camera Shutter Input (Works 100% on HTTP and HTTPS) */}
        <input
          ref={nativeCameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          style={{ display: 'none' }}
          onChange={(e) => {
            if (e.target.files && e.target.files[0]) {
              processFile(e.target.files[0]);
            }
          }}
        />

        <div
          style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            backgroundColor: 'var(--brand-wash)',
            color: 'var(--brand)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: '4px'
          }}
        >
          <UploadCloud size={28} />
        </div>

        <div>
          <div style={{ fontSize: '1.05rem', fontWeight: 600, color: 'var(--ink)' }}>
            {isCompressing ? 'Compressing image...' : 'Upload Signboard Photo'}
          </div>
          <div style={{ fontSize: '0.82rem', color: 'var(--ink-3)', marginTop: '4px' }}>
            Drag &amp; drop or click to browse • JPEG, PNG, HEIC, WebP (Max 25 MB)
          </div>
        </div>

        <div style={{ display: 'flex', gap: '8px', marginTop: '6px', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
          <span className="badge badge-gray">≤ 1600px Clamp</span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleCropper?.();
            }}
            className="badge"
            style={{
              cursor: 'pointer',
              background: isCropperEnabled
                ? 'linear-gradient(135deg, rgba(255, 85, 0, 0.15), rgba(234, 88, 12, 0.2))'
                : 'var(--sunken-2)',
              color: isCropperEnabled ? 'var(--brand)' : 'var(--ink-3)',
              border: `1px solid ${isCropperEnabled ? 'var(--brand-line)' : 'var(--rule)'}`,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              fontWeight: 600,
              transition: 'all 0.15s ease'
            }}
            title={isCropperEnabled ? 'Crop Tool is ON: Click to disable auto-cropper' : 'Crop Tool is OFF: Click to enable auto-cropper'}
          >
            <Crop size={12} />
            Crop Tool: {isCropperEnabled ? 'ON' : 'OFF'}
          </button>
          <span className="badge badge-ok">GPS Stripped</span>
        </div>
      </div>

      {/* Action Shortcut Buttons with Vibrant Modern Accents */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
        <button
          onClick={onOpenLiveCamera}
          className="btn btn-brand"
          style={{
            padding: '14px',
            borderRadius: 'var(--r-lg)',
            justifyContent: 'center',
            fontWeight: 600,
            boxShadow: '0 4px 14px rgba(255, 85, 0, 0.35)'
          }}
          title="Opens in-browser live viewfinder (requires HTTPS)"
        >
          <Video size={18} />
          Live Viewfinder
        </button>

        <button
          onClick={onOpenSampleSigns}
          className="btn btn-brand"
          style={{
            padding: '14px',
            borderRadius: 'var(--r-lg)',
            justifyContent: 'center',
            fontWeight: 600,
            boxShadow: '0 4px 14px rgba(255, 85, 0, 0.35)'
          }}
        >
          <ImageIcon size={18} />
          Sample Signs
        </button>
      </div>
    </div>
  );
};
