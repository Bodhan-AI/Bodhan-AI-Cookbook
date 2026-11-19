"""Interactive HTML reader generator with section-level synchronization and audio playback."""

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
        """Construct the interactive reader HTML with section-synchronized audio player."""
        # 1. Compute cumulative track offsets for sections
        current_offset = 0.0
        for item in narration_items:
            item.full_track_offset_seconds = round(current_offset, 2)
            current_offset += max(1.0, item.duration_seconds) + 0.500  # 500ms section pause

        # 2. Render synchronized sections
        content_html = self._render_sections_html(lesson_markdown, narration_items)

        # 3. Render glossary and annotations sidebars
        glossary_html = self._render_glossary_html(glossary or {})
        annotations_html = self._render_annotations_html(annotations or [])

        # 4. Assemble complete HTML
        lang_label = {
            "hi": "हिन्दी (Hindi)",
            "ta": "தமிழ் (Tamil)",
            "bn": "বাংলা (Bengali)",
            "te": "తెలుగు (Telugu)",
            "mr": "मराठी (Marathi)",
            "gu": "ગુજરાતી (Gujarati)",
            "kn": "ಕನ್ನಡ (Kannada)",
            "ml": "മലയാളം (Malayalam)",
            "pa": "ਪੰਜਾਬੀ (Punjabi)",
            "or": "ଓଡ଼ିଆ (Odia)",
            "as": "অসমীয়া (Assamese)",
            "ur": "اردو (Urdu)",
            "sa": "संस्कृतम् (Sanskrit)",
            "en": "English",
        }.get(language, language.upper())

        html_template = f"""<!DOCTYPE html>
<html lang="{language}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(lesson_title)} - {lang_label} Reader</title>
  
  <!-- KaTeX for STEM mathematical notation -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
  <script>
    window.addEventListener("DOMContentLoaded", function() {{
      if (typeof renderMathInElement === "function") {{
        renderMathInElement(document.body, {{
          delimiters: [
            {{left: "$$", right: "$$", display: true}},
            {{left: "$", right: "$", display: false}}
          ],
          throwOnError: false
        }});
      }}
    }});
  </script>

  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #1e293b;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-light: #eff6ff;
      --highlight: #fef08a;
      --highlight-border: #facc15;
      --border: #e2e8f0;
      --border-strong: #cbd5e1;
      --accent: #475569;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Mukta",
        "Nirmala UI", "Lohit Tamil", "Bangla Sangam MN", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.8;
      margin: 0;
      padding-bottom: 140px;
    }}
    header {{
      background: #0f172a;
      color: white;
      padding: 1.25rem 2rem;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15);
      position: sticky;
      top: 0;
      z-index: 50;
    }}
    .header-content {{
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }}
    .header-title-box h1 {{
      margin: 0;
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }}
    .header-subtitle {{
      font-size: 0.9rem;
      opacity: 0.85;
      margin-top: 0.2rem;
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }}
    .badge {{
      background: #0284c7;
      color: white;
      padding: 0.35rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.82rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }}
    .global-view-toggle {{
      display: inline-flex;
      background: #1e293b;
      padding: 0.25rem;
      border-radius: 8px;
      border: 1px solid #334155;
    }}
    .global-view-btn {{
      background: transparent;
      border: none;
      color: #94a3b8;
      padding: 0.35rem 0.75rem;
      font-size: 0.8rem;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .global-view-btn.active {{
      background: #2563eb;
      color: white;
      box-shadow: 0 1px 3px rgba(0,0,0,0.2);
    }}
    .container {{
      max-width: 1200px;
      margin: 2rem auto;
      padding: 0 1.5rem;
      display: grid;
      grid-template-columns: 2.3fr 1fr;
      gap: 2rem;
    }}
    @media (max-width: 900px) {{
      .container {{ grid-template-columns: 1fr; }}
    }}
    .main-reader {{
      display: flex;
      flex-direction: column;
      gap: 2rem;
    }}
    .lesson-section {{
      background: var(--card-bg);
      padding: 2rem;
      border-radius: 12px;
      border: 2px solid var(--border);
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
      transition: all 0.25s ease-in-out;
      position: relative;
    }}
    .lesson-section.active {{
      border-color: #2563eb;
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15), 0 6px 20px rgba(0, 0, 0, 0.08);
      background: #ffffff;
    }}
    .section-header-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
      padding-bottom: 0.75rem;
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
      gap: 0.75rem;
    }}
    .section-header-left {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }}
    .section-badge {{
      background: #e2e8f0;
      color: #334155;
      font-size: 0.8rem;
      font-weight: 700;
      padding: 0.25rem 0.65rem;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .lesson-section.active .section-badge {{
      background: #2563eb;
      color: #ffffff;
    }}
    .section-play-btn {{
      background: #eff6ff;
      color: #2563eb;
      border: 1px solid #bfdbfe;
      padding: 0.35rem 0.85rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.2s ease;
    }}
    .section-play-btn:hover {{
      background: #2563eb;
      color: #ffffff;
    }}
    .section-tabs {{
      display: inline-flex;
      background: #f1f5f9;
      padding: 0.2rem;
      border-radius: 6px;
      border: 1px solid var(--border);
    }}
    .section-tab-btn {{
      background: transparent;
      border: none;
      color: #64748b;
      padding: 0.3rem 0.65rem;
      font-size: 0.78rem;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .section-tab-btn.active {{
      background: white;
      color: #1e293b;
      box-shadow: 0 1px 2px rgba(0,0,0,0.06);
    }}
    .sentence {{
      display: inline;
      padding: 0.15rem 0.3rem;
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
    .spoken-sentence {{
      font-size: 1.05rem;
      line-height: 2.0;
    }}
    .karaoke-container {{
      background: #fafafa;
      padding: 1.25rem 1.5rem;
      border-radius: 8px;
      border: 1px solid #f1f5f9;
      border-left: 4px solid var(--primary);
      margin-bottom: 1rem;
    }}
    .textbook-container {{
      padding: 0.5rem 0;
    }}
    .textbook-container h1, .textbook-container h2, .textbook-container h3 {{
      color: #0f172a;
      margin-top: 1.25rem;
      margin-bottom: 0.5rem;
    }}
    .table-container {{
      overflow-x: auto;
      margin: 1.25rem 0;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: white;
    }}
    .lesson-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      text-align: left;
    }}
    .lesson-table th {{
      background: #f1f5f9;
      color: #0f172a;
      font-weight: 700;
      padding: 0.75rem 1rem;
      border-bottom: 2px solid var(--border);
    }}
    .lesson-table td {{
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
      color: #334155;
    }}
    .lesson-table tr:nth-child(even) td {{
      background: #f8fafc;
    }}
    .lesson-table tr:hover td {{
      background: #eff6ff;
    }}
    .math-block {{
      text-align: center;
      padding: 1rem 1.25rem;
      margin: 1.25rem 0;
      background: #f8fafc;
      border-left: 4px solid #3b82f6;
      border-radius: 8px;
      overflow-x: auto;
      font-size: 1.15rem;
      font-family: 'Cambria Math', 'STIX Two Math', 'KaTeX_Math', Georgia, serif;
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
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .sidebar-card h3 {{
      margin-top: 0;
      font-size: 1.05rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      color: #0f172a;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.6rem;
      margin-bottom: 1rem;
    }}
    .term-item {{
      margin-bottom: 1rem;
      border-left: 3px solid var(--primary);
      padding-left: 0.75rem;
    }}
    .term-name {{
      font-weight: 700;
      color: var(--primary);
      font-size: 0.95rem;
    }}
    .term-def {{
      font-size: 0.88rem;
      color: #475569;
      margin-top: 0.2rem;
    }}
    .audio-player-bar {{
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: #0f172a;
      color: white;
      padding: 0.85rem 1.5rem;
      display: flex;
      justify-content: center;
      align-items: center;
      box-shadow: 0 -4px 16px rgba(0,0,0,0.25);
      z-index: 100;
    }}
    .audio-player-content {{
      max-width: 1200px;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.25rem;
      flex-wrap: wrap;
    }}
    .player-left {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-grow: 1;
      max-width: 600px;
    }}
    audio {{
      width: 100%;
      height: 38px;
      outline: none;
    }}
    .player-controls {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }}
    .nav-btn {{
      background: #1e293b;
      color: #e2e8f0;
      border: 1px solid #334155;
      padding: 0.4rem 0.75rem;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .nav-btn:hover {{
      background: #334155;
      color: white;
    }}
    .speed-controls {{
      display: inline-flex;
      background: #1e293b;
      padding: 0.2rem;
      border-radius: 6px;
      border: 1px solid #334155;
      align-items: center;
    }}
    .speed-btn {{
      background: transparent;
      border: none;
      color: #94a3b8;
      padding: 0.25rem 0.55rem;
      font-size: 0.78rem;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .speed-btn.active {{
      background: #2563eb;
      color: white;
    }}
    .player-status {{
      font-size: 0.85rem;
      color: #cbd5e1;
      min-width: 160px;
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-content">
      <div class="header-title-box">
        <h1>Bodhan Interactive Lesson Reader</h1>
        <div class="header-subtitle">{html.escape(lesson_title)}</div>
      </div>
      <div class="header-actions">
        <div class="global-view-toggle">
          <button class="global-view-btn active" id="btnGlobalSpoken" onclick="switchGlobalView('spoken')">🎙️ Spoken Read-Along</button>
          <button class="global-view-btn" id="btnGlobalTextbook" onclick="switchGlobalView('textbook')">📖 Textbook View</button>
        </div>
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
      <div class="player-left">
        <audio id="lessonAudio" controls src="{audio_filename}"></audio>
      </div>
      <div class="player-controls">
        <button class="nav-btn" onclick="jumpSection(-1)" title="Previous Section">⏮ Prev</button>
        <button class="nav-btn" onclick="jumpSection(1)" title="Next Section">Next ⏭</button>
        <div class="speed-controls">
          <button class="speed-btn" onclick="setSpeed(0.75)">0.75x</button>
          <button class="speed-btn active" id="spd1" onclick="setSpeed(1.0)">1.0x</button>
          <button class="speed-btn" onclick="setSpeed(1.25)">1.25x</button>
        </div>
        <span id="playerStatus" class="player-status">Click any sentence to play</span>
      </div>
    </div>
  </div>

  <script>
    const audio = document.getElementById('lessonAudio');
    const sections = Array.from(document.querySelectorAll('.lesson-section'));
    const sentences = Array.from(document.querySelectorAll('.sentence'));
    const playerStatus = document.getElementById('playerStatus');

    function seekAndPlay(timeSec) {{
      if (!isNaN(timeSec)) {{
        audio.currentTime = timeSec;
        audio.play();
      }}
    }}

    function setSpeed(rate) {{
      audio.playbackRate = rate;
      document.querySelectorAll('.speed-btn').forEach(b => {{
        if (parseFloat(b.textContent) === rate) {{
          b.classList.add('active');
        }} else {{
          b.classList.remove('active');
        }}
      }});
    }}

    function jumpSection(direction) {{
      const curTime = audio.currentTime;
      let targetIdx = 0;
      for (let i = 0; i < sections.length; i++) {{
        const sStart = parseFloat(sections[i].getAttribute('data-start'));
        const sEnd = parseFloat(sections[i].getAttribute('data-end'));
        if (curTime >= sStart && curTime < sEnd) {{
          targetIdx = i + direction;
          break;
        }}
      }}
      if (targetIdx >= 0 && targetIdx < sections.length) {{
        const targetStart = parseFloat(sections[targetIdx].getAttribute('data-start'));
        seekAndPlay(targetStart);
      }}
    }}

    function switchSectionTab(secId, tabMode) {{
      const sec = document.getElementById('section-' + secId);
      if (!sec) return;
      const spokenPanel = sec.querySelector('.panel-spoken');
      const textbookPanel = sec.querySelector('.panel-textbook');
      const tabBtns = sec.querySelectorAll('.section-tab-btn');

      if (tabMode === 'spoken') {{
        if (spokenPanel) spokenPanel.style.display = 'block';
        if (textbookPanel) textbookPanel.style.display = 'none';
        tabBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === 'spoken'));
      }} else {{
        if (spokenPanel) spokenPanel.style.display = 'none';
        if (textbookPanel) textbookPanel.style.display = 'block';
        tabBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === 'textbook'));
      }}
    }}

    function switchGlobalView(mode) {{
      document.getElementById('btnGlobalSpoken').classList.toggle('active', mode === 'spoken');
      document.getElementById('btnGlobalTextbook').classList.toggle('active', mode === 'textbook');

      sections.forEach(sec => {{
        const secId = sec.id.replace('section-', '');
        switchSectionTab(secId, mode);
      }});
    }}

    // Sentence click-to-play
    sentences.forEach(s => {{
      s.addEventListener('click', () => {{
        const start = parseFloat(s.getAttribute('data-start'));
        seekAndPlay(start);
      }});
    }});

    // Real-time synchronization
    let lastActiveSectionId = null;

    audio.addEventListener('timeupdate', () => {{
      const curTime = audio.currentTime;

      // 1. Section-level highlighting & smooth scroll
      let currentSec = null;
      sections.forEach(sec => {{
        const secStart = parseFloat(sec.getAttribute('data-start'));
        const secEnd = parseFloat(sec.getAttribute('data-end'));
        if (curTime >= secStart && curTime < secEnd) {{
          currentSec = sec;
          if (!sec.classList.contains('active')) {{
            sec.classList.add('active');
            if (sec.id !== lastActiveSectionId) {{
              lastActiveSectionId = sec.id;
              sec.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
          }}
        }} else {{
          sec.classList.remove('active');
        }}
      }});

      if (currentSec) {{
        const badge = currentSec.querySelector('.section-badge');
        const titleText = badge ? badge.textContent : currentSec.id;
        playerStatus.textContent = titleText + ' (' + curTime.toFixed(1) + 's)';
      }}

      // 2. Sentence-level karaoke highlighting
      sentences.forEach(s => {{
        const start = parseFloat(s.getAttribute('data-start'));
        const end = parseFloat(s.getAttribute('data-end'));
        if (!isNaN(start) && !isNaN(end) && curTime >= start && curTime < end) {{
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

    def _split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentences preserving Indic punctuation marks."""
        if not text:
            return []
        raw_parts = re.split(r"(?<=[.!?।॥\n])\s+", text.strip())
        return [s.strip() for s in raw_parts if s.strip()]

    def _compute_sentence_spans(
        self,
        sentences: list[str],
        sec_start: float,
        sec_duration: float,
    ) -> list[tuple[str, float, float]]:
        """Calculate character-weighted duration timestamps for each spoken sentence."""
        if not sentences:
            return []
        lengths = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
        total_len = sum(lengths)
        duration = max(1.0, sec_duration)

        spans: list[tuple[str, float, float]] = []
        curr_t = sec_start
        for i, s in enumerate(sentences):
            dur = (lengths[i] / total_len) * duration
            end_t = curr_t + dur
            if i == len(sentences) - 1:
                end_t = sec_start + duration
            spans.append((s, curr_t, end_t))
            curr_t = end_t
        return spans

    def _render_markdown_table(self, table_text: str) -> str:
        """Render a GitHub-flavored Markdown table to a responsive, styled HTML table."""
        lines = [ln.strip() for ln in table_text.strip().splitlines() if ln.strip()]
        if not lines:
            return ""

        rows: list[list[str]] = []
        has_separator = False
        for i, line in enumerate(lines):
            inner = line.strip("|")
            cells = [c.strip() for c in inner.split("|")]
            if i == 1 and all(re.match(r"^:?-+:?$", c) for c in cells):
                has_separator = True
                continue
            rows.append(cells)

        if not rows:
            return f"<pre><code>{html.escape(table_text)}</code></pre>"

        out = ['<div class="table-container"><table class="lesson-table">']
        start_idx = 0
        if has_separator:
            out.append("<thead><tr>")
            for cell in rows[0]:
                out.append(f"<th>{html.escape(cell)}</th>")
            out.append("</tr></thead>")
            start_idx = 1

        out.append("<tbody>")
        for row in rows[start_idx:]:
            out.append("<tr>")
            for cell in row:
                out.append(f"<td>{html.escape(cell)}</td>")
            out.append("</tr>")
        out.append("</tbody></table></div>")
        return "".join(out)

    def _split_markdown_into_sections(
        self, markdown_text: str, items: list[NarrationItem]
    ) -> list[tuple[NarrationItem, list[str]]]:
        """Align markdown paragraphs with narration items to prevent cross-section drift."""
        paragraphs = [p.strip() for p in markdown_text.split("\n\n") if p.strip()]
        if not items:
            return []
        if len(items) == 1:
            return [(items[0], paragraphs)]

        grouped: list[tuple[NarrationItem, list[str]]] = []
        item_idx = 0
        current_paras: list[str] = []

        for p in paragraphs:
            is_new_heading = False
            # Check for markdown heading # or numbered heading (e.g. 5.6.1)
            if p.startswith("#") or re.match(r"^\d+(\.\d+)+\s+", p):
                if item_idx > 0:
                    is_new_heading = True
                elif item_idx == 0 and any(
                    cp.startswith("#") or re.match(r"^\d+(\.\d+)+\s+", cp) for cp in current_paras
                ):
                    is_new_heading = True

            if is_new_heading and (item_idx + 1) < len(items):
                grouped.append((items[item_idx], current_paras))
                item_idx += 1
                current_paras = [p]
            else:
                current_paras.append(p)

        if current_paras and item_idx < len(items):
            grouped.append((items[item_idx], current_paras))
            item_idx += 1

        # Fallback padding for any trailing items
        while item_idx < len(items):
            grouped.append((items[item_idx], []))
            item_idx += 1

        return grouped

    def _render_textbook_markdown(self, paragraphs: list[str]) -> str:
        """Convert textbook markdown paragraphs into styled HTML elements."""
        body_parts: list[str] = []
        for p in paragraphs:
            lines = [ln.strip() for ln in p.strip().splitlines() if ln.strip()]
            is_table = len(lines) >= 2 and all(ln.startswith("|") and ln.endswith("|") for ln in lines)

            if is_table:
                body_parts.append(self._render_markdown_table(p))
            elif p.startswith("#"):
                level = min(6, p.count("#"))
                clean_title = p.lstrip("#").strip()
                body_parts.append(f"<h{level}>{html.escape(clean_title)}</h{level}>")
            elif p.startswith("$$") and p.endswith("$$"):
                body_parts.append(f'<div class="math-block">{html.escape(p)}</div>')
            else:
                body_parts.append(f"<p>{html.escape(p)}</p>")
        return "".join(body_parts)

    def _render_sections_html(
        self,
        markdown_text: str,
        narration_items: list[NarrationItem],
    ) -> str:
        """Render distinct lesson sections with dual-mode views and karaoke sentence sync."""
        # 1. Map markdown paragraphs to sections (use item.section_markdown if available)
        section_groups: list[tuple[NarrationItem, list[str]]] = []
        has_direct_markdown = any(bool(it.section_markdown.strip()) for it in narration_items)

        if has_direct_markdown:
            for it in narration_items:
                paras = [p.strip() for p in it.section_markdown.split("\n\n") if p.strip()]
                section_groups.append((it, paras))
        else:
            section_groups = self._split_markdown_into_sections(markdown_text, narration_items)

        html_sections: list[str] = []

        for item, paras in section_groups:
            sec_start = item.full_track_offset_seconds
            sec_dur = max(1.0, item.duration_seconds)
            sec_end = sec_start + sec_dur
            sec_id = item.section_id or "01"

            # -------------------------------------------------------------
            # Panel 1: Spoken Lesson (Karaoke Read-Along)
            # -------------------------------------------------------------
            script_text = item.target_script.strip()
            if not script_text and paras:
                script_text = " ".join(
                    p for p in paras if not p.startswith("#") and not p.startswith("|") and not p.startswith("$$")
                )

            spoken_sentences = self._split_into_sentences(script_text)
            sentence_spans = self._compute_sentence_spans(spoken_sentences, sec_start, sec_dur)

            karaoke_spans = []
            for s_idx, (s_clean, s_start, s_end) in enumerate(sentence_spans):
                karaoke_spans.append(
                    f'<span class="sentence" id="sent-{sec_id}-{s_idx}" '
                    f'data-start="{s_start:.2f}" data-end="{s_end:.2f}">{html.escape(s_clean)}</span>'
                )

            spoken_panel_html = f"""
            <div class="panel-spoken">
              <div class="karaoke-container">
                <p style="margin: 0;">{" ".join(karaoke_spans) if karaoke_spans else html.escape(item.section_title)}</p>
              </div>
            </div>"""

            # -------------------------------------------------------------
            # Panel 2: Textbook Reference Content
            # -------------------------------------------------------------
            textbook_content = self._render_textbook_markdown(paras)
            if not textbook_content:
                textbook_content = f"<h2>{html.escape(item.section_title)}</h2><p>{html.escape(script_text)}</p>"

            textbook_panel_html = f"""
            <div class="panel-textbook" style="display: none;">
              <div class="textbook-container">
                {textbook_content}
              </div>
            </div>"""

            section_block = f"""
      <section class="lesson-section" id="section-{sec_id}" data-start="{sec_start:.2f}" data-end="{sec_end:.2f}">
        <div class="section-header-bar">
          <div class="section-header-left">
            <span class="section-badge">Section {sec_id}: {html.escape(item.section_title)}</span>
            <button class="section-play-btn" onclick="seekAndPlay({sec_start:.2f})">▶ Play Section ({round(item.duration_seconds, 1)}s)</button>
          </div>
          <div class="section-tabs">
            <button class="section-tab-btn active" data-tab="spoken" onclick="switchSectionTab('{sec_id}', 'spoken')">🎙️ Spoken Read-Along</button>
            <button class="section-tab-btn" data-tab="textbook" onclick="switchSectionTab('{sec_id}', 'textbook')">📖 Textbook Content</button>
          </div>
        </div>
        <div class="section-body">
          {spoken_panel_html}
          {textbook_panel_html}
        </div>
      </section>"""
            html_sections.append(section_block)

        return "\n".join(html_sections)

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
