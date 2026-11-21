import soundfile as sf
from pathlib import Path

from bodhan_eval.bodhan_client import BodhanClient


def main():
    # Initialize Bodhan API client
    client = BodhanClient()

    # Short Hindi sentence to minimize API usage
    text = "नमस्ते, आप कैसे हैं?"
    language = "hi"

    # Output audio file
    audio_path = Path("outputs") / "speech_api_test.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    # STEP 1: Generate speech using Indic-Speak
    print("\n========== STEP 1: INDIC-SPEAK ==========")
    print("Input text:", text)

    speech_result = client.speak(
        text=text,
        language=language,
        voice="Kavya",
        output_path=audio_path
    )

    print("Speech generated successfully!")
    print("Audio path:", speech_result["audio_path"])
    print("Audio size:", speech_result["size_bytes"], "bytes")
    print("Cached:", speech_result["cached"])

    # STEP 2: Inspect generated audio
    print("\n========== STEP 2: AUDIO INSPECTION ==========")

    audio, sample_rate = sf.read(str(audio_path))
    duration = len(audio) / sample_rate

    print("Sample rate:", sample_rate, "Hz")
    print("Duration:", round(duration, 2), "seconds")
    print("Audio shape:", audio.shape)

    # STEP 3: Transcribe audio using Indic-Transcribe
    print("\n========== STEP 3: INDIC-TRANSCRIBE ==========")

    transcription_result = client.transcribe(
        audio_path=audio_path,
        language=language
    )

    transcript = transcription_result["transcript"]

    print("Expected text:", text)
    print("Recognized text:", transcript)
    print("Cached:", transcription_result["cached"])

    # STEP 4: Final result
    print("\n========== TEST COMPLETED ==========")

    if transcript.strip():
        print("SUCCESS: Speech generation and transcription completed.")
    else:
        print("WARNING: Transcription returned empty text.")


if __name__ == "__main__":
    main()