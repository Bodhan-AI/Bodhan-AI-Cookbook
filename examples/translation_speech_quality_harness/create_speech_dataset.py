import json
from pathlib import Path

# Hindi text represented using Unicode escape sequences
# This avoids Windows terminal/source-file encoding issues.
hindi_text = (
    "\u0928\u092e\u0938\u094d\u0924\u0947, "
    "\u0906\u092a "
    "\u0915\u0948\u0938\u0947 "
    "\u0939\u0948\u0902?"
)

record = {
    "id": "speech_test_001",
    "source": hindi_text,
    "translation": hindi_text,
    "lang": "hi",
    "audio": "outputs/speech_api_test.wav"
}

output_path = Path("sample_data/speech_pipeline_test.jsonl")
output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "w", encoding="utf-8") as file:
    file.write(json.dumps(record, ensure_ascii=False) + "\n")

print("Dataset created successfully!")
print("Hindi text:", hindi_text)
print("Dataset:", output_path)