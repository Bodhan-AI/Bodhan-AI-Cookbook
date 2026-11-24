import asyncio
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def test_model(model_name: str):
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    api_key = os.getenv("LLM_API_KEY", "")
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    print(f"Testing model: {model_name}")
    try:
        resp = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Output valid JSON only."},
                {"role": "user", "content": "Return a JSON object with key 'status' set to 'ok' and key 'message' set to 'working'."}
            ],
            temperature=0.0,
        )
        print(f"[{model_name}] Response: {resp.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"[{model_name}] Error: {e}")
        return False

async def main():
    for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
        await test_model(m)

if __name__ == "__main__":
    asyncio.run(main())
