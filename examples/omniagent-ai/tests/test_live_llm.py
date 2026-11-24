
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("LLM_API_KEY")
base_url = os.getenv("LLM_BASE_URL")
model = os.getenv("LLM_MODEL")

if not all([api_key, base_url, model]):
    raise ValueError(
        "Missing LLM_API_KEY, LLM_BASE_URL or LLM_MODEL in .env"
    )

client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=60.0,
)

print("Connecting to Gemini...")
print(f"Model: {model}")

response = client.chat.completions.create(
    model=model,
    messages=[
        {
            "role": "system",
            "content": (
                "You are the tool-selection planner for OmniAgent AI. "
                "Select exactly one tool from: "
                "extract_text, translate_text, "
                "transcribe_audio, generate_speech. "
                'Return a JSON object such as {"tool": "extract_text"}.'
            ),
        },
        {
            "role": "user",
            "content": "Which tool extracts text from an image?",
        },
    ],
    response_format={"type": "json_object"},
    max_tokens=500,
)

choice = response.choices[0]
answer = choice.message.content

print("\nRaw Gemini response:")
print(repr(answer))
print("Finish reason:", choice.finish_reason)

if not answer:
    raise AssertionError(
        "Gemini returned no text. Check the finish reason."
    )

data = json.loads(answer)

print("\nSelected tool:", data.get("tool"))

assert data.get("tool") == "extract_text", (
    f"Expected extract_text, received {data.get('tool')}"
)

print("\nPASS: Gemini API connection successful!")
print("PASS: Gemini selected the correct MCP tool!")
