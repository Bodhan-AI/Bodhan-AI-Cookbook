"""Test doubles for offline unit and integration testing.
This file lives strictly under tests/ and is used solely for offline pytest execution
so that plain pytest makes zero external network or paid API calls.
"""

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class FunctionDouble:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class ToolCallDouble:
    def __init__(self, name: str, args: dict):
        self.id = f"call_double_{random.randint(100000, 999999)}"
        self.type = "function"
        self.function = FunctionDouble(name, json.dumps(args))


class MessageDouble:
    def __init__(self, content: Optional[str] = None, tool_calls: Optional[List[Any]] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class LLMTestDouble:
    def __init__(self, *args, **kwargs):
        self.base_url = "http://localhost:0"
        self.model = "test-double-model"
        self.api_key = "test-double-key"

    def chat_completion(self, messages, temperature=0.1, max_retries=1):
        return "Offline test double response."

    def chat_completion_with_tools(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, tool_choice: str = "auto", temperature: float = 0.1, max_retries: int = 1) -> Tuple[Any, Dict[str, int]]:
        """Deterministic offline tool-calling for tests without network calls."""
        usage = {"prompt_tokens": 60, "completion_tokens": 25, "total_tokens": 85}

        # Find user prompt from messages
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = str(m.get("content", ""))
                break

        task_lower = user_prompt.lower()
        last_msg = messages[-1]

        # 1. Check impossible tasks
        if "french" in task_lower or "german" in task_lower or "spanish" in task_lower or "chinese" in task_lower:
            tc = ToolCallDouble(
                "mark_task_impossible",
                {"reason": "The task requested translation to an unsupported non-Indic language. Bodhan Indic models only support the 22 scheduled Indian languages and English."}
            )
            return MessageDouble(tool_calls=[tc]), usage

        # 2. Document & Image OCR flow (PDF, PNG, JPG)
        has_doc = any(ext in user_prompt.lower() for ext in [".pdf", ".png", ".jpg", ".jpeg"]) or "scanned" in task_lower or "ocr" in task_lower
        if has_doc:
            m_doc = re.search(r"path:\s*'([^']+\.(?:pdf|png|jpg|jpeg))'", user_prompt, re.IGNORECASE)
            doc_path = m_doc.group(1) if m_doc else "doc.pdf"

            if last_msg.get("role") == "user":
                tc = ToolCallDouble("bodhan_ocr", {"file_path": doc_path})
                return MessageDouble(tool_calls=[tc]), usage

            tool_content = str(last_msg.get("content", ""))
            
            # Check if this is an audio speech task or structured table extraction
            wants_speech = any(w in task_lower for w in ["aloud", "recite", "speak", "speech", "audio", "listen", "bengali voice"])
            target_lang = "bn" if ("bengali" in task_lower or "bn" in task_lower) else "ta"

            if ("page_count" in tool_content or "ocr_extracted" in tool_content) and not wants_speech:
                # OCR extraction only -> format table / extract structured markdown
                extracted_table = (
                    "# Extracted Data Table\n\n"
                    "| Sub-Center ID | Taluka Name | Medical Staff | Hospital Beds | Ambulances | Monthly Inflow |\n"
                    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
                    "| PHC-001 | Shirur | 14 | 45 | 4 | 3,820 |\n"
                    "| PHC-002 | Haveli | 18 | 60 | 6 | 5,110 |\n"
                    "| PHC-003 | Baramati | 22 | 80 | 8 | 6,450 |\n"
                    "| PHC-004 | Junnar | 11 | 30 | 3 | 2,940 |\n"
                    "| PHC-005 | Khed | 16 | 55 | 5 | 4,300 |\n"
                )
                tc = ToolCallDouble("write_file_artifact", {
                    "relative_filename": "table_extracted.md",
                    "content": extracted_table,
                })
                return MessageDouble(tool_calls=[tc]), usage
            elif "table_extracted.md" in tool_content or "Saved artifact table_extracted.md" in tool_content:
                tc = ToolCallDouble("complete_task", {
                    "final_answer": "Extracted structured table from degraded document.\nArtifact: [artifacts/table_extracted.md](artifacts/table_extracted.md)",
                    "output_artifacts": ["artifacts/table_extracted.md", "artifacts/ocr_extracted.md"],
                })
                return MessageDouble(tool_calls=[tc]), usage
            elif "page_count" in tool_content or "ocr_extracted" in tool_content:
                # OCR done -> Translate for speech
                tc = ToolCallDouble("bodhan_translate", {
                    "text": "District Health and Rural Infrastructure Report 2026. All facility managers must maintain inventory.",
                    "target_language_code": target_lang,
                })
                return MessageDouble(tool_calls=[tc]), usage
            elif "translated_text" in tool_content and target_lang in tool_content:
                # Translate done -> Speak
                tts_text = "জেলা স্বাস্থ্য ও গ্রামীণ পরিকাঠামো প্রতিবেদন ২০২৬।" if target_lang == "bn" else "மாவட்ட சுகாதார கண்ணோட்டம் சுருக்கம்."
                voice = "Amit"
                tc = ToolCallDouble("bodhan_speak", {
                    "text": tts_text,
                    "language": target_lang,
                    "voice": voice,
                    "output_filename": f"summary_{target_lang}.mp3",
                })
                return MessageDouble(tool_calls=[tc]), usage
            elif f"summary_{target_lang}.mp3" in tool_content and "duration_seconds" in tool_content:
                # Speak done -> Back-transcribe
                tc = ToolCallDouble("bodhan_transcribe", {
                    "audio_path": f"summary_{target_lang}.mp3",
                    "language": target_lang,
                    "output_format": "text",
                    "purpose": "backtranscribe",
                })
                return MessageDouble(tool_calls=[tc]), usage
            else:
                tc = ToolCallDouble("complete_task", {
                    "final_answer": f"Scanned document processed to {target_lang.upper()} audio summary.\nArtifact: [artifacts/summary_{target_lang}.mp3](artifacts/summary_{target_lang}.mp3)",
                    "output_artifacts": [f"artifacts/summary_{target_lang}.mp3", f"artifacts/summary_{target_lang}.md", "artifacts/ocr_extracted.md"],
                })
                return MessageDouble(tool_calls=[tc]), usage

        # 3. Markdown Table flow (Translate -> Write Artifact -> Complete)
        if ".md" in task_lower or "table" in task_lower or "census" in task_lower or "marks" in task_lower or "inventory" in task_lower:
            m_md = re.search(r"path:\s*'([^']+\.md)'", user_prompt, re.IGNORECASE)
            md_path = m_md.group(1) if m_md else "table.md"

            if last_msg.get("role") == "user":
                content = ""
                try:
                    with open(md_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    content = "# Table\n| Item | Val |\n|---|---|\n| A | 1 |\n"
                tc = ToolCallDouble("bodhan_translate", {
                    "text": content,
                    "target_language_code": "mr",
                    "preserve_formatting": True,
                })
                return MessageDouble(tool_calls=[tc]), usage

            tool_content = str(last_msg.get("content", ""))
            if "translated_text" in tool_content:
                try:
                    clean_c = tool_content.replace("<data_untrusted>\n", "").replace("\n</data_untrusted>", "")
                    data = json.loads(clean_c)
                    t_text = data.get("translated_text", "")
                except Exception:
                    t_text = tool_content
                tc = ToolCallDouble("write_file_artifact", {
                    "relative_filename": "table_mr.md",
                    "content": t_text,
                })
                return MessageDouble(tool_calls=[tc]), usage
            else:
                tc = ToolCallDouble("complete_task", {
                    "final_answer": "Table translated successfully.\nArtifact: [artifacts/table_mr.md](artifacts/table_mr.md)",
                    "output_artifacts": ["artifacts/table_mr.md"],
                })
                return MessageDouble(tool_calls=[tc]), usage

        # 4. Audio language detection / query flow
        audio_paths = re.findall(r"path:\s*'([^']+\.(?:mp3|wav|m4a|ogg|flac))'", user_prompt, re.IGNORECASE)
        is_lang_detect = (
            ("language" in task_lower or "detect" in task_lower or "identify" in task_lower or "date" in task_lower or "timeline" in task_lower or "kannada" in task_lower)
            and not any(w in task_lower for w in ["subtitle", "srt", "caption"])
            and bool(audio_paths)
        )
        if is_lang_detect:
            # Check which audio files have already been transcribed in previous assistant tool calls
            transcribed_paths = []
            for m in messages:
                if m.get("role") == "assistant":
                    for tc in m.get("tool_calls", []):
                        fn = tc.get("function", {})
                        if fn.get("name") == "bodhan_transcribe":
                            args_raw = fn.get("arguments", "{}")
                            try:
                                d = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                                ap = d.get("audio_path")
                                if ap:
                                    transcribed_paths.append(ap)
                            except Exception:
                                pass

            last_tool_content = str(last_msg.get("content", "")) if last_msg.get("role") == "tool" else ""

            # Check what was the last assistant tool call
            last_tc_name = ""
            for m in reversed(messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    last_tc_name = m["tool_calls"][-1]["function"]["name"]
                    break

            if last_tc_name == "bodhan_transcribe" and not ("date" in task_lower or "timeline" in task_lower or "kannada" in task_lower):
                try:
                    clean_c = last_tool_content.replace("<data_untrusted>\n", "").replace("\n</data_untrusted>", "")
                    data = json.loads(clean_c)
                    txt = data.get("text", "")
                except Exception:
                    txt = "नवा मजकूर"
                audio_file_name = Path(transcribed_paths[-1]).name if transcribed_paths else "clip.mp3"
                tc = ToolCallDouble("bodhan_detect_language", {
                    "text": txt,
                    "audio_file": audio_file_name,
                })
                return MessageDouble(tool_calls=[tc]), usage

            remaining = [p for p in audio_paths if p not in transcribed_paths]
            if remaining:
                tc = ToolCallDouble("bodhan_transcribe", {"audio_path": remaining[0], "output_format": "text"})
                return MessageDouble(tool_calls=[tc]), usage
            else:
                if "date" in task_lower or "timeline" in task_lower or "calendar" in task_lower:
                    ans = "Audited regional clips: clip_hi.mp3 explicitly references calendar year '2026' and national mission timeline."
                    tc = ToolCallDouble("complete_task", {"final_answer": ans, "output_artifacts": []})
                    return MessageDouble(tool_calls=[tc]), usage
                elif "kannada" in task_lower:
                    ans = "Comparison completed: clip_kn.mp3 is identified as Kannada (Kannada script, ISO kn)."
                    tc = ToolCallDouble("complete_task", {"final_answer": ans, "output_artifacts": []})
                    return MessageDouble(tool_calls=[tc]), usage
                else:
                    tc = ToolCallDouble("complete_task", {
                        "final_answer": f"Detected audio languages for {len(audio_paths)} file(s).\nArtifact: [artifacts/lang_detect_results.json](artifacts/lang_detect_results.json)",
                        "output_artifacts": ["artifacts/lang_detect_results.json"],
                    })
                    return MessageDouble(tool_calls=[tc]), usage

        # 5. Audio Subtitles flow (Inspect -> Transcribe -> Detect -> Format SRT -> Complete)
        m_audio = re.search(r"path:\s*'([^']+\.(?:mp3|wav|m4a|ogg|flac))'", user_prompt, re.IGNORECASE)
        audio_path = m_audio.group(1) if m_audio else "audio.mp3"

        if last_msg.get("role") == "user":
            tc = ToolCallDouble("bodhan_inspect_media", {"file_path": audio_path})
            return MessageDouble(tool_calls=[tc]), usage

        tool_content = str(last_msg.get("content", ""))
        if "duration" in tool_content and "channels" in tool_content:
            # Inspect done -> Transcribe
            tc = ToolCallDouble("bodhan_transcribe", {"audio_path": audio_path, "output_format": "segments"})
            return MessageDouble(tool_calls=[tc]), usage
        elif "segments" in tool_content and "cost_inr" in tool_content:
            # Transcribe done -> Detect language
            try:
                clean_c = tool_content.replace("<data_untrusted>\n", "").replace("\n</data_untrusted>", "")
                data = json.loads(clean_c)
                txt = data.get("text", "नमस्ते")
            except Exception:
                txt = "नमस्ते"
            tc = ToolCallDouble("bodhan_detect_language", {"text": txt})
            return MessageDouble(tool_calls=[tc]), usage
        elif "confidence" in tool_content and "script" in tool_content:
            # Detect language done -> Format Subtitles SRT
            target_lang = "ta" if ("tamil" in task_lower or re.search(r"\bta\b", task_lower)) else ("mr" if ("marathi" in task_lower or "source language" in task_lower or "hindi_audio_to_hindi_srt" in task_lower or re.search(r"\bmr\b", task_lower)) else "en")
            tc = ToolCallDouble("format_subtitles_srt", {
                "audio_path": audio_path,
                "target_language": target_lang,
            })
            return MessageDouble(tool_calls=[tc]), usage
        else:
            target_lang = "ta" if ("tamil" in task_lower or re.search(r"\bta\b", task_lower)) else ("mr" if ("marathi" in task_lower or "source language" in task_lower or "hindi_audio_to_hindi_srt" in task_lower or re.search(r"\bmr\b", task_lower)) else "en")
            clean_art = f"artifacts/subtitles_{target_lang}.srt"
            tc = ToolCallDouble("complete_task", {
                "final_answer": f"Subtitles generated successfully.\nArtifact: [{clean_art}]({clean_art})",
                "output_artifacts": [clean_art],
            })
            return MessageDouble(tool_calls=[tc]), usage


