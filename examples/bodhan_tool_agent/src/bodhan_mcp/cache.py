import hashlib
import json
import pathlib

# .cache folder sits in the project folder, no matter where you run from
CACHE_DIR = pathlib.Path(__file__).resolve().parents[2] / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def make_key(tool: str, **inputs) -> str:
    payload = json.dumps([tool, inputs], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def get(key: str):
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def put(key: str, value) -> None:
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")