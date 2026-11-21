import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BODHAN_TRANSLATE_KEY")
BASE_URL = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

if not API_KEY:
    raise ValueError("BODHAN_TRANSLATE_KEY missing from .env")

url = f"{BASE_URL.rstrip('/')}/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

payload = {
    "model": "indic-translate",
    "messages": [
        {
            "role": "user",
            "content": "नमस्ते, आप कैसे हैं?"
        }
    ],
    "target_language_code": "en"
}

print("=" * 50)
print("BODHAN TRANSLATION API TEST")
print("=" * 50)

try:
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=45
    )

    print("\nStatus:", response.status_code)
    print("Request ID:", response.headers.get("x-request-id"))
    print("Response:", response.text)

    if response.status_code == 200:
        data = response.json()

        translation = data["choices"][0]["message"]["content"]

        print("\nSUCCESS!")
        print("Hindi:", payload["messages"][0]["content"])
        print("English:", translation)

    else:
        print("\nAPI request failed.")

except requests.exceptions.RequestException as error:
    print("\nConnection error:", error)