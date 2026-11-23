import argparse
import asyncio
import io
import os
import sys
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv

RECIPE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RECIPE_DIR.parents[1]
sys.path.insert(0, str(RECIPE_DIR / "src"))

from bodhan_eval.budget import Budget  # noqa: E402
from bodhan_eval.cache import Cache  # noqa: E402
from bodhan_eval.client.bodhan import BodhanClient  # noqa: E402
from bodhan_eval.client.errors import BodhanAPIError  # noqa: E402
from bodhan_eval.client.llm import LLMClient  # noqa: E402

KEY_NAMES = (
    "BODHAN_TRANSLATE_KEY",
    "BODHAN_SPEAK_KEY",
    "BODHAN_TRANSCRIBE_KEY",
    "LLM_API_KEY",
)


def inspect_keys() -> dict[str, dict[str, object]]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    report: dict[str, dict[str, object]] = {}
    values: dict[str, str] = {}
    for name in KEY_NAMES:
        value = os.getenv(name)
        values[name] = value or ""
        report[name] = {
            "present": bool(value),
            "length": len(value or ""),
            "leading_whitespace": bool(value and value[:1].isspace()),
            "trailing_whitespace": bool(value and value[-1:].isspace()),
            "surrounding_quotes": bool(
                value
                and len(value) >= 2
                and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'"))
            ),
            "has_cr": "\r" in (value or ""),
            "has_bom": (value or "").startswith("\ufeff"),
        }

    identical: dict[str, list[str]] = {}
    for name, value in values.items():
        if value:
            identical.setdefault(value, []).append(name)
    report["_identical_values"] = {
        "pairs": sorted(
            sorted(names)
            for names in identical.values()
            if len(names) > 1
        )
    }
    return report


def _one_second_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 24000)
    return buffer.getvalue()


async def live_probes(max_cost_inr: float) -> None:
    values = {
        name: os.getenv(name)
        for name in KEY_NAMES
    }
    budget = Budget(max_cost_inr)
    cache_dir = RECIPE_DIR / ".cache" / "key-diagnostics"

    if values["BODHAN_TRANSLATE_KEY"]:
        try:
            async with BodhanClient(
                values["BODHAN_TRANSLATE_KEY"] or "",
                "",
                "",
                Cache(cache_dir),
                budget,
            ) as client:
                await client.translate("three words", "hi", max_output_tokens=16)
            print("bodhan_translate=status_ok")
        except BodhanAPIError as error:
            print(
                f"bodhan_translate=status={error.status} "
                f"code={error.code or ''} request_id={error.request_id or ''}"
            )
        except httpx.HTTPError:
            print("bodhan_translate=status_transport_error")
    else:
        print("bodhan_translate=status_missing_key")

    if values["BODHAN_SPEAK_KEY"]:
        try:
            async with BodhanClient(
                "",
                values["BODHAN_SPEAK_KEY"] or "",
                "",
                Cache(cache_dir),
                budget,
            ) as client:
                await client.speak("A", "Kavya", "hi")
            print("bodhan_speak=status_ok")
        except BodhanAPIError as error:
            print(
                f"bodhan_speak=status={error.status} "
                f"code={error.code or ''} request_id={error.request_id or ''}"
            )
        except httpx.HTTPError:
            print("bodhan_speak=status_transport_error")
    else:
        print("bodhan_speak=status_missing_key")

    if values["BODHAN_TRANSCRIBE_KEY"]:
        try:
            async with BodhanClient(
                "",
                "",
                values["BODHAN_TRANSCRIBE_KEY"] or "",
                Cache(cache_dir),
                budget,
            ) as client:
                await client.transcribe(_one_second_wav(), "hi", duration_s=1.0)
            print("bodhan_transcribe=status_ok")
        except BodhanAPIError as error:
            print(
                f"bodhan_transcribe=status={error.status} "
                f"code={error.code or ''} request_id={error.request_id or ''}"
            )
        except httpx.HTTPError:
            print("bodhan_transcribe=status_transport_error")
    else:
        print("bodhan_transcribe=status_missing_key")

    llm_base = os.getenv("LLM_BASE_URL")
    llm_model = os.getenv("LLM_MODEL")
    llm_key = values["LLM_API_KEY"]
    if llm_base and llm_model and llm_key:
        try:
            async with LLMClient(
                llm_base,
                llm_model,
                llm_key,
                Cache(cache_dir),
            ) as client:
                await client.chat(
                    "Return exactly one word.",
                    "Reply.",
                    temperature=0.0,
                )
            print("llm=status_ok")
        except httpx.HTTPStatusError as error:
            request_id = error.response.headers.get("x-request-id", "")
            print(
                f"llm=status={error.response.status_code} "
                f"code=none request_id={request_id}"
            )
        except (httpx.HTTPError, ValueError):
            print("llm=status_transport_or_response_error")
    else:
        print("llm=status_missing_configuration")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cost-inr", type=float, default=3.0)
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env", override=False)

    report = inspect_keys()
    for name in KEY_NAMES:
        details = report[name]
        print(
            f"{name}:present={details['present']};"
            f"length={details['length']};"
            f"leading_whitespace={details['leading_whitespace']};"
            f"trailing_whitespace={details['trailing_whitespace']};"
            f"surrounding_quotes={details['surrounding_quotes']};"
            f"cr={details['has_cr']};bom={details['has_bom']}"
        )
    print("identical_values=" + repr(report["_identical_values"]["pairs"]))
    if args.live:
        if args.max_cost_inr < 0:
            parser.error("--max-cost-inr must be non-negative")
        asyncio.run(live_probes(args.max_cost_inr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
