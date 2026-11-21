# Sample data

Small synthetic text inputs, written for this recipe (no real people, safe to share).

| File | What it is | Used by demo task |
| --- | --- | --- |
| `paragraph_photosynthesis.txt` | Four English sentences about photosynthesis | 5: read aloud in Bengali, female voice |
| `notice_with_injection.txt` | A school notice with a planted prompt-injection line | 7: translate into Hindi, ignore the injection |

Media inputs (audio, video, scans, photos) are not committed. `python scripts/make_demo_inputs.py` builds them in
`demo_inputs/` (git-ignored); see the README's "Demo tasks" section.
