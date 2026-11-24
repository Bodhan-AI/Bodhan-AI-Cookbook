import json
from pathlib import Path

# Hindi text represented using Unicode escape sequences.
hindi_text = "\u0928\u092e\u0938\u094d\u0924\u0947, \u0906\u092a \u0915\u0948\u0938\u0947 \u0939\u0948\u0902?"

records = [
    {
        "id": "test_001",
        "source": hindi_text,
        "lang": "hi",
        "translation": "Hello, how are you?"
    },
    {
        "id": "test_002",
        "source": "The payment amount is Rs 500.",
        "lang": "en",
        "translation": "The payment amount is Rs 800."
    },
    {
        "id": "test_003",
        "source": "The company received a payment from the customer.",
        "lang": "en",
        "translation": "The company received a payment from the customer."
    }
]

output_path = Path("sample_data/pipeline_test.jsonl")

output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "w", encoding="utf-8", newline="\n") as file:
    for record in records:
        file.write(
            json.dumps(record, ensure_ascii=False) + "\n"
        )

print("Dataset successfully repaired!")
print("Saved to:", output_path)

# Verify the saved Hindi text.
with open(output_path, "r", encoding="utf-8") as file:
    first_record = json.loads(file.readline())

print("Verified Hindi text:", first_record["source"])