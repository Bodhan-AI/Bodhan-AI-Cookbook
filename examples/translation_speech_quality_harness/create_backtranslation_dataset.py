import json
from pathlib import Path

hindi_text = (
    "\u0928\u092e\u0938\u094d\u0924\u0947, "
    "\u0906\u092a "
    "\u0915\u0948\u0938\u0947 "
    "\u0939\u0948\u0902?"
)

record = {
    "id": "backtranslation_test_001",
    "source": hindi_text,
    "translation": "Hello, how are you?",
    "lang": "hi",
    "source_lang": "hi",
    "target_lang": "en"
}

output_path = Path("sample_data/backtranslation_test.jsonl")

with open(output_path, "w", encoding="utf-8") as file:
    file.write(json.dumps(record, ensure_ascii=False) + "\n")

print("Back-translation test dataset created!")
print("Original Hindi:", hindi_text)
print("English translation:", record["translation"])