import sys

modules = ['mcp', 'openai', 'fitz', 'soundfile', 'PIL', 'pydantic', 'dotenv', 'httpx', 'pytest']
for mod in modules:
    try:
        __import__(mod)
        print(f"{mod}: OK")
    except Exception as e:
        print(f"{mod}: FAIL - {e}")

