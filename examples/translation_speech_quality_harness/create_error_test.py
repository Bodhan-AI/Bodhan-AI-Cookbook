import json
from pathlib import Path

record = {
    "id": "error_detection_test_001",
    "source": "नमस्ते, आप कैसे हैं?",
    "translation": "The weather is very cold today.",
    "lang": "hi",
    "source_lang": "Hindi",
    "target_lang": "English"
}

output_path = Path("sample_data/error_detection_test.jsonl")
output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "w", encoding="utf-8") as file:
    file.write(json.dumps(record, ensure_ascii=False) + "\n")

print("Error detection test dataset created successfully.")
print("File:", output_path)
print("Source:", record["source"])
print("Incorrect translation:", record["translation"])