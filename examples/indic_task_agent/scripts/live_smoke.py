"""Live smoke test: run each tool once against the real API. Costs about Rs 1."""

import json
import sys

from bodhan_mcp.toolkit import Toolkit

sys.stdout.reconfigure(encoding="utf-8")
tk = Toolkit.from_env()


def show(title, result):
    print(f"\n=== {title} ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:900])


ocr = tk.ocr_document("sample_data/page.png")
show("OCR", ocr)
text = ocr.get("text") or ocr["preview"]

tr = tk.translate_text("hi", text=text[:600])
show("TRANSLATE -> Hindi", tr)

sp = tk.speak_text("hi", text="बैठक कल तक के लिए स्थगित कर दी गई है।", out_path="smoke.wav")
show("SPEAK", sp)

show("VERIFY", tk.verify_speech(sp["path"], expected_text="बैठक कल तक के लिए स्थगित कर दी गई है।", language="hi"))
show("TRANSCRIBE (no language given)", tk.transcribe_audio("sample_data/hello_hi.wav"))
show("USAGE", tk.get_usage())