// File: src/components/CameraViewfinder.tsx
import React, { useRef, useState, useEffect } from 'react';
import { Camera, SwitchCamera, X, AlertCircle } from 'lucide-react';
import { compressImage, CompressedImageResult } from '../services/imageCompressor';

interface CameraViewfinderProps {
  onCapture: (compressed: CompressedImageResult) => void;
  onCancel: () => void;
}

export const CameraViewfinder: React.FC<CameraViewfinderProps> = ({ onCapture, onCancel }) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nativeCameraInputRef = useRef<HTMLInputElement | null>(null);
  const [facingMode, setFacingMode] = useState<'environment' | 'user'>('environment');
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);

  useEffect(() => {
    startCamera();
    return () => {
      stopCamera();
    };
  }, [facingMode]);

  const startCamera = async () => {
    stopCamera();
    setCameraError(null);

    // Modern mobile browsers restrict live WebRTC camera to HTTPS or localhost
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setCameraError(
        'Live stream camera requires HTTPS on mobile browsers. Tap below to capture directly with your phone camera.'
      );
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: facingMode },
          width: { ideal: 1920 },
          height: { ideal: 1080 }
        },
        audio: false
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.play();
      }
    } catch (err: any) {
      console.warn('Camera access error:', err);
      setCameraError(
        'Mobile browser blocked live video. Tap below to use your phone camera directly.'
      );
    }
  };

  const handleNativeCapture = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setIsProcessing(true);
      try {
        const compressed = await compressImage(e.target.files[0]);
        stopCamera();
        onCapture(compressed);
      } catch (err: any) {
        alert(`Photo processing failed: ${err.message}`);
        setIsProcessing(false);
      }
    }
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  };

  const toggleFacing = () => {
    setFacingMode((prev) => (prev === 'environment' ? 'user' : 'environment'));
  };

  const captureFrame = async () => {
    if (!videoRef.current || isProcessing) return;
    setIsProcessing(true);

    try {
      const video = videoRef.current;
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Canvas not supported');

      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      canvas.toBlob(
        async (blob) => {
          if (!blob) throw new Error('Frame capture failed');
          const compressed = await compressImage(blob);
          stopCamera();
          onCapture(compressed);
        },
        'image/jpeg',
        0.9
      );
    } catch (err: any) {
      alert(`Capture failed: ${err.message}`);
      setIsProcessing(false);
    }
  };

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '460px',
        backgroundColor: '#000000',
        borderRadius: 'var(--r-xl)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: 'var(--shadow-lg)'
      }}
    >
      {cameraError ? (
        <div style={{ textAlign: 'center', color: '#ffffff', padding: '24px', maxWidth: '380px' }}>
          <AlertCircle size={44} color="#ea580c" style={{ marginBottom: '12px' }} />
          <div style={{ fontSize: '0.98rem', fontWeight: 600, lineHeight: 1.5 }}>
            {cameraError}
          </div>

          {/* Hidden Native Camera Input */}
          <input
            ref={nativeCameraInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: 'none' }}
            onChange={handleNativeCapture}
          />

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '20px' }}>
            <button
              onClick={() => nativeCameraInputRef.current?.click()}
              disabled={isProcessing}
              className="btn btn-brand"
              style={{ padding: '12px 20px', gap: '8px', fontSize: '0.92rem', justifyContent: 'center' }}
            >
              <Camera size={20} />
              {isProcessing ? 'Processing Photo...' : 'Open Phone Camera'}
            </button>

            <button
              onClick={onCancel}
              className="btn btn-secondary"
              style={{ padding: '10px 16px', fontSize: '0.85rem', justifyContent: 'center' }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <video
            ref={videoRef}
            playsInline
            muted
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover'
            }}
          />

          {/* AR Target Reticle Overlay */}
          <div
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: '75%',
              height: '65%',
              border: '2px dashed rgba(255, 85, 0, 0.6)',
              borderRadius: 'var(--r-lg)',
              pointerEvents: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <div
              style={{
                backgroundColor: 'rgba(0,0,0,0.6)',
                color: '#ffffff',
                fontSize: '11px',
                padding: '4px 10px',
                borderRadius: '4px',
                fontWeight: 600
              }}
            >
              Align signboard within the frame
            </div>
          </div>

          {/* Close Button Top Right */}
          <button
            onClick={onCancel}
            className="btn btn-ghost btn-icon"
            style={{
              position: 'absolute',
              top: '16px',
              right: '16px',
              backgroundColor: 'rgba(0,0,0,0.5)',
              color: '#ffffff',
              borderRadius: '50%'
            }}
          >
            <X size={20} />
          </button>

          {/* Flip Camera Button Top Left */}
          <button
            onClick={toggleFacing}
            className="btn btn-ghost btn-icon"
            style={{
              position: 'absolute',
              top: '16px',
              left: '16px',
              backgroundColor: 'rgba(0,0,0,0.5)',
              color: '#ffffff',
              borderRadius: '50%'
            }}
            title="Switch front/back camera"
          >
            <SwitchCamera size={20} />
          </button>

          {/* Bottom Shutter Controls */}
          <div
            style={{
              position: 'absolute',
              bottom: '20px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <button
              onClick={captureFrame}
              disabled={isProcessing}
              style={{
                width: '68px',
                height: '68px',
                borderRadius: '50%',
                backgroundColor: '#ffffff',
                border: '4px solid var(--brand)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: '0 0 20px rgba(255, 85, 0, 0.6)',
                transition: 'transform 0.15s ease'
              }}
              aria-label="Capture Signboard Photo"
            >
              <div
                style={{
                  width: '52px',
                  height: '52px',
                  borderRadius: '50%',
                  backgroundColor: 'var(--brand)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#ffffff'
                }}
              >
                <Camera size={26} />
              </div>
            </button>
          </div>
        </>
      )}
    </div>
  );
};
