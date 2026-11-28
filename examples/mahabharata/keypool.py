"""Round-robin Groq key pool: every call to new_client() returns a fresh Groq client on the next key.

Keys come from .env (never hard-coded): GROQ_API_KEY (the original) plus GROQ_API_KEYS (comma-separated).
"""
import os
import threading

from groq import Groq


def _load_keys():
    keys = [os.getenv("GROQ_API_KEY", "")] + os.getenv("GROQ_API_KEYS", "").split(",")
    seen, out = set(), []
    for k in (k.strip() for k in keys):
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


_lock = threading.Lock()
_keys = None
_ptr = 0  # cyclic pointer into _keys


def new_client():
    """Create a Groq client using the next key in the cycle (thread-safe)."""
    global _keys, _ptr
    with _lock:
        if _keys is None:
            _keys = _load_keys()
        if not _keys:
            raise RuntimeError("No Groq API keys found: set GROQ_API_KEY / GROQ_API_KEYS in .env")
        key = _keys[_ptr]
        _ptr = (_ptr + 1) % len(_keys)
    return Groq(api_key=key)
