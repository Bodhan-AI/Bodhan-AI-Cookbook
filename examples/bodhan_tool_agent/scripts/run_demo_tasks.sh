#!/usr/bin/env bash
# Runs the demo tasks. Each task gets its own folder under ./runs/. Needs keys in .env and demo_inputs/.
set -u
mkdir -p runs
run() { name="$1"; shift; echo "=== $name"; python -m bodhan_agent --out "runs/$name" "$@"; echo; }

run 01_tamil_summary    --task "Make a two-minute Tamil audio summary of this chapter" --files demo_inputs/chapter.pdf
run 02_hindi_subtitles  --task "Transcribe this Hindi voice note and give me English subtitles" --files demo_inputs/voice_note_hindi.wav
run 03_marathi_table    --task "Translate this worksheet into Marathi and keep the table" --files demo_inputs/worksheet.png
run 04_telugu_clips     --task "Which of these clips are in Telugu? Translate those into English" \
                        --files demo_inputs/clip1_telugu.wav demo_inputs/clip2_tamil.wav demo_inputs/clip3_telugu.wav demo_inputs/clip4_hindi.wav
run 05_bengali_female   --task "Read this paragraph aloud in Bengali with a female voice" --files demo_inputs/paragraph.txt
run 06_french_impossible --task "Translate this video into French" --files demo_inputs/video.mp4
run 07_budget_too_small --task "Make a two-minute Tamil audio summary of this chapter" --files demo_inputs/chapter.pdf --max-cost-inr 0.3
