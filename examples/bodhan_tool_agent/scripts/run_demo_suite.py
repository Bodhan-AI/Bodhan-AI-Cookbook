"""
Script to execute all 6 required demo tasks and generate reproducible artifacts and traces.
"""

import os
import shutil
from bodhan_agent.agent import TaskAgent

DEMO_TASKS = [
    {
        "id": "task_1_tamil_summary",
        "task": "Make a two-minute Tamil audio summary of this chapter",
        "files": "sample_inputs/chapter.pdf",
    },
    {
        "id": "task_2_hindi_subtitles",
        "task": "Transcribe this Hindi voice note and give me English subtitles",
        "files": "sample_inputs/voice_note.wav",
    },
    {
        "id": "task_3_marathi_worksheet",
        "task": "Translate this worksheet into Marathi and keep the table",
        "files": "sample_inputs/worksheet.png",
    },
    {
        "id": "task_4_telugu_clips",
        "task": "Which of these clips are in Telugu? Translate those into English",
        "files": "sample_inputs/audio_clips",
    },
    {
        "id": "task_5_bengali_speech",
        "task": "Read this paragraph aloud in Bengali with a female voice",
        "files": "sample_inputs/paragraph.txt",
    },
    {
        "id": "task_6_french_video_impossible",
        "task": "Translate this video into French",
        "files": "sample_inputs/video.mp4",
    },
]


def setup_sample_inputs():
    os.makedirs("sample_inputs/audio_clips", exist_ok=True)
    with open("sample_inputs/chapter.pdf", "w") as f:
        f.write("%PDF-1.4 Class 9 Math Chapter 6 Lines and Angles")
    with open("sample_inputs/voice_note.wav", "w") as f:
        f.write("RIFF dummy audio data")
    with open("sample_inputs/worksheet.png", "w") as f:
        f.write("PNG dummy image data")
    with open("sample_inputs/audio_clips/clip1.wav", "w") as f:
        f.write("RIFF clip 1")
    with open("sample_inputs/audio_clips/clip2.wav", "w") as f:
        f.write("RIFF clip 2")
    with open("sample_inputs/paragraph.txt", "w", encoding="utf-8") as f:
        f.write("This is a sample paragraph to be narrated in Bengali.")
    with open("sample_inputs/video.mp4", "w") as f:
        f.write("MP4 dummy video")


def run_all():
    setup_sample_inputs()
    base_out = "examples/demo_outputs"
    os.makedirs(base_out, exist_ok=True)

    print("Running 6 Demo Tasks...")
    for item in DEMO_TASKS:
        out_dir = os.path.join(base_out, item["id"])
        print(f"\n--- Executing {item['id']}: '{item['task']}' ---")
        agent = TaskAgent(
            task=item["task"],
            files_path=item["files"],
            out_dir=out_dir,
            max_steps=20,
            max_cost_inr=15.0,
        )
        report = agent.run()
        print(f"Status: {report.status} | Steps: {report.total_steps} | Cost: ₹{report.total_cost_inr:.4f} | Time: {report.total_duration_seconds:.2f}s")


if __name__ == "__main__":
    run_all()
