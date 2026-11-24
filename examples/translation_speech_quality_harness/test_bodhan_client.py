from bodhan_eval.bodhan_client import BodhanClient

print("=" * 50)
print("INDICEVAL - BODHAN CLIENT TEST")
print("=" * 50)

client = BodhanClient()

# Test 1: Translation
print("\n[TEST 1] Indic-Translate")

result = client.translate(
    text="नमस्ते, आप कैसे हैं?",
    target_language="en"
)

print("Hindi:", "नमस्ते, आप कैसे हैं?")
print("English:", result["translation"])
print("Model:", result["model"])
print("Usage:", result["usage"])
print("Cached:", result["cached"])

print("\nTranslation test completed successfully!")

# Test 2: Configuration
print("\n[TEST 2] Checking client configuration")

print("Base URL:", client.base_url)
print("Translation key configured:", bool(client.translate_key))
print("Speech key configured:", bool(client.speak_key))
print("Transcription key configured:", bool(client.transcribe_key))

print("\nAll checks completed!")