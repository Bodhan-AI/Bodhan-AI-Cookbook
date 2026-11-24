
import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai")

def get_client(api_key):
    return OpenAI(
        base_url=BASE + "/v1",
        api_key=api_key
    )

def transcribe_audio(path, language="ta"):

    key = os.getenv("BODHAN_TRANSCRIBE_API_KEY")

    if not key:
        raise RuntimeError("Set BODHAN_TRANSCRIBE_API_KEY in .env first")

    client = get_client(key)

    with open(path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="indic-transcribe",
            file=audio_file,
            language=language
        )

    return result.text

def translate_text(text, target_language="en"):

    key = os.getenv("BODHAN_TRANSLATE_API_KEY")

    if not key:
        raise RuntimeError("Set BODHAN_TRANSLATE_API_KEY in .env first")

    client = get_client(key)

    response = client.chat.completions.create(
        model="indic-translate",
        messages=[
            {
                "role": "user",
                "content": text
            }
        ],
        extra_body={
            "target_language_code": target_language
        }
    )

    result = response.choices[0].message.content

    if not result:
        raise RuntimeError("Translation API returned an empty response")

    return result.strip()

def speak_text(text, language="ta", voice=None):

    key = os.getenv("BODHAN_SPEAK_API_KEY")

    if not key:
        raise RuntimeError(
            "Set BODHAN_SPEAK_API_KEY in .env first"
        )

    language_voices = {
        "ta": "Arun", 
        "te": "Vamsi", 
        "hi": "Kavya", 
        "en": "Arun" 
    }

    selected_voice = voice or language_voices.get(language, "Arun")

    client = OpenAI(
        base_url=BASE + "/v1",
        api_key=key
    )

    response = client.audio.speech.create(
        model="indic-speak",
        input=text,
        voice=selected_voice,
        instructions=json.dumps({
            "lang": language
        })
    )

    return response.content

def extract_report_details(text):

    key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    if not key:
        raise RuntimeError("Set GROQ_API_KEY in .env first")

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=key
    )

    prompt = f"""
You are an information extraction assistant for an agricultural
reporting application.

Extract information from the farmer's report below.

REPORT:
{text}

Extract these four fields:

1. crop:
   The crop being affected.

2. suspected_disease:
   The disease or pest explicitly mentioned or suspected in the report.
   If no disease or pest is mentioned, return Unknown.

3. symptoms:
   Describe the symptoms reported by the farmer.
   Do not add symptoms that were not mentioned.

4. region:
   The village, town, district or region mentioned.
   If no location is mentioned, return Unknown.

Rules:
- Extract information only from the report.
- Do not hallucinate or invent information.
- Keep all field values in English.
- Return Unknown for missing information.
- Do not provide diagnosis or treatment recommendations.
- Return only valid JSON.
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract structured agricultural "
                    "information accurately. Never invent "
                    "details. Return valid JSON only."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0,
        response_format={
            "type": "json_object"
        }
    )

    raw = response.choices[0].message.content

    print("GROQ EXTRACTION RESPONSE:", repr(raw))

    if not raw:
        raise RuntimeError("Groq returned an empty response")

    details = json.loads(raw)

    fields = [
        "crop",
        "suspected_disease",
        "symptoms",
        "region"
    ]
    for field in fields:

        value = details.get(field, "Unknown")

        if value is None or not str(value).strip():
            value = "Unknown"

        details[field] = str(value).strip()
    return {
        field: details[field]
        for field in fields
    }
