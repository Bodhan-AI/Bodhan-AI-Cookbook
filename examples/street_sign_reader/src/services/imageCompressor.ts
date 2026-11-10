// File: src/services/imageCompressor.ts

export interface CompressedImageResult {
  dataUrl: string; // Base64 JPEG data URL for Bodhan OCR
  base64Data: string; // Raw base64 string without data prefix
  blob: Blob;
  width: number;
  height: number;
  originalSizeBytes: number;
  compressedSizeBytes: number;
  compressionRatio: string;
}

const ACCEPTED_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp'];
const ACCEPTED_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];
const MAX_ORIGINAL_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB
const MAX_LONGEST_EDGE_PX = 1600;
const JPEG_QUALITY = 0.8;

export function validateImageFile(file: File): { valid: boolean; error?: string } {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  const isValidExt = ACCEPTED_EXTENSIONS.includes(ext);
  const isValidMime = ACCEPTED_MIME_TYPES.includes(file.type) || file.type.startsWith('image/');

  if (!isValidExt && !isValidMime) {
    return {
      valid: false,
      error: `Unsupported format: Only ${ACCEPTED_EXTENSIONS.join(', ')} images are accepted.`
    };
  }

  if (file.size > MAX_ORIGINAL_SIZE_BYTES) {
    return {
      valid: false,
      error: `File too large: Image exceeds 25 MB limit (${(file.size / (1024 * 1024)).toFixed(1)} MB).`
    };
  }

  return { valid: true };
}

/**
 * Compresses an image client-side using Canvas:
 * 1. Clamps longest dimension to <= 1600px
 * 2. Flattens PNG transparency onto solid white #FFFFFF
 * 3. Strips EXIF / GPS metadata
 * 4. Compresses to JPEG 0.80 quality (<= 800 KB)
 */
export async function compressImage(file: File | Blob): Promise<CompressedImageResult> {
  const originalSizeBytes = file.size;

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Failed to read image file'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('Failed to decode image'));
      img.onload = () => {
        let width = img.naturalWidth || img.width;
        let height = img.naturalHeight || img.height;

        // Calculate aspect-preserving clamp to <= 1600px longest edge
        if (width > MAX_LONGEST_EDGE_PX || height > MAX_LONGEST_EDGE_PX) {
          if (width >= height) {
            height = Math.round((height * MAX_LONGEST_EDGE_PX) / width);
            width = MAX_LONGEST_EDGE_PX;
          } else {
            width = Math.round((width * MAX_LONGEST_EDGE_PX) / height);
            height = MAX_LONGEST_EDGE_PX;
          }
        }

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');

        if (!ctx) {
          reject(new Error('Could not initialize canvas context'));
          return;
        }

        // Fill solid white background (prevents transparent PNGs turning black in JPEG)
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, width, height);

        // Draw image (strips EXIF / GPS metadata automatically)
        ctx.drawImage(img, 0, 0, width, height);

        // Export to JPEG 0.80 quality
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error('Canvas compression failed'));
              return;
            }

            const dataUrl = canvas.toDataURL('image/jpeg', JPEG_QUALITY);
            const base64Data = dataUrl.split(',')[1] || '';
            const compressedSizeBytes = blob.size;
            const ratio = ((1 - compressedSizeBytes / originalSizeBytes) * 100).toFixed(0) + '%';

            resolve({
              dataUrl,
              base64Data,
              blob,
              width,
              height,
              originalSizeBytes,
              compressedSizeBytes,
              compressionRatio: ratio
            });
          },
          'image/jpeg',
          JPEG_QUALITY
        );
      };

      img.src = reader.result as string;
    };

    reader.readAsDataURL(file);
  });
}
