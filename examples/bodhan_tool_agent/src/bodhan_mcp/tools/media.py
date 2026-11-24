"""inspect_media: free, local facts about an input file so a planner can choose the right tool."""

from __future__ import annotations

from PIL import Image

from bodhan_core import audio, langs, workspace
from bodhan_core.ledger import cost_ocr, cost_transcribe


async def inspect_media(path: str) -> dict:
    p = workspace.resolve_input(path)
    kind = workspace.media_kind(p)
    out: dict = {"ok": True, "path": str(p), "name": p.name, "kind": kind, "bytes": p.stat().st_size}

    if kind in {"audio", "video"}:
        info = audio.probe(p)
        out.update(info)
        if info["has_video"]:
            out["kind"] = "video"
        if not info["has_audio"]:
            out["warnings"] = ["No audio stream: nothing to transcribe."]
        out["suggested_tool"] = "transcribe_audio" if info["has_audio"] else None
        out["estimated_cost_inr"] = {"transcribe_audio": round(cost_transcribe(info["duration_s"]), 4)}
    elif kind == "image":
        with Image.open(p) as img:
            out.update({"width": img.width, "height": img.height, "format": img.format, "pages": 1})
        out["suggested_tool"] = "ocr_document"
        out["estimated_cost_inr"] = {"ocr_document": cost_ocr(1)}
    elif kind == "pdf":
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(p)) as doc:
            pages = len(doc)
        out.update({"pages": pages, "suggested_tool": "ocr_document",
                    "estimated_cost_inr": {"ocr_document": round(cost_ocr(pages), 2)},
                    "note": "All PDF text is read with ocr_document (Indic-OCR), including digital PDFs."})
    elif kind == "text":
        text = workspace.read_text_file(p)
        script = langs.detect_script(text)
        out.update({
            "chars": len(text), "lines": text.count("\n") + 1, "script": script,
            "preview": workspace.preview(text), "untrusted_content": True,
            "suggested_tool": "read_text / translate_text / synthesize_speech",
        })
    else:
        out["warnings"] = [f"Unrecognised file type {p.suffix or '(no extension)'}; no tool handles it."]
        out["suggested_tool"] = None
    return out
