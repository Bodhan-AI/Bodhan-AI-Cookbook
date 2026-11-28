// File: src/components/ImageCropperModal.tsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Crop, RotateCw, Check, X, Maximize2, Sparkles, Image as ImageIcon } from 'lucide-react';
import { compressImage, CompressedImageResult } from '../services/imageCompressor';

interface CropRect {
  x: number; // percentage [0, 100]
  y: number; // percentage [0, 100]
  width: number; // percentage [0, 100]
  height: number; // percentage [0, 100]
}

interface ImageCropperModalProps {
  imageSource: string | File | Blob;
  onCropComplete: (compressed: CompressedImageResult) => void;
  onCancel: () => void;
}

type HandleType =
  | 'move'
  | 'nw'
  | 'ne'
  | 'sw'
  | 'se'
  | 'n'
  | 's'
  | 'e'
  | 'w'
  | null;

export const ImageCropperModal: React.FC<ImageCropperModalProps> = ({
  imageSource,
  onCropComplete,
  onCancel
}) => {
  const [dataUrl, setDataUrl] = useState<string>('');
  const [rotation, setRotation] = useState<number>(0);
  const [crop, setCrop] = useState<CropRect>({ x: 5, y: 15, width: 90, height: 70 });
  const [aspectPreset, setAspectPreset] = useState<'free' | '16:9' | '3:1' | '1:1'>('free');
  const [isProcessing, setIsProcessing] = useState<boolean>(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const originalBlobRef = useRef<Blob | null>(null);

  // Drag tracking ref
  const dragRef = useRef<{
    handle: HandleType;
    startX: number;
    startY: number;
    startCrop: CropRect;
  }>({
    handle: null,
    startX: 0,
    startY: 0,
    startCrop: { x: 0, y: 0, width: 0, height: 0 }
  });

  // Load and normalize incoming imageSource to DataURL
  useEffect(() => {
    if (typeof imageSource === 'string') {
      setDataUrl(imageSource);
      // Fetch as blob for full-image fallback
      fetch(imageSource)
        .then((r) => r.blob())
        .then((b) => {
          originalBlobRef.current = b;
        })
        .catch(() => {});
    } else {
      originalBlobRef.current = imageSource;
      const reader = new FileReader();
      reader.onload = (e) => {
        if (e.target?.result) {
          setDataUrl(e.target.result as string);
        }
      };
      reader.readAsDataURL(imageSource);
    }
  }, [imageSource]);

  // Handle Preset Changes
  const applyPreset = (preset: 'free' | '16:9' | '3:1' | '1:1') => {
    setAspectPreset(preset);
    if (!imageRef.current) return;
    const imgAspect = imageRef.current.naturalWidth / imageRef.current.naturalHeight;

    if (preset === 'free') return;

    let targetRatio = 1;
    if (preset === '16:9') targetRatio = 16 / 9;
    if (preset === '3:1') targetRatio = 3 / 1;
    if (preset === '1:1') targetRatio = 1;

    // Adjust crop height/width to match ratio based on image natural dimensions
    let newW = 85;
    let newH = (newW * imgAspect) / targetRatio;
    if (newH > 85) {
      newH = 85;
      newW = (newH * targetRatio) / imgAspect;
    }
    const newX = Math.max(0, (100 - newW) / 2);
    const newY = Math.max(0, (100 - newH) / 2);
    setCrop({ x: newX, y: newY, width: newW, height: newH });
  };

  const handleResetCrop = () => {
    setCrop({ x: 2, y: 2, width: 96, height: 96 });
    setAspectPreset('free');
  };

  // Rotate 90° Clockwise
  const handleRotate = () => {
    setRotation((prev) => (prev + 90) % 360);
  };

  // Pointer Down for handles or move
  const startDrag = (handle: HandleType, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);

    dragRef.current = {
      handle,
      startX: e.clientX,
      startY: e.clientY,
      startCrop: { ...crop }
    };
  };

  // Pointer Move
  const handlePointerMove = (e: React.PointerEvent) => {
    const { handle, startX, startY, startCrop } = dragRef.current;
    if (!handle || !containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    const dxPercent = ((e.clientX - startX) / rect.width) * 100;
    const dyPercent = ((e.clientY - startY) / rect.height) * 100;

    let { x, y, width, height } = startCrop;
    const minSize = 10; // min 10%

    if (handle === 'move') {
      x = Math.max(0, Math.min(100 - width, startCrop.x + dxPercent));
      y = Math.max(0, Math.min(100 - height, startCrop.y + dyPercent));
    } else {
      if (handle.includes('w')) {
        const potentialX = startCrop.x + dxPercent;
        const potentialW = startCrop.width - dxPercent;
        if (potentialW >= minSize && potentialX >= 0) {
          x = potentialX;
          width = potentialW;
        }
      }
      if (handle.includes('e')) {
        const potentialW = startCrop.width + dxPercent;
        if (potentialW >= minSize && startCrop.x + potentialW <= 100) {
          width = potentialW;
        }
      }
      if (handle.includes('n')) {
        const potentialY = startCrop.y + dyPercent;
        const potentialH = startCrop.height - dyPercent;
        if (potentialH >= minSize && potentialY >= 0) {
          y = potentialY;
          height = potentialH;
        }
      }
      if (handle.includes('s')) {
        const potentialH = startCrop.height + dyPercent;
        if (potentialH >= minSize && startCrop.y + potentialH <= 100) {
          height = potentialH;
        }
      }
    }

    setCrop({ x, y, width, height });
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    if (dragRef.current.handle) {
      try {
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
      } catch (_) {}
      dragRef.current.handle = null;
    }
  };

  // Crop execution using Canvas
  const handlePerformCrop = async () => {
    if (!dataUrl || !imageRef.current) return;
    setIsProcessing(true);

    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = dataUrl;
      await new Promise((res, rej) => {
        img.onload = res;
        img.onerror = rej;
      });

      // 1. First render with rotation into canvas
      const isRotated = rotation % 180 !== 0;
      const baseCanvas = document.createElement('canvas');
      baseCanvas.width = isRotated ? img.naturalHeight : img.naturalWidth;
      baseCanvas.height = isRotated ? img.naturalWidth : img.naturalHeight;

      const baseCtx = baseCanvas.getContext('2d');
      if (!baseCtx) throw new Error('Could not initialize canvas context');

      baseCtx.translate(baseCanvas.width / 2, baseCanvas.height / 2);
      baseCtx.rotate((rotation * Math.PI) / 180);
      baseCtx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);

      // 2. Crop from rotated canvas
      const cropPxX = Math.round((crop.x / 100) * baseCanvas.width);
      const cropPxY = Math.round((crop.y / 100) * baseCanvas.height);
      const cropPxW = Math.max(1, Math.round((crop.width / 100) * baseCanvas.width));
      const cropPxH = Math.max(1, Math.round((crop.height / 100) * baseCanvas.height));

      const croppedCanvas = document.createElement('canvas');
      croppedCanvas.width = cropPxW;
      croppedCanvas.height = cropPxH;

      const croppedCtx = croppedCanvas.getContext('2d');
      if (!croppedCtx) throw new Error('Could not initialize crop context');

      croppedCtx.imageSmoothingEnabled = true;
      croppedCtx.imageSmoothingQuality = 'high';
      croppedCtx.drawImage(
        baseCanvas,
        cropPxX,
        cropPxY,
        cropPxW,
        cropPxH,
        0,
        0,
        cropPxW,
        cropPxH
      );

      // Convert to blob and pass through standard compression
      croppedCanvas.toBlob(
        async (blob) => {
          if (!blob) {
            setIsProcessing(false);
            alert('Failed to generate cropped image');
            return;
          }
          try {
            const compressed = await compressImage(blob);
            setIsProcessing(false);
            onCropComplete(compressed);
          } catch (err: any) {
            setIsProcessing(false);
            alert(`Compression error: ${err.message}`);
          }
        },
        'image/jpeg',
        0.92
      );
    } catch (err: any) {
      setIsProcessing(false);
      alert(`Crop failed: ${err.message}`);
    }
  };

  // Bypass crop: process full original photo
  const handleUseFullImage = async () => {
    setIsProcessing(true);
    try {
      if (rotation !== 0 && dataUrl) {
        // Apply rotation to full image if rotated
        const img = new Image();
        img.src = dataUrl;
        await new Promise((res) => (img.onload = res));

        const isRotated = rotation % 180 !== 0;
        const rotCanvas = document.createElement('canvas');
        rotCanvas.width = isRotated ? img.naturalHeight : img.naturalWidth;
        rotCanvas.height = isRotated ? img.naturalWidth : img.naturalHeight;
        const ctx = rotCanvas.getContext('2d');
        if (ctx) {
          ctx.translate(rotCanvas.width / 2, rotCanvas.height / 2);
          ctx.rotate((rotation * Math.PI) / 180);
          ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);

          rotCanvas.toBlob(async (blob) => {
            if (blob) {
              const comp = await compressImage(blob);
              setIsProcessing(false);
              onCropComplete(comp);
            }
          }, 'image/jpeg', 0.9);
          return;
        }
      }

      if (originalBlobRef.current) {
        const compressed = await compressImage(originalBlobRef.current);
        setIsProcessing(false);
        onCropComplete(compressed);
      } else {
        // Fallback fetch
        const res = await fetch(dataUrl);
        const blob = await res.blob();
        const compressed = await compressImage(blob);
        setIsProcessing(false);
        onCropComplete(compressed);
      }
    } catch (err: any) {
      setIsProcessing(false);
      alert(`Full image processing failed: ${err.message}`);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        backgroundColor: 'rgba(9, 9, 11, 0.88)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px'
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '720px',
          maxHeight: '92vh',
          backgroundColor: 'var(--surface)',
          borderRadius: 'var(--r-xl)',
          border: '1px solid var(--rule)',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden'
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid var(--rule)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'var(--sunken)'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div
              style={{
                width: '32px',
                height: '32px',
                borderRadius: 'var(--r-md)',
                backgroundColor: 'var(--brand-wash)',
                color: 'var(--brand)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <Crop size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: '1rem', color: 'var(--ink)' }}>
                Crop Signboard Photo
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                Frame the signboard to eliminate street clutter &amp; boost OCR accuracy
              </div>
            </div>
          </div>

          <button
            onClick={onCancel}
            className="btn btn-ghost btn-icon"
            style={{ width: '32px', height: '32px' }}
            title="Cancel and close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Preset & Rotate Toolbar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 16px',
            backgroundColor: 'var(--sunken-2)',
            borderBottom: '1px solid var(--rule)',
            flexWrap: 'wrap',
            gap: '8px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--ink-3)', marginRight: '2px' }}>
              Ratio:
            </span>
            {(['free', '16:9', '3:1', '1:1'] as const).map((preset) => (
              <button
                key={preset}
                onClick={() => applyPreset(preset)}
                className="btn btn-ghost"
                style={{
                  padding: '3px 9px',
                  fontSize: '0.74rem',
                  fontWeight: 600,
                  borderRadius: 'var(--r-sm)',
                  backgroundColor: aspectPreset === preset ? 'var(--brand)' : 'transparent',
                  color: aspectPreset === preset ? '#ffffff' : 'var(--ink-2)',
                  border: `1px solid ${aspectPreset === preset ? 'var(--brand)' : 'var(--rule)'}`
                }}
              >
                {preset === 'free' ? 'Free' : preset === '16:9' ? '16:9 Wide' : preset === '3:1' ? '3:1 Sign' : '1:1'}
              </button>
            ))}

            <button
              onClick={handleResetCrop}
              className="btn btn-ghost"
              style={{
                padding: '3px 8px',
                fontSize: '0.74rem',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--rule)'
              }}
              title="Reset to full view"
            >
              <Maximize2 size={12} />
              Reset
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <button
              onClick={handleRotate}
              className="btn btn-ghost"
              style={{
                padding: '4px 10px',
                fontSize: '0.75rem',
                fontWeight: 600,
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--rule)',
                color: 'var(--ink)'
              }}
              title="Rotate 90° clockwise"
            >
              <RotateCw size={13} style={{ marginRight: '4px' }} />
              Rotate 90° {rotation > 0 ? `(${rotation}°)` : ''}
            </button>
          </div>
        </div>

        {/* Viewport / Interactive Crop Stage */}
        <div
          style={{
            flex: 1,
            position: 'relative',
            minHeight: '320px',
            maxHeight: '52vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: '#09090b',
            overflow: 'hidden',
            userSelect: 'none'
          }}
        >
          {dataUrl ? (
            <div
              ref={containerRef}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              style={{
                position: 'relative',
                display: 'inline-block',
                maxHeight: '100%',
                maxWidth: '100%',
                lineHeight: 0
              }}
            >
              {/* Displayed Image */}
              <img
                ref={imageRef}
                src={dataUrl}
                alt="Crop preview"
                style={{
                  display: 'block',
                  maxWidth: '100%',
                  maxHeight: '50vh',
                  objectFit: 'contain',
                  transform: `rotate(${rotation}deg)`,
                  transition: 'transform 0.15s ease'
                }}
              />

              {/* Shaded Mask: Top */}
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  height: `${crop.y}%`,
                  backgroundColor: 'rgba(0, 0, 0, 0.65)',
                  pointerEvents: 'none'
                }}
              />
              {/* Shaded Mask: Bottom */}
              <div
                style={{
                  position: 'absolute',
                  top: `${crop.y + crop.height}%`,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  backgroundColor: 'rgba(0, 0, 0, 0.65)',
                  pointerEvents: 'none'
                }}
              />
              {/* Shaded Mask: Left */}
              <div
                style={{
                  position: 'absolute',
                  top: `${crop.y}%`,
                  left: 0,
                  width: `${crop.x}%`,
                  height: `${crop.height}%`,
                  backgroundColor: 'rgba(0, 0, 0, 0.65)',
                  pointerEvents: 'none'
                }}
              />
              {/* Shaded Mask: Right */}
              <div
                style={{
                  position: 'absolute',
                  top: `${crop.y}%`,
                  left: `${crop.x + crop.width}%`,
                  right: 0,
                  height: `${crop.height}%`,
                  backgroundColor: 'rgba(0, 0, 0, 0.65)',
                  pointerEvents: 'none'
                }}
              />

              {/* Active Crop Box */}
              <div
                onPointerDown={(e) => startDrag('move', e)}
                style={{
                  position: 'absolute',
                  left: `${crop.x}%`,
                  top: `${crop.y}%`,
                  width: `${crop.width}%`,
                  height: `${crop.height}%`,
                  border: '2px solid #ff5500',
                  boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.3), 0 0 20px rgba(255, 85, 0, 0.3)',
                  cursor: 'move',
                  boxSizing: 'border-box'
                }}
              >
                {/* Rule of Thirds Grid lines */}
                <div
                  style={{
                    position: 'absolute',
                    top: '33.33%',
                    left: 0,
                    right: 0,
                    height: '1px',
                    borderTop: '1px dashed rgba(255, 255, 255, 0.45)',
                    pointerEvents: 'none'
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    top: '66.66%',
                    left: 0,
                    right: 0,
                    height: '1px',
                    borderTop: '1px dashed rgba(255, 255, 255, 0.45)',
                    pointerEvents: 'none'
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    left: '33.33%',
                    top: 0,
                    bottom: 0,
                    width: '1px',
                    borderLeft: '1px dashed rgba(255, 255, 255, 0.45)',
                    pointerEvents: 'none'
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    left: '66.66%',
                    top: 0,
                    bottom: 0,
                    width: '1px',
                    borderLeft: '1px dashed rgba(255, 255, 255, 0.45)',
                    pointerEvents: 'none'
                  }}
                />

                {/* Corner Handles */}
                {/* NW */}
                <div
                  onPointerDown={(e) => startDrag('nw', e)}
                  style={{
                    position: 'absolute',
                    top: -6,
                    left: -6,
                    width: 14,
                    height: 14,
                    backgroundColor: '#ff5500',
                    border: '2px solid #ffffff',
                    borderRadius: '2px',
                    cursor: 'nwse-resize'
                  }}
                />
                {/* NE */}
                <div
                  onPointerDown={(e) => startDrag('ne', e)}
                  style={{
                    position: 'absolute',
                    top: -6,
                    right: -6,
                    width: 14,
                    height: 14,
                    backgroundColor: '#ff5500',
                    border: '2px solid #ffffff',
                    borderRadius: '2px',
                    cursor: 'nesw-resize'
                  }}
                />
                {/* SW */}
                <div
                  onPointerDown={(e) => startDrag('sw', e)}
                  style={{
                    position: 'absolute',
                    bottom: -6,
                    left: -6,
                    width: 14,
                    height: 14,
                    backgroundColor: '#ff5500',
                    border: '2px solid #ffffff',
                    borderRadius: '2px',
                    cursor: 'nesw-resize'
                  }}
                />
                {/* SE */}
                <div
                  onPointerDown={(e) => startDrag('se', e)}
                  style={{
                    position: 'absolute',
                    bottom: -6,
                    right: -6,
                    width: 14,
                    height: 14,
                    backgroundColor: '#ff5500',
                    border: '2px solid #ffffff',
                    borderRadius: '2px',
                    cursor: 'nwse-resize'
                  }}
                />

                {/* Edge Handles */}
                {/* N */}
                <div
                  onPointerDown={(e) => startDrag('n', e)}
                  style={{
                    position: 'absolute',
                    top: -4,
                    left: '50%',
                    transform: 'translateX(-50%)',
                    width: 24,
                    height: 8,
                    backgroundColor: '#ff5500',
                    border: '1px solid #ffffff',
                    borderRadius: '3px',
                    cursor: 'ns-resize'
                  }}
                />
                {/* S */}
                <div
                  onPointerDown={(e) => startDrag('s', e)}
                  style={{
                    position: 'absolute',
                    bottom: -4,
                    left: '50%',
                    transform: 'translateX(-50%)',
                    width: 24,
                    height: 8,
                    backgroundColor: '#ff5500',
                    border: '1px solid #ffffff',
                    borderRadius: '3px',
                    cursor: 'ns-resize'
                  }}
                />
                {/* W */}
                <div
                  onPointerDown={(e) => startDrag('w', e)}
                  style={{
                    position: 'absolute',
                    left: -4,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: 8,
                    height: 24,
                    backgroundColor: '#ff5500',
                    border: '1px solid #ffffff',
                    borderRadius: '3px',
                    cursor: 'ew-resize'
                  }}
                />
                {/* E */}
                <div
                  onPointerDown={(e) => startDrag('e', e)}
                  style={{
                    position: 'absolute',
                    right: -4,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: 8,
                    height: 24,
                    backgroundColor: '#ff5500',
                    border: '1px solid #ffffff',
                    borderRadius: '3px',
                    cursor: 'ew-resize'
                  }}
                />
              </div>
            </div>
          ) : (
            <div style={{ color: 'var(--ink-3)', fontSize: '0.85rem' }}>Loading image...</div>
          )}
        </div>

        {/* Footer Action Bar */}
        <div
          style={{
            padding: '14px 20px',
            backgroundColor: 'var(--sunken)',
            borderTop: '1px solid var(--rule)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '10px',
            flexWrap: 'wrap'
          }}
        >
          <button
            onClick={onCancel}
            disabled={isProcessing}
            className="btn btn-ghost"
            style={{ color: 'var(--ink-2)' }}
          >
            Cancel
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {/* Skip Crop Button */}
            <button
              onClick={handleUseFullImage}
              disabled={isProcessing}
              className="btn"
              style={{
                backgroundColor: 'var(--sunken-2)',
                color: 'var(--ink)',
                border: '1px solid var(--rule)',
                fontWeight: 600
              }}
              title="Scan the full picture without any cropping"
            >
              <ImageIcon size={16} />
              Use Full Image
            </button>

            {/* Main Crop & Scan Button */}
            <button
              onClick={handlePerformCrop}
              disabled={isProcessing}
              className="btn"
              style={{
                background: 'linear-gradient(135deg, #ff5500 0%, #e04400 100%)',
                color: '#ffffff',
                fontWeight: 600,
                boxShadow: '0 4px 14px rgba(255, 85, 0, 0.4)',
                border: 'none',
                minWidth: '140px'
              }}
            >
              {isProcessing ? (
                <>Processing...</>
              ) : (
                <>
                  <Check size={16} />
                  Crop &amp; Scan
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
