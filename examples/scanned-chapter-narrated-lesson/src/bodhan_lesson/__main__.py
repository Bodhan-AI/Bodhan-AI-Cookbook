import argparse
from pathlib import Path
from .config import load_settings
from .ocr import run_ocr_on_pdf
from .lesson import build_lesson_from_ocr
from .translate import translate_lesson
from .audio import synthesize_lesson_audio
from .export import write_markdown_lesson


def main():
    parser = argparse.ArgumentParser(description="Bodhan Narrated Lesson CLI")
    parser.add_argument("--pdf", required=True, type=Path, help="Input PDF path")
    parser.add_argument("--pages", default="1-2", help="Page range (e.g., 1-4, 1,3,5, or all)")
    parser.add_argument("--lang", default="hi", help="Target ISO language code (e.g., en, hi, mr, ta)")
    parser.add_argument("--voice", default="Kavya", help="Bodhan indic-speak voice name")
    parser.add_argument("--verify-asr", action="store_true", help="Verify generated audio via indic-transcribe")
    parser.add_argument("--out", default="outputs/test_run", type=Path, help="Output directory")
    args = parser.parse_args()

    settings = load_settings()

    # Stage 1: Scanned PDF -> indic-ocr
    ocr_pages = run_ocr_on_pdf(args.pdf, args.out, settings.ocr_key, args.pages)

    # Stage 2: OCR -> LLM Structured Lesson & Narration Script
    lesson = build_lesson_from_ocr(ocr_pages, args.out, settings)

    # Stage 3: Multilingual Translation -> indic-translate
    final_lesson = translate_lesson(lesson, args.lang, args.out, settings.translate_key)

    # Stage 4: Audio Narration -> indic-speak (+ optional indic-transcribe verification)
    audio_paths = synthesize_lesson_audio(
        lesson=final_lesson,
        target_lang=args.lang,
        voice=args.voice,
        out_dir=args.out,
        speak_key=settings.speak_key,
        transcribe_key=settings.transcribe_key,
        verify_asr=args.verify_asr,
    )

    # Export final Markdown study guide
    md_path = write_markdown_lesson(final_lesson, args.lang, audio_paths, args.out)

    print("\n=== Pipeline Complete ===")
    print(f"Markdown Lesson : {md_path}")
    print(f"Full Audio      : {audio_paths['full']}")
    print(f"Section Audios  : {len(final_lesson.sections)} WAV files in {audio_paths['full'].parent}")


if __name__ == "__main__":
    main()