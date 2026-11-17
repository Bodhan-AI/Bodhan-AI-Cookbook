#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env file
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bodhan_mcp.client import client, InrLedger


async def test_stt():
    print("\n" + "="*60)
    print("TESTING STT (indic-transcribe)")
    print("="*60)
    
    # Use sample audio file from root sample_data
    sample_path = Path(__file__).resolve().parent.parent.parent / "sample_data" / "sample_hindi.wav"
    if not sample_path.exists():
        print(f"Sample file not found: {sample_path}")
        return None
    
    with open(sample_path, "rb") as f:
        audio_data = f.read()
    
    files = {
        "file": ("sample_hindi.wav", audio_data, "audio/wav"),
    }
    json_data = {
        "model": "indic-transcribe",
        "language": "hi",
    }
    
    try:
        result, cost = await client.request(
            "indic-transcribe",
            "/v1/audio/transcriptions",
            json_data=json_data,
            files=files,
            timeout=60,
        )
        print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
        print(f"Cost: ?{cost:.4f}")
        return result, cost
    except Exception as e:
        print(f"STT Error: {e}")
        return None


async def test_tts():
    print("\n" + "="*60)
    print("TESTING TTS (indic-speak)")
    print("="*60)
    
    json_data = {
        "model": "indic-speak",
        "input": "??????, ?? ?? ??????? ???",
        "voice": "Kavya",
        "instructions": json.dumps({"lang": "hi"}),
        "response_format": "wav",
    }
    
    try:
        result, cost = await client.request(
            "indic-speak",
            "/v1/audio/speech",
            json_data=json_data,
            timeout=60,
        )
        print(f"Response keys: {list(result.keys()) if isinstance(result, dict) else 'non-dict'}")
        if isinstance(result, dict) and "audio" in result:
            print(f"Audio data length: {len(result['audio'])} chars (base64)")
        print(f"Cost: ?{cost:.4f}")
        return result, cost
    except Exception as e:
        print(f"TTS Error: {e}")
        return None


async def test_translate():
    print("\n" + "="*60)
    print("TESTING TRANSLATE (indic-translate)")
    print("="*60)
    
    json_data = {
        "text": "Hello, how are you?",
        "source_language": "en",
        "target_language": "hi",
    }
    
    try:
        result, cost = await client.request(
            "indic-translate",
            "/translate",
            json_data=json_data,
            timeout=60,
        )
        print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
        print(f"Cost: ?{cost:.4f}")
        return result, cost
    except Exception as e:
        print(f"Translate Error: {e}")
        return None


async def test_ocr():
    print("\n" + "="*60)
    print("TESTING OCR (indic-ocr)")
    print("="*60)
    
    # Create a simple test image with text
    try:
        from PIL import Image, ImageDraw, ImageFont
        import base64
        import tempfile
        
        # Create a simple image with text
        img = Image.new('RGB', (400, 200), color='white')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except:
            font = ImageFont.load_default()
        draw.text((20, 50), "Hello World\n?????? ??????", fill='black', font=font)
        
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name, "PNG")
            with open(tmp.name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            os.unlink(tmp.name)
    except Exception as e:
        print(f"Could not create test image: {e}")
        return None
    
    json_data = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ]}],
        "table_format": "markdown",
    }
    
    try:
        result, cost = await client.request(
            "indic-ocr",
            "/v1/chat/completions",
            json_data=json_data,
            timeout=120,
        )
        print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
        print(f"Cost: ?{cost:.4f}")
        return result, cost
    except Exception as e:
        print(f"OCR Error: {e}")
        return None


async def main():
    print("Bodhan AI Smoke Test")
    print("Testing all 4 APIs with minimal cost")
    
    # Check API keys
    keys = {
        "BODHAN_TRANSCRIBE_KEY": os.environ.get("BODHAN_TRANSCRIBE_KEY"),
        "BODHAN_SPEAK_KEY": os.environ.get("BODHAN_SPEAK_KEY"),
        "BODHAN_TRANSLATE_KEY": os.environ.get("BODHAN_TRANSLATE_KEY"),
        "BODHAN_OCR_KEY": os.environ.get("BODHAN_OCR_KEY"),
    }
    
    print("\nAPI Keys configured:")
    for k, v in keys.items():
        print(f"  {k}: {'?' if v else '?'}")
    
    if not all(keys.values()):
        print("\nWarning: Some API keys are missing. Tests may fail.")
    
    total_cost = 0.0
    results = {}
    
    # Test STT
    stt_result = await test_stt()
    if stt_result:
        results["stt"] = stt_result
        total_cost += stt_result[1]
    
    # Test TTS
    tts_result = await test_tts()
    if tts_result:
        results["tts"] = tts_result
        total_cost += tts_result[1]
    
    # Test Translate
    translate_result = await test_translate()
    if translate_result:
        results["translate"] = translate_result
        total_cost += translate_result[1]
    
    # Test OCR
    ocr_result = await test_ocr()
    if ocr_result:
        results["ocr"] = ocr_result
        total_cost += ocr_result[1]
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total estimated cost: ?{total_cost:.4f}")
    print(f"Tests passed: {len(results)}/4")
    
    if total_cost < 0.30:
        print("? Total cost under ?0.30 target")
    else:
        print(f"? Total cost ?{total_cost:.4f} exceeds ?0.30 target")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())
