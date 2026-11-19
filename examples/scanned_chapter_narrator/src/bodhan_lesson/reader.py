"""Interactive HTML reader generator with synchronized sentence-level audio highlighting."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from bodhan_lesson.schemas import NarrationItem


class HTMLReaderGenerator:
    """Generates a standalone, responsive HTML5 reader with synchronized audio playback."""

    def generate_reader_html(
        self,
        lesson_title: str,
        language: str,
        lesson_markdown: str,
        narration_items: list[NarrationItem],
        audio_filename: str,
        glossary: dict[str, str] | None = None,
        annotations: list[dict[str, Any]] | None = None,
        output_html_path: Path | None = None,
    ) -> str:
        """Construct the interactive reader HTML with time-stamped sentences and audio player."""
        # 1. Parse markdown text into paragraphs and timestamped sentences
        sentences_with_timing = self._build_timed_sentences(narration_items)

        # 2. Render paragraphs with span elements
        content_html = self._render_content_html(lesson_markdown, sentences_with_timing)

        # 3. Render glossary and annotations sidebars
        glossary_html = self._render_glossary_html(glossary or {})
        annotations_html = self._render_annotations_html(annotations or [])

        # 4. Assemble complete HTML
        lang_label = {"hi": "हिन्दी (Hindi)", "ta": "தமிழ் (Tamil)", "bn": "বাংলা (Bengali)"}.get(language, language.upper())

        html_template = f"""<!DOCTYPE html>
<html lang="{language}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(lesson_title)} - {lang_label} Reader</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #1e293b;
      --primary: #2563eb;
      --primary-light: #dbeafe;
      --highlight: #fef08a;
      --highlight-border: #facc15;
      --border: #e2e8f0;
      --accent: #475569;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.8;
      margin: 0;
      padding-bottom: 120px;
    }}
    header {{
      background: #1e293b;
      color: white;
      padding: 1.5rem 2rem;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}
    .header-content {{
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }}
    .badge {{
      background: #0284c7;
      padding: 0.35rem 0.8rem;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
    }}
    .container {{
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1.5rem;
      display: grid;
      grid-template-columns: 2.2fr 1fr;
      gap: 2rem;
    }}
    @media (max-width: 850px) {{
      .container {{ grid-template-columns: 1fr; }}
    }}
    .main-reader {{
      background: var(--card-bg);
      padding: 2.5rem;
      border-radius: 12px;
      border: 1px solid var(--border);
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    h1, h2, h3 {{ color: #0f172a; margin-top: 1.8rem; }}
    h1 {{ border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; }}
    .sentence {{
      display: inline;
      padding: 0.15rem 0.25rem;
      margin: 0 0.1rem;
      border-radius: 4px;
      cursor: pointer;
      transition: background-color 0.2s ease, box-shadow 0.2s ease;
    }}
    .sentence:hover {{
      background-color: var(--primary-light);
    }}
    .sentence.active {{
      background-color: var(--highlight);
      box-shadow: 0 0 0 2px var(--highlight-border);
      font-weight: 500;
    }}
    .sidebar {{
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }}
    .sidebar-card {{
      background: var(--card-bg);
      padding: 1.5rem;
      border-radius: 12px;
      border: 1px solid var(--border);
    }}
    .sidebar-card h3 {{
      margin-top: 0;
      font-size: 1.1rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .term-item {{
      margin-bottom: 1rem;
      border-left: 3px solid var(--primary);
      padding-left: 0.75rem;
    }}
    .term-name {{ font-weight: 700; color: var(--primary); }}
    .term-def {{ font-size: 0.9rem; color: #475569; }}
    .audio-player-bar {{
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: #0f172a;
      color: white;
      padding: 1rem 2rem;
      display: flex;
      justify-content: center;
      align-items: center;
      box-shadow: 0 -4px 12px rgba(0,0,0,0.15);
      z-index: 100;
    }}
    .audio-player-content {{
      max-width: 900px;
      width: 100%;
      display: flex;
      align-items: center;
      gap: 1.5rem;
      flex-wrap: wrap;
    }}
    audio {{ width: 100%; max-width: 650px; outline: none; }}
  </style>
</head>
<body>
  <header>
    <div class="header-content">
      <div>
        <h2 style="margin:0; font-size: 1.4rem;">Bodhan Interactive Lesson Reader</h2>
        <div style="font-size: 0.95rem; opacity: 0.85; margin-top: 0.25rem;">{html.escape(lesson_title)}</div>
      </div>
      <div>
        <span class="badge">{lang_label}</span>
      </div>
    </div>
  </header>

  <div class="container">
    <main class="main-reader">
      {content_html}
    </main>

    <aside class="sidebar">
      {glossary_html}
      {annotations_html}
    </aside>
  </div>

  <div class="audio-player-bar">
    <div class="audio-player-content">
      <audio id="lessonAudio" controls src="{audio_filename}"></audio>
      <span style="font-size: 0.85rem; opacity: 0.8;">Click any sentence to jump</span>
    </div>
  </div>

  <script>
    const audio = document.getElementById('lessonAudio');
    const sentences = document.querySelectorAll('.sentence');

    // Sentence click-to-play
    sentences.forEach(s => {{
      s.addEventListener('click', () => {{
        const start = parseFloat(s.getAttribute('data-start'));
        if (!isNaN(start)) {{
          audio.currentTime = start;
          audio.play();
        }}
      }});
    }});

    // Synchronized audio highlight
    audio.addEventListener('timeupdate', () => {{
      const curTime = audio.currentTime;
      sentences.forEach(s => {{
        const start = parseFloat(s.getAttribute('data-start'));
        const end = parseFloat(s.getAttribute('data-end'));
        if (curTime >= start && curTime <= end) {{
          s.classList.add('active');
        }} else {{
          s.classList.remove('active');
        }}
      }});
    }});
  </script>
</body>
</html>"""

        if output_html_path:
            output_html_path.parent.mkdir(parents=True, exist_ok=True)
            output_html_path.write_text(html_template, encoding="utf-8")

        return html_template

    def _build_timed_sentences(self, items: list[NarrationItem]) -> list[dict[str, Any]]:
        """Compute estimated start and end timestamps for each sentence utterance."""
        timed = []
        current_offset = 0.0

        for item in items:
            sentences = re.split(r"(?<=[.!?।॥\n])\s+", item.target_script)
            clean_s = [s.strip() for s in sentences if s.strip()]
            if not clean_s:
                continue

            sec_duration = max(1.0, item.duration_seconds)
            time_per_sentence = sec_duration / float(len(clean_s))

            for s in clean_s:
                start_t = current_offset
                end_t = start_t + time_per_sentence
                timed.append({
                    "text": s,
                    "start": round(start_t, 2),
                    "end": round(end_t, 2),
                })
                current_offset = end_t
            current_offset += 0.500  # section transition pause

        return timed

    def _render_content_html(self, markdown_text: str, timed_sentences: list[dict[str, Any]]) -> str:
        """Render markdown text with embedded sentence spans."""
        paragraphs = markdown_text.split("\n\n")
        html_blocks: list[str] = []
        timed_idx = 0

        for p in paragraphs:
            p = p.strip()
            if not p:
                continue

            if p.startswith("#"):
                level = min(6, p.count("#"))
                clean_title = p.lstrip("#").strip()
                html_blocks.append(f"<h{level}>{html.escape(clean_title)}</h{level}>")
            elif p.startswith("|") and p.endswith("|"):
                html_blocks.append(f"<pre style='background:#f1f5f9; padding:1rem; border-radius:8px;'><code>{html.escape(p)}</code></pre>")
            elif p.startswith("$$") and p.endswith("$$"):
                html_blocks.append(f"<div style='text-align:center; padding:1rem; font-style:italic;'>{html.escape(p)}</div>")
            else:
                # Regular paragraph: wrap in clickable spans
                raw_sentences = re.split(r"(?<=[.!?।॥])\s+", p)
                span_elements = []
                for s in raw_sentences:
                    s_clean = s.strip()
                    if not s_clean:
                        continue
                    if timed_idx < len(timed_sentences):
                        t_info = timed_sentences[timed_idx]
                        timed_idx += 1
                        span_elements.append(
                            f'<span class="sentence" data-start="{t_info["start"]}" data-end="{t_info["end"]}">{html.escape(s_clean)}</span>'
                        )
                    else:
                        span_elements.append(f'<span class="sentence">{html.escape(s_clean)}</span>')

                html_blocks.append(f"<p>{' '.join(span_elements)}</p>")

        return "\n".join(html_blocks)

    def _render_glossary_html(self, glossary: dict[str, str]) -> str:
        """Render side card for STEM technical glossary terms."""
        if not glossary:
            return ""

        terms_html = []
        for term, defn in glossary.items():
            terms_html.append(
                f'<div class="term-item"><div class="term-name">{html.escape(term)}</div>'
                f'<div class="term-def">{html.escape(defn)}</div></div>'
            )

        return f"""
        <div class="sidebar-card">
          <h3>📖 Key Glossary Terms</h3>
          {"".join(terms_html)}
        </div>"""

    def _render_annotations_html(self, annotations: list[dict[str, Any]]) -> str:
        """Render side card for margin notes and callout annotations."""
        if not annotations:
            return ""

        notes_html = []
        for ann in annotations:
            txt = ann.get("text", "")
            pg = ann.get("page_number", 1)
            notes_html.append(
                f'<div style="margin-bottom:0.8rem; font-size:0.9rem; background:#fffbeb; padding:0.6rem; border-left:3px solid #f59e0b; border-radius:4px;">'
                f'<div style="font-size:0.75rem; color:#92400e; font-weight:600;">PAGE {pg} NOTE</div>'
                f'<div>{html.escape(txt)}</div></div>'
            )

        return f"""
        <div class="sidebar-card">
          <h3>📌 Margin Notes & Callouts</h3>
          {"".join(notes_html)}
        </div>"""


html_reader_generator = HTMLReaderGenerator()
