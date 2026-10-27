import { forwardRef } from "react";

interface AudioPlayerProps {
  src: string;
  fileName: string;
  onTimeUpdate: (seconds: number) => void;
  onPlay: () => void;
  onError: () => void;
}

export const AudioPlayer = forwardRef<HTMLAudioElement, AudioPlayerProps>(function AudioPlayer(
  { src, fileName, onTimeUpdate, onPlay, onError },
  ref,
) {
  return (
    <div className="audio-player">
      <audio
        ref={ref}
        className="audio-player__audio"
        controls
        preload="metadata"
        src={src}
        aria-label={`Recording: ${fileName}`}
        onTimeUpdate={(e) => onTimeUpdate(e.currentTarget.currentTime)}
        onSeeked={(e) => onTimeUpdate(e.currentTarget.currentTime)}
        onPlay={onPlay}
        onError={onError}
      />
      <p className="audio-player__hint">Select a timestamp in the transcript to jump to that point.</p>
    </div>
  );
});
