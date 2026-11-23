import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.bodhan.ai"


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable {name}. See .env.example.")
    return value