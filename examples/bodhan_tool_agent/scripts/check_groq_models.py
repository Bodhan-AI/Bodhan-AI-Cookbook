import asyncio
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def list_all_models():
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    api_key = os.getenv("LLM_API_KEY", "")
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    models = await client.models.list()
    print("ALL models returned by endpoint:")
    for m in models.data:
        print(f"  - {m.id}")

if __name__ == "__main__":
    asyncio.run(list_all_models())
