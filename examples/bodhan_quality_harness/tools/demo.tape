# vhs tools/demo.tape  ->  demo.mp4
# Declarative terminal recording for the CLI segment of the demo video.
Output demo.mp4

Set FontSize 20
Set Width 1600
Set Height 900
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set Shell bash

Type "# Bodhan quality harness — clean-clone run"
Enter
Sleep 1s

Type "pip install -r requirements.txt -q"
Enter
Sleep 6s

Type "pytest -q"
Enter
Sleep 8s

Type "python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report --offline"
Enter
Sleep 12s

Type "head -24 report/summary.md"
Enter
Sleep 6s

Type "# rerun: cached, so it costs nothing and reproduces the same digest"
Enter
Sleep 1s
Type "python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report2 --offline | tail -2"
Enter
Sleep 10s
