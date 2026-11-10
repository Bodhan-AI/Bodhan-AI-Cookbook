// File: src/components/BoundingBoxOverlay.tsx
import React, { useState, useRef, useEffect } from 'react';
import { SemanticBlock } from '../models/SignboardTypes';
import { Volume2, PhoneCall, CheckCircle } from 'lucide-react';

interface BoundingBoxOverlayProps {
  imageUrl: string;
  imageDimensions: { width: number; height: number };
  semanticBlocks: SemanticBlock[];
  activeBlockId?: string | null;
  onSelectBlock: (block: SemanticBlock) => void;
}

export const BoundingBoxOverlay: React.FC<BoundingBoxOverlayProps> = ({
  imageUrl,
  imageDimensions,
  semanticBlocks,
  activeBlockId,
  onSelectBlock
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [renderedDims, setRenderedDims] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0
  });

  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        setRenderedDims({ width: rect.width, height: rect.height });
      }
    };
    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, [imageUrl]);

  const scaleX = imageDimensions.width ? renderedDims.width / imageDimensions.width : 1;
  const scaleY = imageDimensions.height ? renderedDims.height / imageDimensions.height : 1;

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        width: '100%',
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
        backgroundColor: '#000000',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: 'var(--shadow)'
      }}
    >
      {/* Background Compressed Image */}
      <img
        src={imageUrl}
        alt="Captured Signboard"
        onLoad={() => {
          if (containerRef.current) {
            const rect = containerRef.current.getBoundingClientRect();
            setRenderedDims({ width: rect.width, height: rect.height });
          }
        }}
        style={{
          width: '100%',
          height: 'auto',
          maxHeight: '480px',
          objectFit: 'contain',
          display: 'block'
        }}
      />

      {/* Interactive SVG Bounding Box Layer */}
      {renderedDims.width > 0 && renderedDims.height > 0 && (
        <svg
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none'
          }}
          viewBox={`0 0 ${renderedDims.width} ${renderedDims.height}`}
        >
          {semanticBlocks.map((block) => {
            const [x1, y1, x2, y2] = block.bbox_xyxy;
            const rx = x1 * scaleX;
            const ry = y1 * scaleY;
            const rw = Math.max(12, (x2 - x1) * scaleX);
            const rh = Math.max(12, (y2 - y1) * scaleY);
            const isActive = activeBlockId === block.id;

            return (
              <g
                key={block.id}
                style={{
                  pointerEvents: block.isClickable ? 'all' : 'none',
                  cursor: block.isClickable ? 'pointer' : 'default'
                }}
                onClick={() => block.isClickable && onSelectBlock(block)}
              >
                {/* Highlight Rect */}
                <rect
                  x={rx}
                  y={ry}
                  width={rw}
                  height={rh}
                  rx={4}
                  fill={isActive ? `${block.badgeColor}33` : `${block.badgeColor}15`}
                  stroke={block.badgeColor}
                  strokeWidth={isActive ? 3 : 2}
                  strokeDasharray={block.semanticTag === 'DISCARDED_NOISE' ? '4,4' : undefined}
                />

                {/* Floating Category Pill */}
                <foreignObject x={rx} y={Math.max(2, ry - 22)} width={rw + 60} height={24}>
                  <div
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      backgroundColor: block.badgeColor,
                      color: '#ffffff',
                      fontSize: '10px',
                      fontWeight: 700,
                      padding: '1px 6px',
                      borderRadius: '4px',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
                      whiteSpace: 'nowrap'
                    }}
                  >
                    {block.semanticTag === 'CONTACT' ? (
                      <PhoneCall size={10} />
                    ) : block.semanticTag === 'BRAND_NAME' ? (
                      <CheckCircle size={10} />
                    ) : (
                      <Volume2 size={10} />
                    )}
                    <span>{block.badgeLabel.split(' ')[0]}</span>
                  </div>
                </foreignObject>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
};
