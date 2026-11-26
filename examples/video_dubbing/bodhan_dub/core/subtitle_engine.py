from pathlib import Path

from bodhan_dub.models.segment import DubSegment


class SubtitleEngine:
    """Generates standard SRT and WebVTT subtitles from translated segments."""

    @staticmethod
    def format_timestamp_srt(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            millis = 999
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def format_timestamp_vtt(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            millis = 999
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    def generate_srt(self, segments: list[DubSegment], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for index, seg in enumerate(segments, start=1):
            text = seg.translated_text.strip() or seg.source_text.strip()
            start_str = self.format_timestamp_srt(seg.start)
            end_str = self.format_timestamp_srt(seg.end)
            lines.append(f"{index}\n{start_str} --> {end_str}\n{text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).strip() + "\n")
        return output_path

    def generate_vtt(self, segments: list[DubSegment], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["WEBVTT\n"]
        for index, seg in enumerate(segments, start=1):
            text = seg.translated_text.strip() or seg.source_text.strip()
            start_str = self.format_timestamp_vtt(seg.start)
            end_str = self.format_timestamp_vtt(seg.end)
            lines.append(f"{index}\n{start_str} --> {end_str}\n{text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).strip() + "\n")
        return output_path
