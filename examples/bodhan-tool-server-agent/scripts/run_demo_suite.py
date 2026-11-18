"""Automated Demo Suite for Bodhan Tool Server and Task Agent.

Runs 8 comprehensive demonstration tasks covering:
1. Speech-to-Text & Translation (Hindi -> Kannada)
2. Speech-to-Speech Localization (Hindi -> Tamil)
3. Document OCR & Markdown Translation (Image -> Telugu)
4. Document OCR to Audio Narration (Image -> Bengali -> Speech)
5. Audio Windowing for Long Files (>30s audio chunking and merging)
6. Impossible Task: Unsupported Language (French - halts cleanly)
7. Impossible Task: Missing Input File (halts cleanly without fabrication)
8. Budget Limit Protection (halts cleanly on insufficient INR budget)
"""

from __future__ import annotations

import asyncio
import math
import struct
import time
import wave
from pathlib import Path
from typing import List, Tuple

from PIL import Image

from bodhan_agent.models import ExecutionStatus, RunReport
from bodhan_agent.runner import AgentRunner


def generate_wav(path: Path, duration_sec: float, sample_rate: int = 16000) -> Path:
    """Generate valid PCM WAV audio using Python's standard library wave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = bytearray()
        for i in range(num_samples):
            val = int(16000.0 * math.sin(2.0 * math.pi * 440.0 * (i / sample_rate)))
            data.extend(struct.pack("<h", val))
        wf.writeframes(data)
    return path


def generate_image(path: Path, width: int = 600, height: int = 300) -> Path:
    """Generate valid document image using Pillow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=(250, 250, 250))
    img.save(path)
    return path


async def run_all_demos() -> List[Tuple[str, RunReport]]:
    workspace_dir = Path("runs/demo_suite").resolve()
    media_dir = Path("runs/demo_media").resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("  Bodhan AI Cookbook: Automated Task Agent & Tool Server Demo Suite")
    print("=" * 70 + "\n")

    # 1. Prepare synthetic input media
    print("[1/3] Preparing test media artifacts (WAV & PNG)...")
    hindi_wav = generate_wav(media_dir / "lecture_hindi_12s.wav", duration_sec=12.0)
    welcome_wav = generate_wav(media_dir / "welcome_hindi_8s.wav", duration_sec=8.0)
    long_wav = generate_wav(media_dir / "seminar_english_65s.wav", duration_sec=65.0)
    notice_png = generate_image(media_dir / "exam_notice.png")
    announcement_png = generate_image(media_dir / "school_announcement.png")
    missing_wav = media_dir / "non_existent_file.wav"

    print("      Test media ready on disk.\n")

    # 2. Define demo tasks
    tasks = [
        (
            "Task 1: Speech-to-Text & Translation (Hindi -> Kannada)",
            f"Transcribe {hindi_wav.name} in Hindi, and translate the transcript to Kannada",
            [str(hindi_wav)],
            workspace_dir / "task_1_speech_translate",
            30,
            20.0,
            ExecutionStatus.SUCCESS,
        ),
        (
            "Task 2: Speech-to-Speech Localization (Hindi -> Tamil)",
            f"Transcribe {welcome_wav.name} in Hindi, translate to Tamil, and synthesize speech in Tamil",
            [str(welcome_wav)],
            workspace_dir / "task_2_speech_to_speech",
            30,
            20.0,
            ExecutionStatus.SUCCESS,
        ),
        (
            "Task 3: Document OCR & Markdown Translation (Image -> Telugu)",
            f"Extract text from {notice_png.name} using OCR, and translate into Telugu preserving Markdown",
            [str(notice_png)],
            workspace_dir / "task_3_ocr_translate",
            30,
            20.0,
            ExecutionStatus.SUCCESS,
        ),
        (
            "Task 4: Document OCR to Speech Narration (Image -> Bengali -> Speech)",
            f"Extract text from {announcement_png.name} using OCR, translate to Bengali, and synthesize speech in Bengali",
            [str(announcement_png)],
            workspace_dir / "task_4_ocr_to_speech",
            30,
            20.0,
            ExecutionStatus.SUCCESS,
        ),
        (
            "Task 5: Long Audio Windowing (>30s Chunks -> Marathi Translation)",
            f"Transcribe {long_wav.name} in English, window into <=30s chunks, merge transcripts, and translate to Marathi",
            [str(long_wav)],
            workspace_dir / "task_5_long_audio_windowing",
            30,
            20.0,
            ExecutionStatus.SUCCESS,
        ),
        (
            "Task 6: Impossible Task — Unsupported Language (French)",
            f"Transcribe {welcome_wav.name} in Hindi and translate to French",
            [str(welcome_wav)],
            workspace_dir / "task_6_impossible_language",
            30,
            20.0,
            ExecutionStatus.IMPOSSIBLE,
        ),
        (
            "Task 7: Impossible Task — Non-existent Input File",
            f"Transcribe {missing_wav.name} in Marathi and translate to Gujarati",
            [str(missing_wav)],
            workspace_dir / "task_7_impossible_missing_file",
            30,
            20.0,
            ExecutionStatus.IMPOSSIBLE,
        ),
        (
            "Task 8: Budget Safety — Insufficient INR Budget (₹0.001 Limit)",
            f"Transcribe {hindi_wav.name} in Hindi, translate to Kannada, and synthesize speech in Kannada",
            [str(hindi_wav)],
            workspace_dir / "task_8_budget_exceeded",
            30,
            0.001,  # Insufficient budget triggers clean halt
            ExecutionStatus.BUDGET_EXCEEDED,
        ),
    ]

    print("[2/3] Executing 8 Demo Tasks...\n")
    results = []

    for idx, (title, task_prompt, files, out_dir, max_steps, max_cost, expected_status) in enumerate(tasks, 1):
        print(f"  [{idx}/8] Running {title}...")
        runner = AgentRunner(
            task=task_prompt,
            files=files,
            output_dir=out_dir,
            max_steps=max_steps,
            max_cost_inr=max_cost,
        )
        report = await runner.run()
        results.append((title, report))

        match = "MATCH" if report.status == expected_status else "MISMATCH"
        print(
            f"        Result: {report.status.value} (Expected: {expected_status.value}) | "
            f"Cost: ₹{report.total_cost_inr:.4f} | Steps: {report.steps_taken} | Status: [{match}]"
        )
        if report.reason:
            print(f"        Notice: {report.reason}")
        print()

    # 3. Print Final Summary Table
    print("\n" + "=" * 90)
    print("  DEMO SUITE FINAL EXECUTION SUMMARY TABLE")
    print("=" * 90)
    print(f"{'#':<3} | {'Task Title':<45} | {'Status':<15} | {'Cost (₹)':<10} | {'Steps':<6} | {'Artifacts'}")
    print("-" * 90)

    for i, (title, rep) in enumerate(results, 1):
        short_title = title.split(":")[1].strip() if ":" in title else title
        print(
            f"{i:<3} | {short_title[:45]:<45} | {rep.status.value:<15} | "
            f"₹{rep.total_cost_inr:<9.4f} | {rep.steps_taken:<6} | {len(rep.artifacts)} files"
        )

    print("=" * 90)
    print(f"\nAll dossiers, reports, and traces written under: {workspace_dir}\n")
    return results


def main() -> None:
    asyncio.run(run_all_demos())


if __name__ == "__main__":
    main()
