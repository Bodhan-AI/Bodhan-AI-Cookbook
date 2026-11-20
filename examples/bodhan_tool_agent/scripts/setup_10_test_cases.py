import shutil
from pathlib import Path

base_dir = Path("demo/inputs/test_suite_10")
base_dir.mkdir(parents=True, exist_ok=True)

# 1. Audio Transcribe
c1 = base_dir / "case_01_audio_transcribe"
c1.mkdir(exist_ok=True)
shutil.copy("demo/inputs/audio_formats/speech_16k.wav", c1 / "speech_hindi.wav")

# 2. Audio Transcribe + Translate
c2 = base_dir / "case_02_audio_transcribe_translate"
c2.mkdir(exist_ok=True)
shutil.copy("demo/inputs/audio_formats/speech_compressed.mp3", c2 / "speech_compressed.mp3")

# 3. Text Translate (Hindi to Tamil)
c3 = base_dir / "case_03_text_translate"
c3.mkdir(exist_ok=True)
(c3 / "press_release_hindi.txt").write_text(
    "भारत सरकार ने आज नई डिजिटल शिक्षा नीति की घोषणा की है। यह नीति सभी छात्रों के लिए सुलभ होगी।",
    encoding="utf-8"
)

# 4. Text to Speech
c4 = base_dir / "case_04_text_tts"
c4.mkdir(exist_ok=True)
(c4 / "announcement_tamil.txt").write_text(
    "வணக்கம், இன்றைய செய்தி அறிக்கைக்கு உங்களை வரவேற்கிறோம்.",
    encoding="utf-8"
)

# 5. Document OCR
c5 = base_dir / "case_05_doc_ocr"
c5.mkdir(exist_ok=True)
shutil.copy("demo/inputs/task_1/scanned_report_8pages.pdf", c5 / "legal_circular.pdf")

# 6. Image OCR
c6 = base_dir / "case_06_image_ocr"
c6.mkdir(exist_ok=True)
shutil.copy("demo/inputs/task_3/worksheet_table.png", c6 / "receipt_table.png")

# 7. Full Pipeline (OCR -> Translate -> TTS)
c7 = base_dir / "case_07_full_pipeline"
c7.mkdir(exist_ok=True)
shutil.copy("demo/inputs/task_1/scanned_report_8pages.pdf", c7 / "annual_report.pdf")

# 8. Audio Chunking
c8 = base_dir / "case_08_audio_chunking"
c8.mkdir(exist_ok=True)
shutil.copy("demo/inputs/audio_formats/speech_long_45s.wav", c8 / "lecture_long_45s.wav")

# 9. Missing File (Impossible task)
c9 = base_dir / "case_09_missing_file"
c9.mkdir(exist_ok=True)
shutil.copy("demo/inputs/audio_formats/speech_16k.wav", c9 / "present_file.wav")

# 10. Unsupported Language (Impossible task)
c10 = base_dir / "case_10_unsupported_language"
c10.mkdir(exist_ok=True)
shutil.copy("demo/inputs/task_1/scanned_report_8pages.pdf", c10 / "document.pdf")

print("Created all 10 test case folders and files in demo/inputs/test_suite_10!")
