"""MCP Prompts providing reusable workflow templates for common multilingual tasks."""

from __future__ import annotations


def get_audio_summary_pipeline_prompt(topic: str, target_language: str) -> str:
    """Template for generating a concise audio summary from a spoken audio file."""
    return f"""You are an autonomous multilingual assistant. Follow this pipeline strictly:
1. Inspect the input audio file and call `transcribe_audio` to produce a full transcript.
2. Formulate a clear, structured summary in English capturing the key points on '{topic}'.
3. Call `translate_text` with target_language='{target_language}' to translate the summary into {target_language}.
4. Call `synthesize_speech` with language='{target_language}' using an appropriate native voice to produce the final audio.
5. Verify that the synthesized audio artifact is non-empty, and verify the transcript against the summary.
"""


def get_document_translation_pipeline_prompt(target_language: str) -> str:
    """Template for translating a scanned document or PDF into an Indic language."""
    return f"""You are an autonomous document translation assistant. Follow this pipeline strictly:
1. Call `extract_text_from_document` to OCR the input document and extract structured Markdown.
2. Verify that all pages were rasterized and extracted successfully without page omissions.
3. Call `translate_text` with target_language='{target_language}', preserving Markdown formatting.
4. Save the translated document to an artifact file and verify that all sections were translated.
"""


def get_multilingual_subtitling_prompt(source_language: str, target_languages: list[str]) -> str:
    """Template for transcribing audio and generating SRT subtitles in multiple languages."""
    langs_str = ", ".join(target_languages)
    return f"""You are an autonomous subtitling assistant. Follow this pipeline strictly:
1. Call `transcribe_audio` with language='{source_language}' to obtain the timestamped transcript.
2. Format the transcription segments into standard SRT subtitle format.
3. For each target language in [{langs_str}], call `translate_text` to translate each subtitle line.
4. Save the respective .srt files and verify timestamp monotonicity and structural formatting.
"""
