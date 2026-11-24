import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

POPPLER_PATH = r"C:\poppler\poppler-26.09.0\Library\bin"
BODHAN_BASE_URL = "https://api.bodhan.ai/v1"


@dataclass
class Settings:
    ocr_key: str
    translate_key: str
    speak_key: str
    transcribe_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str


def load_settings() -> Settings:
    return Settings(
        ocr_key=os.environ["BODHAN_OCR_KEY"],
        translate_key=os.environ["BODHAN_TRANSLATE_KEY"],
        speak_key=os.environ["BODHAN_SPEAK_KEY"],
        transcribe_key=os.environ["BODHAN_TRANSCRIBE_KEY"],
        llm_base_url=os.environ["LLM_BASE_URL"],
        llm_model=os.environ["LLM_MODEL"],
        llm_api_key=os.environ["LLM_API_KEY"],
    )