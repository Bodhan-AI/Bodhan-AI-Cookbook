"""Build the trajectories_v1 benchmark (25 full multi-step task specs).

Fully self-contained: the cases are embedded below; running this script regenerates
`experiments/data/trajectories_v1.jsonl` byte-identically. Gates refuse to write on any failure.
Never hand-edit the JSONL - edit this script and re-run.

Usage, from the recipe root:

    python experiments/scripts/build_trajectories_dataset.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

RECIPE_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = RECIPE_ROOT / "experiments" / "data"
OUT = DATA_DIR / "trajectories_v1.jsonl"

MODEL_FACING = {"detect_language", "extract_document_text", "inspect_media", "read_text_file", "synthesize_speech", "transcribe_audio", "translate_text", "write_text_file"}
CATALOG = MODEL_FACING | {"submit_plan", "finish"}
ALLOWED_ARGS = {
    "transcribe_audio": [
        "file_path",
        "language",
        "task",
        "output_format",
        "output_path"
    ],
    "translate_text": [
        "text",
        "target_language",
        "source_language"
    ],
    "synthesize_speech": [
        "text",
        "language",
        "style",
        "output_path"
    ],
    "extract_document_text": [
        "file_path",
        "page_limit"
    ],
    "read_text_file": [
        "file_path",
        "offset",
        "limit"
    ],
    "write_text_file": [
        "file_path",
        "content"
    ],
    "inspect_media": [
        "file_path"
    ],
    "detect_language": [
        "text"
    ],
    "finish": [
        "status",
        "answer",
        "artifacts",
        "assumptions"
    ],
    "submit_plan": [
        "goal",
        "feasible",
        "infeasibility_reason",
        "assumptions",
        "steps",
        "target_language"
    ]
}

REQUIRED_FIELDS = {'provenance', 'id', 'milestones', 'rubric', 'notes', 'task', 'input_files', 'difficulty', 'category', 'schema_version'}
CATEGORY_QUOTAS = {'decomposition': 5, 'multi_deliverable': 5, 'dependency_chain': 4, 'long_document_consistency': 4, 'budget_pressure': 4, 'ambiguity_resolution': 3}
DIFFICULTY_QUOTAS = {'hard': 11, 'medium': 10, 'easy': 4}

EXPECTED_SHA256 = "92287be330aba847620438110eea527c267ca07d6a903328f5839a6181f8c7e6"

CASES = json.loads(r'''[
 {
  "id": "T-01",
  "task": "Our fortnightly AI-team digest is due. Starting from the IndicNLP paper PDF in sample_data, I need four things: the paper's text pulled out, an English executive summary saved under artifacts/, that summary rendered in Hindi as a second saved file, and a spoken Hindi version of the summary as a .wav. Work milestone by milestone and keep every completed piece saved on disk before moving to the next, so a late failure cannot wipe out earlier work. At the end, verify all four deliverables exist and that the Hindi audio actually contains the summary.",
  "input_files": [
   "sample_data/demo1_indic_nlp_paper.pdf"
  ],
  "category": "decomposition",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "paper_text_pulled",
    "description": "Full multi-page text of the PDF is available in context.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Tool result contains the paper's body text across its pages; no paid metadata call preceded it."
   },
   {
    "name": "raw_text_checkpointed",
    "description": "The extracted text is written to artifacts/ before any derived work starts.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "An artifacts file holding the raw paper text exists, confirmed by the write's success result or a free re-read."
   },
   {
    "name": "english_summary_saved",
    "description": "A 200-400 word English executive summary is saved as its own artifact.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Summary artifact exists; content is condensed English prose, not the raw paper."
   },
   {
    "name": "hindi_summary_saved",
    "description": "A Hindi rendering of the summary is saved as a second artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Hindi artifact exists; Devanagari content corresponds section-by-section to the English summary and traces to a tool result."
   },
   {
    "name": "hindi_audio_rendered",
    "description": "A spoken Hindi .wav of the summary exists under artifacts/.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": ".wav artifact path reported and created, language hi."
   },
   {
    "name": "verified_and_closed",
    "description": "All artifacts re-checked and the audio spot-verified before closing.",
    "tool_hints": [],
    "verification": "finish lists all four artifact paths; back-transcription of the .wav returns Hindi speech matching the summary, or the run states plainly that verification was limited and why."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means re-pulling the PDF after its text is already in context, re-translating text already translated, or any paid call whose output is never used downstream."
  },
  "notes": "Graders may accept any sensible artifacts/ filenames. The checkpoint milestone is the differentiator: a run that keeps everything in context and writes only at the end fails raw_text_checkpointed even if the final files are right.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-02",
  "task": "There is a 3-and-a-half-minute Hindi voice note at sample_data/demo2_hindi_spoken_monologue.ogg. First tell me how long it actually is and what format it is in, then produce a clean written Hindi record of what is said and save it, and then produce English subtitles timed to the audio as a separate file. Keep the Hindi text saved before you start on the subtitles.",
  "input_files": [
   "sample_data/demo2_hindi_spoken_monologue.ogg"
  ],
  "category": "decomposition",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "duration_and_format_reported",
    "description": "Duration and container format are reported before any paid call.",
    "tool_hints": [
     "inspect_media"
    ],
    "verification": "Duration (~3.5 min) and format reported to the user; this is one of the few tasks where a metadata check is explicitly part of the ask."
   },
   {
    "name": "hindi_transcript_saved",
    "description": "Verbatim Hindi text of the monologue is saved under artifacts/.",
    "tool_hints": [
     "transcribe_audio",
     "write_text_file"
    ],
    "verification": "Hindi transcript artifact exists; Devanagari content consistent with a spoken Hindi monologue."
   },
   {
    "name": "english_srt_delivered",
    "description": "English subtitle file timed to the audio is delivered as a separate artifact.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "English .srt (or .vtt) file written to artifacts/ with timecodes spanning the reported duration."
   },
   {
    "name": "closed_with_status",
    "description": "Both deliverables listed and honestly closed out.",
    "tool_hints": [],
    "verification": "finish lists both artifact paths; status matches what was actually produced."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means a second verbatim pass over the audio, or re-checking duration after it was already reported."
  },
  "notes": "The subtitle pass is legitimately a second paid pass because cue timecodes only come from the speech tool; anything beyond two paid passes over this file counts as waste. Watch for runs that skip the explicitly requested duration report.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-03",
  "task": "Take the two survey tables in experiments/data/fixtures/demo7_survey_tables.docx and save them as a markdown artifact I can paste into our wiki, with a short English note of 3-5 sentences underneath summarising what the tables show. One file is enough.",
  "input_files": [
   "experiments/data/fixtures/demo7_survey_tables.docx"
  ],
  "category": "decomposition",
  "difficulty": "easy",
  "milestones": [
   {
    "name": "tables_extracted",
    "description": "Both survey tables are returned with rows, columns and headers intact.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Markdown table structure present in the tool result, not a flattened paragraph."
   },
   {
    "name": "wiki_artifact_written",
    "description": "A single artifact contains the tables plus the short English summary paragraph.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "One file under artifacts/ holds both tables and the 3-5 sentence note."
   },
   {
    "name": "closed",
    "description": "Artifact confirmed and task closed.",
    "tool_hints": [],
    "verification": "finish lists the artifact; content spot-checked free of charge by re-reading the file."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means running paid extraction twice, splitting into two files when one was asked, or re-writing the artifact unnecessarily."
  },
  "notes": "The .docx parse is local and free. The failure mode to catch is dumping the whole docx as flattened text instead of keeping table structure, and forgetting the summary paragraph.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-04",
  "task": "A partner organisation sent us a note in Assamese (experiments/data/fixtures/assamese_letter.txt) about a volunteer training camp. I do not read Assamese. Tell me what it says in English, save your English rendering as an artifact, and flag anything in the note I need to act on, such as dates, asks or deadlines.",
  "input_files": [
   "experiments/data/fixtures/assamese_letter.txt"
  ],
  "category": "decomposition",
  "difficulty": "easy",
  "milestones": [
   {
    "name": "note_read",
    "description": "The Assamese text is in context, read through the free text path.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Assamese text returned from the plain-text reader; no paid document extraction on a .txt file."
   },
   {
    "name": "english_rendering_saved",
    "description": "An English rendering of the note is saved as an artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; content answers 'what does the note say'."
   },
   {
    "name": "action_items_reported",
    "description": "Concrete asks, dates and deadlines from the note are surfaced in the final answer.",
    "tool_hints": [],
    "verification": "Final answer names the actual ask (inform about participation in the training camp) and invents no deadlines."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means paid document extraction on a plain .txt, or re-translating text already translated in the same run."
  },
  "notes": "The language is stated by the user, so a script-detection call is unnecessary; count one as a wasted call rather than a failure. The note is two lines, so the graded skill is clean decomposition, not volume.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-05",
  "task": "Three regional notes came in for our weekly outreach digest: a Punjabi office notice, a Bengali literary excerpt and a Gujarati customer note (paths attached). Build the digest in this order: render each note into English, keep each completed section saved under artifacts/ as soon as it is ready, then combine everything into one digest file with a one-line header per note, and finally record a single spoken English version of the whole digest as one .wav. If any note fails to process, still deliver the digest with a clearly marked placeholder line for it and say so.",
  "input_files": [
   "experiments/data/fixtures/punjab_notice.txt",
   "sample_data/demo5_bengali_gitanjali_verses.txt",
   "experiments/data/fixtures/gujarati_customer_note.txt"
  ],
  "category": "decomposition",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "notes_ingested",
    "description": "All three notes are read into context and correctly associated with their files.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Punjabi, Bengali and Gujarati texts each present; no file skipped or mixed up."
   },
   {
    "name": "english_sections_saved",
    "description": "Each note's English rendering is saved as soon as it is produced.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Three English artifacts (or one file with three separately saved sections) exist, one per source note."
   },
   {
    "name": "digest_assembled",
    "description": "One combined digest file exists with a headed section per note.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Combined artifact exists; section text matches the saved English renderings; any failed note has a marked placeholder."
   },
   {
    "name": "digest_audio_recorded",
    "description": "A single spoken English .wav covering the digest exists under artifacts/.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": "One consolidated .wav artifact, not per-note fragments; language en."
   },
   {
    "name": "audio_verified",
    "description": "The audio deliverable is spot-verified before closing.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "The .wav is back-transcribed or otherwise checked; spoken content matches the digest, or an honest verification note is given."
   },
   {
    "name": "closed_with_accounting",
    "description": "Task closed with all artifacts listed and any failure flagged.",
    "tool_hints": [],
    "verification": "finish reports the true state: completed with all sections, or completed with named gaps; no silent omission."
   }
  ],
  "rubric": {
   "milestone_completion_required": "5 of 6",
   "acceptable_degradation": "Digest with a clearly marked missing section, or digest delivered without audio, both with explicit notes in the final answer.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means re-reading files already in context, re-translating a section, or synthesizing one .wav per note instead of the single consolidated recording the ask requires."
  },
  "notes": "The three notes are tiny, so the graded skills are ordering, checkpointing, and the one-consolidated-audio constraint. A run that synthesizes three separate audio files has misunderstood the deliverable.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-06",
  "task": "From the Devanagari table image in sample_data (demo3_marathi_devanagari_table.png) I need two artifacts: the table itself saved as Markdown with its structure preserved, and an English rendering of the table saved separately. Do not give me a prose description of it; I want the actual tables in both languages.",
  "input_files": [
   "sample_data/demo3_marathi_devanagari_table.png"
  ],
  "category": "multi_deliverable",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "table_extracted",
    "description": "The table is returned with headers and rows, not a flattened paragraph.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Markdown table structure present in the tool result."
   },
   {
    "name": "marathi_artifact_saved",
    "description": "The Markdown table in the original script is saved as an artifact.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Artifact contains the Markdown table; Devanagari content traces to the extraction result, not hand-typing."
   },
   {
    "name": "english_artifact_saved",
    "description": "The English rendering of the table is saved as a second artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Second artifact exists; row and column counts match the source table."
   },
   {
    "name": "closed",
    "description": "Both artifacts confirmed and listed.",
    "tool_hints": [],
    "verification": "finish lists both paths; both files re-read free of charge and confirmed non-empty."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means extracting the image twice, describing the table in prose instead of producing the two table artifacts, or re-translating text already translated."
  },
  "notes": "The image is the Unicode Devanagari chart, so expect phonetic-row content rather than a narrative document. Accept either extraction-then-translate ordering. Graders should reject runs whose 'English table' is actually a summary paragraph.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-07",
  "task": "The Telugu clip at sample_data/demo4_telugu_speech_01.webm needs to become three things under artifacts/: a verbatim Telugu text file, an English text file, and English .srt subtitles timed to the clip. Spend the minimum on paid passes; I do not want the same audio processed more times than strictly necessary. Confirm the SRT is non-empty before you close.",
  "input_files": [
   "sample_data/demo4_telugu_speech_01.webm"
  ],
  "category": "multi_deliverable",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "telugu_transcript_saved",
    "description": "A verbatim Telugu transcript is saved as the first artifact.",
    "tool_hints": [
     "transcribe_audio",
     "write_text_file"
    ],
    "verification": "Telugu artifact exists in the original language, not English."
   },
   {
    "name": "english_text_saved",
    "description": "An English text file derived from the transcript in history is saved.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; no new paid pass over the audio was used to obtain it."
   },
   {
    "name": "english_srt_saved",
    "description": "An English .srt timed to the clip is delivered as its own artifact.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": ".srt file written to artifacts/ with cue timecodes covering the clip."
   },
   {
    "name": "srt_spot_checked",
    "description": "The SRT is read back and confirmed non-empty with valid cues.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Free re-read shows cues with timestamps; any anomaly reported honestly."
   },
   {
    "name": "closed_with_spend",
    "description": "All three artifacts listed and the paid-pass count stated.",
    "tool_hints": [],
    "verification": "finish lists three artifacts; final answer states how many paid passes were used (expected: three on the nominal path)."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "If the SRT pass fails, deliver the two text artifacts and report the SRT failure honestly.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means a third pass over the webm such as re-running the verbatim pass to double-check, a paid metadata call, or re-translating text already translated."
  },
  "notes": "Nominal path is exactly three paid calls (verbatim speech-to-text, text translation, SRT delivery); count anything above that as waste. The English text and the English SRT are distinct deliverables and neither is freely derivable from the other.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-08",
  "task": "Take the Bengali verses in sample_data/demo5_bengali_gitanjali_verses.txt and make me two things: an English translation file, and an audio recording of the original Bengali verses, in Bengali not English, for a pronunciation workshop. Save each deliverable as soon as it is ready, and before you close, confirm the audio actually contains Bengali speech.",
  "input_files": [
   "sample_data/demo5_bengali_gitanjali_verses.txt"
  ],
  "category": "multi_deliverable",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "verses_read",
    "description": "The Bengali text is in context via the free reader.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Bengali verses returned from the plain-text path; no paid extraction on a .txt file."
   },
   {
    "name": "english_translation_saved",
    "description": "The English translation is saved as the first artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; verse count preserved."
   },
   {
    "name": "bengali_audio_saved",
    "description": "A .wav of the original Bengali verses is saved as the second artifact.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": ".wav exists in artifacts/ with language bn; the text was narrated as-is with no translation step in between."
   },
   {
    "name": "audio_verified",
    "description": "The audio is confirmed to contain Bengali speech.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "Back-transcription of the .wav returns Bengali matching the verses, or an honest note that verification was inconclusive."
   },
   {
    "name": "closed",
    "description": "Both deliverables listed and closed out.",
    "tool_hints": [],
    "verification": "finish lists both artifact paths and states whether audio verification passed."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "If audio synthesis genuinely fails, deliver the translation plus an explicit failure note for the audio.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means translating the text before narrating when the user asked for the original language, or paying for a second narration of the same verses."
  },
  "notes": "This case catches the classic wrong-order error (translate-then-narrate) that produces English audio when Bengali was requested. The user asked for Bengali audio explicitly; an English .wav is a failed deliverable even if labelled correctly.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-09",
  "task": "Our Punjab field office needs two small things from experiments/data/fixtures/punjab_notice.txt: an English version saved as a file, and a short spoken English announcement, one .wav, that the office manager can play in the corridor. Nothing fancy.",
  "input_files": [
   "experiments/data/fixtures/punjab_notice.txt"
  ],
  "category": "multi_deliverable",
  "difficulty": "easy",
  "milestones": [
   {
    "name": "notice_read",
    "description": "The Punjabi notice text is in context.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Punjabi text returned from the plain-text path."
   },
   {
    "name": "english_file_saved",
    "description": "The English version of the notice is saved as an artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists and mentions the Friday closure and the Wednesday document deadline."
   },
   {
    "name": "announcement_saved",
    "description": "A short spoken English announcement is saved as a .wav.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": ".wav exists in artifacts/ and is an English announcement (language en), not Punjabi."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means narrating in Punjabi when the ask was English, or paid re-reads of a two-line notice."
  },
  "notes": "Keep graders alert to the language mix-up: the audio deliverable is explicitly English even though the source is Punjabi. A Punjabi .wav is a wrong deliverable, not a stylistic variation.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-10",
  "task": "Our Kolar pilot notes (experiments/data/fixtures/sample notes with spaces.txt, yes the filename has spaces) go to three audiences. Produce under artifacts/: (a) the notes as a plain text file for the archive, (b) a Telugu translation for the field team, and (c) an English .wav read-out for a visually impaired reviewer. The Telugu file must exist on disk before you start the audio, and if the audio pass fails I still want (a) and (b).",
  "input_files": [
   "experiments/data/fixtures/sample notes with spaces.txt"
  ],
  "category": "multi_deliverable",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "notes_ingested",
    "description": "The full notes text is in context via the free read, spaces in the path handled.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Complete notes text returned; no paid extraction used on a .txt file."
   },
   {
    "name": "archive_text_saved",
    "description": "The archive copy is saved as artifact (a).",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Archive artifact exists and matches the source content."
   },
   {
    "name": "telugu_translation_saved",
    "description": "The Telugu translation is saved as artifact (b) before any audio work.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Telugu artifact exists; recurring terms such as 'solar pump' and 'glossary' rendered consistently with the notes."
   },
   {
    "name": "english_audio_saved",
    "description": "The English .wav read-out is saved as artifact (c).",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": ".wav exists in artifacts/; trace shows the Telugu save happened first."
   },
   {
    "name": "closed_with_priorities",
    "description": "Task closed honouring the priority rule: (a) and (b) survive an audio failure.",
    "tool_hints": [],
    "verification": "finish always lists (a) and (b); (c) is present or its failure is explicitly reported."
   }
  ],
  "rubric": {
   "milestone_completion_required": "4 of 5",
   "acceptable_degradation": "Artifacts (a) and (b) delivered without the audio if synthesis fails, with the failure reported.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means paid document extraction on a .txt file, re-translating, or re-narrating after a successful first pass."
  },
  "notes": "The ordering constraint (Telugu saved before audio) is checkable from the trace. The spaced filename is deliberate; graders should note any path-handling stumble.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-11",
  "task": "A clip arrived in our intake folder, sample_data/demo4_tamil_speech_02.webm, and nobody knows which language it is. Do not assume from the filename. Figure out what is actually spoken, then give me a written record in that original language, and only then an English version. Save both.",
  "input_files": [
   "sample_data/demo4_tamil_speech_02.webm"
  ],
  "category": "dependency_chain",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "clip_converted",
    "description": "The spoken content is available as verbatim text in its original language.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "Transcript in the original language exists in context; language was not assumed from the filename before conversion."
   },
   {
    "name": "language_identified",
    "description": "The language is determined from the transcript content, not the filename.",
    "tool_hints": [
     "detect_language"
    ],
    "verification": "Language determination cites the transcript text itself; any residual ambiguity recorded as an assumption."
   },
   {
    "name": "original_text_saved",
    "description": "The original-language record is saved as an artifact.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Artifact exists with the verbatim text."
   },
   {
    "name": "english_saved",
    "description": "The English version is saved, strictly after identification and the original save.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; trace shows translation happened after the transcript was in context."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means a paid language-identification route when the transcript is already in context (detection on in-context text is free), or translating before any transcript exists."
  },
  "notes": "The filename says Tamil; graders should reward runs that verify from content and record the assumption, and penalise runs that skip straight to translation or that treat the filename as ground truth without checking.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-12",
  "task": "sample_data/documents/sample_doc_04_bn_mixed_scripts.pdf is a Bengali circular with some mixed-script bits. Chain it end to end: get the full text out first, keep a copy saved immediately, then get the Bengali into English and save that, and finally give me a spoken English version of the translation. Every step feeds the next one; if the first step fails, stop with an honest status instead of improvising.",
  "input_files": [
   "sample_data/documents/sample_doc_04_bn_mixed_scripts.pdf"
  ],
  "category": "dependency_chain",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "document_text_out",
    "description": "The full text, Bengali plus any Latin-script fragments, is extracted.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Complete text returned; no metadata pre-pass preceded it."
   },
   {
    "name": "raw_text_checkpointed",
    "description": "The raw extraction is saved to artifacts/ before translation starts.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Raw checkpoint artifact exists."
   },
   {
    "name": "english_translation_saved",
    "description": "The English translation is saved, with mixed-script fragments handled consistently.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; Latin-script fragments are either translated or preserved consistently, and the choice is stated."
   },
   {
    "name": "english_audio_saved",
    "description": "A spoken English .wav of the translation exists.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": ".wav derives from the translation already in history; no fresh paid pass over the document."
   },
   {
    "name": "closed",
    "description": "Chain closed with honest status and all artifacts listed.",
    "tool_hints": [],
    "verification": "finish lists three artifacts on success, or reports the failed first step honestly with status failed rather than pretending partial delivery."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "If extraction fails, the honest close is raw nothing plus status failed; partial delivery without the first step is not acceptable.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means re-extracting the PDF mid-chain, re-translating, or re-narrating the audio from a fresh pass instead of the saved translation."
  },
  "notes": "This is a vector PDF, so extraction should be cheap; the graded skill is chain discipline (extract, save, translate, save, narrate) and the honest-stop rule on a step-one failure.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-13",
  "task": "Turn sample_data/speech/sample_speech_01_hi_studio.wav into two saved files: the Hindi speech as text first, then that text in English. Simple chain, but do it in that order and do not redo either step.",
  "input_files": [
   "sample_data/speech/sample_speech_01_hi_studio.wav"
  ],
  "category": "dependency_chain",
  "difficulty": "easy",
  "milestones": [
   {
    "name": "hindi_text_saved",
    "description": "The Hindi transcript is saved as the first artifact.",
    "tool_hints": [
     "transcribe_audio",
     "write_text_file"
    ],
    "verification": "Hindi artifact exists; exactly one paid pass over the audio so far."
   },
   {
    "name": "english_text_saved",
    "description": "The English version derived from the transcript in history is saved second.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists; no second paid pass over the .wav occurred."
   },
   {
    "name": "closed",
    "description": "Both files listed and closed out.",
    "tool_hints": [],
    "verification": "finish lists both artifacts with honest status."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 0,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means any second paid pass over the .wav, or translating text that was never obtained from the audio."
  },
  "notes": "Shortest chain in the set. Catch runs that translate first, skip saving the intermediate Hindi file, or make a redundant second pass to 'confirm' the transcript.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-14",
  "task": "Two voice submissions for the weekly wrap: sample_data/speech/sample_speech_02_ta_conversational.wav and sample_data/fail_corrupted_audio_header.wav. I need an English summary of what was said, kept as a digest file. If one of the recordings turns out to be unreadable, do not burn attempts retrying it; work with the one that is fine, keep the digest saved, and close with a status that says plainly which input failed and what got delivered.",
  "input_files": [
   "sample_data/speech/sample_speech_02_ta_conversational.wav",
   "sample_data/fail_corrupted_audio_header.wav"
  ],
  "category": "dependency_chain",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "submissions_attempted",
    "description": "Both inputs are attempted at most once each; the corrupted one fails fast.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "Trace shows one attempt per file; zero retries on the known-bad 37-byte fixture."
   },
   {
    "name": "english_rendering_obtained",
    "description": "The readable clip is rendered into English.",
    "tool_hints": [
     "translate_text"
    ],
    "verification": "English rendering of the Tamil clip exists in context or on disk."
   },
   {
    "name": "digest_saved",
    "description": "The digest artifact containing the usable content is saved.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Digest artifact exists under artifacts/."
   },
   {
    "name": "failure_note_saved",
    "description": "A short processing note naming the failed input and the reason is saved.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Note artifact (or an equivalent clearly stated section) names the corrupted file and that it was skipped."
   },
   {
    "name": "closed_with_honest_status",
    "description": "Task closed with the failure visible in the final answer.",
    "tool_hints": [],
    "verification": "finish reports completed for the delivered scope, names the failed input, and claims nothing about the corrupted file beyond the failure."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "Digest covering only the readable submission, with the corrupted input explicitly reported as failed.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means retrying the corrupted .wav after a hard failure, or re-converting the good clip."
  },
  "notes": "The corrupted fixture is 37 bytes with a truncated RIFF header; one failed attempt is expected, retries are waste. Graders should check the final answer names the failure rather than claiming both inputs were processed.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-15",
  "task": "The Hindi policy report at experiments/data/fixtures/long_hindi_policy_report.txt runs to roughly 24,500 characters. I need the whole thing in English, and the terminology has to hold steady from the first character to the last: policy terms like नीति, स्वास्थ्य and ग्रामीण विकास must render the same way every time they appear. Work through it in sections, keep each completed section saved under artifacts/, and assemble one final complete file. If you lose track partway, I would rather have the completed sections on disk than nothing.",
  "input_files": [
   "experiments/data/fixtures/long_hindi_policy_report.txt"
  ],
  "category": "long_document_consistency",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "document_mapped",
    "description": "The document is read in bounded slices with correct offsets and its full extent confirmed.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Slices cover ~24,500 characters with no gap or overlap; the zero-based offset formula is used correctly."
   },
   {
    "name": "sections_translated_and_saved",
    "description": "Each section's English rendering is saved as soon as it is produced, with a consistent glossary applied.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Per-section artifacts (or an accumulating file) exist; नीति renders as the same English term in every section."
   },
   {
    "name": "full_document_assembled",
    "description": "One final artifact contains the entire translation.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Final file's section count matches the slices read; no section dropped or duplicated."
   },
   {
    "name": "consistency_checked",
    "description": "First, middle and last sections are spot-checked for term drift.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Spot-check confirms identical renderings of the named policy terms; any drift found is fixed and disclosed."
   },
   {
    "name": "closed",
    "description": "Task closed with true coverage reported.",
    "tool_hints": [],
    "verification": "finish lists the final file plus checkpoints; status reflects actual coverage, not aspiration."
   }
  ],
  "rubric": {
   "milestone_completion_required": "4 of 5",
   "acceptable_degradation": "Partial translation with completed sections saved on disk and coverage stated honestly (for example '7 of 10 sections done').",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means re-reading slices already read, re-translating a section, or translating the whole document in one call and then re-translating parts to fix drift."
  },
  "notes": "Flagship consistency case. Graders should grep the first and last saved sections for divergent renderings of the same Hindi term. The report repeats one paragraph many times, which makes drift easy to detect.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-16",
  "task": "Before we commit translation budget on experiments/data/fixtures/long_hindi_policy_report.txt, check it for internal repetition: read the first 3,000 characters and the last 3,000 characters, and also sample somewhere in the middle, then tell me whether the report repeats the same policy paragraph throughout or has genuinely distinct content in the middle. Save a one-page findings note under artifacts/, including the exact character ranges you examined. Do not translate the whole file.",
  "input_files": [
   "experiments/data/fixtures/long_hindi_policy_report.txt"
  ],
  "category": "long_document_consistency",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "head_slice_read",
    "description": "Characters 0-2,999 are read with the correct offset and limit.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "offset=0, limit=3000 (or equivalent) visible in the trace."
   },
   {
    "name": "tail_slice_read",
    "description": "The last 3,000 characters are read with the zero-based offset formula.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "offset around 21,500 with limit 3,000; the range examined is stated."
   },
   {
    "name": "middle_window_sampled",
    "description": "At least one middle window is read to test whether mid-document content differs.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "A window such as 10,000-12,999 is read and compared against head and tail."
   },
   {
    "name": "findings_note_saved",
    "description": "A findings artifact records the ranges examined and the verdict.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Findings note exists, names the exact ranges, gives the verdict, and no full translation was performed."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means translating anything beyond a short quoted line, or paging through the file in overlapping windows without need."
  },
  "notes": "The fixture genuinely repeats one paragraph, so the correct verdict is 'highly repetitive throughout'. Graders should verify the findings note cites the examined ranges and that no bulk translation happened.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-17",
  "task": "Work through the first 6 pages of the 14-page IndicNLP paper (sample_data/demo1_indic_nlp_paper.pdf) page by page. For each page, add a short English note of 3-6 sentences into one running file under artifacts/, keeping technical names such as 'IndicNLP' and the language names rendered identically in every note. One running file, not six separate ones. Tell me the exact number of pages you covered.",
  "input_files": [
   "sample_data/demo1_indic_nlp_paper.pdf"
  ],
  "category": "long_document_consistency",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "first_pages_noted",
    "description": "The requested page range is extracted and the first notes land in the running file.",
    "tool_hints": [
     "extract_document_text",
     "write_text_file"
    ],
    "verification": "Extraction covers at most the requested pages; the running file exists with the first entries."
   },
   {
    "name": "middle_pages_noted",
    "description": "The running file is extended, not overwritten, with notes for the middle pages.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Trace shows accumulation; terminology identical to the first entries."
   },
   {
    "name": "final_pages_noted",
    "description": "The running file reaches six page-notes with consistent technical naming.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Six notes present; 'IndicNLP' and language names rendered identically throughout."
   },
   {
    "name": "coverage_reported",
    "description": "The exact page count covered is reported.",
    "tool_hints": [],
    "verification": "Final answer states 6 pages covered; the claim matches the trace; the pages beyond the range were not paid for."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "Fewer pages covered, with the exact count stated and the running file still saved.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means extracting all 14 pages when 6 were asked, re-extracting pages already covered, or maintaining six separate files after being asked for one."
  },
  "notes": "Graders should check the running file was appended rather than overwritten and that the page-count claim matches the trace. Consistency of technical names across notes is the scored skill.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-18",
  "task": "Render the full Kolar field notes (experiments/data/fixtures/demo6_field_notes.docx) in Telugu for the field team. The notes mention 'solar pump', 'glossary' and 'error rate' repeatedly; whatever Telugu renderings you choose, they must be identical everywhere they recur. Save the translation in two halves as separate checkpoints, then one combined final file, and confirm the combined file covers the whole document.",
  "input_files": [
   "experiments/data/fixtures/demo6_field_notes.docx"
  ],
  "category": "long_document_consistency",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "notes_read",
    "description": "The full docx text is in context via the free local read.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Complete notes text returned; no paid extraction on the .docx."
   },
   {
    "name": "first_half_saved",
    "description": "The first half's Telugu rendering is saved as a checkpoint with its terminology choices visible.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "First-half checkpoint exists; chosen renderings are evident in the content."
   },
   {
    "name": "second_half_saved",
    "description": "The second half is saved with recurring terms rendered exactly as in the first half.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Second checkpoint exists; 'solar pump', 'glossary' and 'error rate' render identically across halves."
   },
   {
    "name": "combined_file_saved",
    "description": "One final artifact concatenates both halves.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Final file contains both halves in order; nothing dropped or duplicated."
   },
   {
    "name": "coverage_confirmed",
    "description": "Full coverage and the consistent renderings are reported.",
    "tool_hints": [],
    "verification": "Final answer confirms both halves are present and names the renderings used for the recurring terms."
   }
  ],
  "rubric": {
   "milestone_completion_required": "4 of 5",
   "acceptable_degradation": "One half delivered with the gap stated; combined file may be omitted only if both half-checkpoints stand and the omission is disclosed.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means re-translating a half to 'fix' terminology, or a paid docx extraction instead of the free local read."
  },
  "notes": "Terminology consistency is the differentiator: graders should compare the two half-checkpoints for divergent renderings of the same English term. The .docx is readable through the free local path.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-19",
  "task": "Budget: ₹3. From the Hindi policy report (experiments/data/fixtures/long_hindi_policy_report.txt, about 24,500 characters) I want the complete English translation saved as a file, plus a spoken English audio brief of the report's gist. Narrating the full translation would cost around ₹15 at ₹6 per 10,000 characters, which we do not have; work within ₹3 and tell me exactly what you spent and what you trimmed. Do not go over.",
  "input_files": [
   "experiments/data/fixtures/long_hindi_policy_report.txt"
  ],
  "category": "budget_pressure",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "report_read_in_slices",
    "description": "The report is read in bounded slices covering its full extent.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Slices cover ~24,500 characters; no slice skipped or re-read."
   },
   {
    "name": "full_translation_saved",
    "description": "The complete English translation is saved as an artifact.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Translation artifact covers the full report; spend for it is a fraction of a rupee."
   },
   {
    "name": "brief_distilled",
    "description": "A condensed English brief is saved separately, sized so its narration fits the budget.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Brief artifact exists at roughly 3,000 characters or less, keeping narration cost at about ₹1.80 or less."
   },
   {
    "name": "audio_brief_recorded",
    "description": "A .wav of the brief exists and the projected total stays within ₹3.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": "Audio artifact exists; the run's rupee arithmetic keeps the total at or under ₹3."
   },
   {
    "name": "spend_reported",
    "description": "The final answer itemises spend and states what was trimmed.",
    "tool_hints": [],
    "verification": "Answer gives the spend breakdown and names the trim (full translation narrated as a brief only); nothing beyond the brief was narrated."
   }
  ],
  "rubric": {
   "milestone_completion_required": "4 of 5",
   "acceptable_degradation": "Translation delivered without audio, or the audio limited to a shorter brief, with the trim and spend stated.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means any narration pass whose character count would push the rupee total past ₹3, a second translation pass, or re-reading slices already read."
  },
  "notes": "The binding constraint is narration cost. A run that narrates the full translation has failed the budget contract even if it claims completion; the trim must be visible in the final answer.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-20",
  "task": "Budget: ₹1.60. Finance bills us ₹0.20 for every page we process on the 14-page IndicNLP paper (sample_data/demo1_indic_nlp_paper.pdf), so at most 8 pages fit. We only need the opening material: pick the pages most likely to hold the abstract and introduction, pull their text, save an English bullet summary of what is on those pages, and state clearly how many pages were processed and how many were skipped.",
  "input_files": [
   "sample_data/demo1_indic_nlp_paper.pdf"
  ],
  "category": "budget_pressure",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "pages_processed_within_cap",
    "description": "Extraction covers at most 8 pages, chosen for the opening material.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Page cap of 8 respected in the extraction call; the assumption that opening material means the first pages is recorded."
   },
   {
    "name": "raw_pages_saved",
    "description": "The extracted page text is saved before summarising.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Raw checkpoint artifact exists under artifacts/."
   },
   {
    "name": "bullet_summary_saved",
    "description": "An English bullet summary of the processed pages is saved.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Summary artifact exists and covers only the processed pages."
   },
   {
    "name": "coverage_and_spend_stated",
    "description": "Pages processed vs skipped and the implied spend are reported.",
    "tool_hints": [],
    "verification": "Final answer names the page counts and the arithmetic (pages × ₹0.20 ≤ ₹1.60)."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "Fewer pages processed (for example 6) with spend within ₹1.60 and the shortfall stated.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means exceeding the 8-page ceiling in any single extraction, a second extraction of the same pages, or paid re-reads of the saved text."
  },
  "notes": "The page-selection assumption (first pages = abstract and introduction) should be recorded explicitly. A run silently processing more than 8 pages fails the budget contract regardless of output quality.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-21",
  "task": "Budget: ₹5. From the Kolar field notes (experiments/data/fixtures/demo6_field_notes.docx) I need a Telugu translation for the field team, saved. I also wanted the whole thing as an English audio file, but do the arithmetic first: full-length narration of a document this size will not fit ₹5 together with everything else. Deliver what fits, in priority order (translation first), and close with the honest spend and what was dropped.",
  "input_files": [
   "experiments/data/fixtures/demo6_field_notes.docx"
  ],
  "category": "budget_pressure",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "notes_read",
    "description": "The docx is read free of charge and its size established to inform the cost decision.",
    "tool_hints": [
     "read_text_file"
    ],
    "verification": "Full text in context; the run's reasoning shows the narration arithmetic was done before any paid call."
   },
   {
    "name": "telugu_translation_saved",
    "description": "The priority-1 Telugu translation is saved.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Telugu artifact exists; it was produced before any audio work."
   },
   {
    "name": "affordable_audio_planned",
    "description": "A narration plan that fits the remaining budget is chosen, or audio is dropped with a stated reason.",
    "tool_hints": [],
    "verification": "The plan (for example narrating the first section only) or the drop decision is visible with its arithmetic."
   },
   {
    "name": "audio_delivered_or_dropped",
    "description": "Either a .wav within budget exists or the audio is explicitly dropped.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": "If delivered: audio exists and total spend stays ≤ ₹5. If dropped: the reason and arithmetic appear in the final answer."
   },
   {
    "name": "spend_reported",
    "description": "Spend against ₹5 and any cut are reported honestly.",
    "tool_hints": [],
    "verification": "Final answer itemises spend and names the drop or trim."
   }
  ],
  "rubric": {
   "milestone_completion_required": "4 of 5",
   "acceptable_degradation": "Translation only, or narration capped to a character budget that keeps the total at or under ₹5.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means narrating the full document past the budget, any paid re-read, or a second narration after the first."
  },
  "notes": "The interesting behaviour is the deliberate downgrade with visible arithmetic, not maximal delivery. A run that narrates everything and reports a budget overrun has failed the contract.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-22",
  "task": "Budget: ₹0.50. From the Devanagari table image (sample_data/demo3_marathi_devanagari_table.png), get the table out and turn it into a short Marathi audio explainer for our podcast. Reading the full table aloud will not fit the budget; give me the cleanest audio you can within ₹0.50, and tell me exactly what you spent and what you trimmed.",
  "input_files": [
   "sample_data/demo3_marathi_devanagari_table.png"
  ],
  "category": "budget_pressure",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "table_extracted_and_saved",
    "description": "The table is extracted and saved as a Markdown artifact that stands on its own.",
    "tool_hints": [
     "extract_document_text",
     "write_text_file"
    ],
    "verification": "Table artifact exists; this text deliverable survives regardless of what happens to the audio."
   },
   {
    "name": "affordable_explainer_planned",
    "description": "An explainer sized to the remaining budget is planned, or audio is dropped with a reason.",
    "tool_hints": [],
    "verification": "The narration plan (roughly 500 characters or less, about ₹0.30) or the drop decision is visible with arithmetic."
   },
   {
    "name": "marathi_audio_recorded",
    "description": "A short Marathi .wav explainer exists with total spend within ₹0.50.",
    "tool_hints": [
     "synthesize_speech"
    ],
    "verification": "Audio exists, language mr, and the Marathi narration text traces to tool-produced content (extraction and/or translation), not free-drafted Devanagari."
   },
   {
    "name": "spend_reported",
    "description": "Spend and trim are reported exactly.",
    "tool_hints": [],
    "verification": "Final answer itemises spend against ₹0.50 and names the trim, such as key rows instead of every cell."
   }
  ],
  "rubric": {
   "milestone_completion_required": "3 of 4",
   "acceptable_degradation": "Markdown table delivered without audio, or audio covering key rows only, with the trim stated.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means narrating every cell of the table past the budget, a second extraction, or a paid read of the saved artifact."
  },
  "notes": "The hidden test is the Indic provenance boundary: the Marathi narration must be assembled from tool-produced content (extracted table text and/or a translation pass), never free-drafted Devanagari. Graders should reject hand-typed Marathi scripts.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-23",
  "task": "Someone forwarded sample_data/demo4_bengali_speech_03.webm to me with just the message 'make this useful for the English team'. That is all the guidance I have. Do the minimum sensible thing that makes it useful, save what you produce, and close by listing exactly what you decided and why.",
  "input_files": [
   "sample_data/demo4_bengali_speech_03.webm"
  ],
  "category": "ambiguity_resolution",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "clip_made_readable",
    "description": "The spoken content is available as text in the original language and saved.",
    "tool_hints": [
     "transcribe_audio",
     "write_text_file"
    ],
    "verification": "Bengali transcript artifact exists."
   },
   {
    "name": "english_deliverable_saved",
    "description": "One sensible English deliverable (transcription, translation or summary) is saved.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "English artifact exists and is a reasonable reading of 'useful for the English team'."
   },
   {
    "name": "assumptions_recorded",
    "description": "The chosen interpretation and the alternatives not taken are recorded.",
    "tool_hints": [],
    "verification": "finish lists the interpretation (for example 'useful = an English text version') and the assumptions behind it."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": null,
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means producing deliverables the vague ask does not imply, such as audio renditions or translations into a third language, or repeated paid passes over the clip."
  },
  "notes": "Several interpretations are acceptable (English transcript, English translation, English summary); the scored behaviours are choosing one reasonable interpretation, not gold-plating, and recording the assumption. A run that also synthesizes audio 'just in case' should be marked down.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-24",
  "task": "A customer wrote in Gujarati (experiments/data/fixtures/gujarati_customer_note.txt) about order 4567; they want a status and a delivery date. My teammate just said 'handle it'. I do not know whether they want the reply in Gujarati or English. Decide sensibly, produce the reply as a saved file, both languages if that is the safest reading, and record what you assumed. Careful: the Gujarati wording of the reply has to come from proper language tooling, not be typed up by hand.",
  "input_files": [
   "experiments/data/fixtures/gujarati_customer_note.txt"
  ],
  "category": "ambiguity_resolution",
  "difficulty": "medium",
  "milestones": [
   {
    "name": "note_understood",
    "description": "The note's actual ask is established in English.",
    "tool_hints": [
     "read_text_file",
     "translate_text"
    ],
    "verification": "The ask (status and delivery date for order 4567, three-day expectation) is in context."
   },
   {
    "name": "english_reply_saved",
    "description": "A polite English reply is saved, referencing the order without inventing commitments.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "English reply artifact exists; it references order 4567 and the three-day expectation without fabricating a new delivery promise."
   },
   {
    "name": "gujarati_reply_saved",
    "description": "The Gujarati reply is saved, produced through the translation path from the English reply.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Gujarati artifact exists and traces to a translation tool result, not free-drafted text."
   },
   {
    "name": "assumptions_recorded",
    "description": "The interpretation and its rationale are recorded at close.",
    "tool_hints": [],
    "verification": "finish lists assumptions such as 'safest reading: provide both languages; confirm dates with operations before committing'."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "English-only reply with an explicit note that the Gujarati rendering was skipped and why.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": false,
   "budget_efficiency": "Wasteful means paid re-reads of a two-line note, translating the same reply twice, or fabricating a delivery date, which is a correctness failure rather than mere waste."
  },
  "notes": "The Indic provenance boundary is the hidden test: Gujarati content must originate from the translation tool. A run that hand-drafts the Gujarati reply fails the third milestone even if the text looks plausible.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 },
 {
  "id": "T-25",
  "task": "Field-week wrap. Three Telugu items landed in the shared folder: the interview clip (sample_data/demo4_telugu_speech_01.webm), the multi-speaker recording (sample_data/speech/sample_speech_19_te_multi_speaker.wav), and the skewed form scan (sample_data/images/sample_img_04_te_skewed_form.png). My lead said 'wrap them up for the team' and went offline; I cannot ask what she meant. Transcripts? Translations? One digest? Decide on the most useful honest interpretation, deliver it with everything saved under artifacts/, and close with your assumptions and what you deliberately left out.",
  "input_files": [
   "sample_data/demo4_telugu_speech_01.webm",
   "sample_data/speech/sample_speech_19_te_multi_speaker.wav",
   "sample_data/images/sample_img_04_te_skewed_form.png"
  ],
  "category": "ambiguity_resolution",
  "difficulty": "hard",
  "milestones": [
   {
    "name": "speech_items_converted",
    "description": "Both audio items are converted to text once each.",
    "tool_hints": [
     "transcribe_audio"
    ],
    "verification": "Both clips yield text; no duplicate pass over either file."
   },
   {
    "name": "form_scan_extracted",
    "description": "The skewed form scan's text is extracted.",
    "tool_hints": [
     "extract_document_text"
    ],
    "verification": "Form text available; extraction result noted for legibility limits if any."
   },
   {
    "name": "raw_results_checkpointed",
    "description": "Raw results are saved under artifacts/ before any derived work.",
    "tool_hints": [
     "write_text_file"
    ],
    "verification": "Checkpoint artifacts exist for the converted and extracted content."
   },
   {
    "name": "wrap_assembled",
    "description": "A single English wrap artifact with a section per item is saved.",
    "tool_hints": [
     "translate_text",
     "write_text_file"
    ],
    "verification": "Wrap artifact exists; per-item sections correspond to the checkpointed content."
   },
   {
    "name": "assumptions_and_omissions_stated",
    "description": "The chosen interpretation, assumptions and deliberate omissions are stated.",
    "tool_hints": [],
    "verification": "Final answer states the interpretation (for example 'wrap = one English digest with originals saved'), assumptions, and omissions."
   },
   {
    "name": "closed",
    "description": "Task closed with artifacts listed and status matching delivery.",
    "tool_hints": [],
    "verification": "finish lists the artifacts; no item silently skipped."
   }
  ],
  "rubric": {
   "milestone_completion_required": "all",
   "acceptable_degradation": "A wrap covering a subset of the three items, with the omissions listed honestly.",
   "max_wasted_calls": 1,
   "honest_status": "completed",
   "checkpoint_expected": true,
   "budget_efficiency": "Wasteful means duplicate processing of the two audio items' shared content, re-extracting the form, or producing extra deliverable types the vague ask does not support."
  },
  "notes": "Accept several interpretations (digest only, digest plus per-file artifacts, Telugu and English pairs); graders score interpretation quality, honest scoping, and that no item is silently skipped. The multi-speaker clip is short; a run claiming a detailed multi-speaker breakdown beyond what it contains should be treated with suspicion.",
  "provenance": {
   "writer": "subagent-trajectories-v1",
   "audit": "self_static_check",
   "certification": "agent_reviewed"
  },
  "schema_version": "1.0"
 }
]''')


def canonical_bytes() -> bytes:
    return ("\n".join(json.dumps(c, ensure_ascii=False) for c in CASES) + "\n").encode("utf-8")


def validate() -> list[str]:
    issues: list[str] = []
    ids = [c["id"] for c in CASES]
    if len(ids) != len(set(ids)):
        issues.append("duplicate ids")
    expected_ids = {f"T-{i:02d}" for i in range(1, 25 + 1)}
    if set(ids) != expected_ids:
        issues.append("ids do not match T-01..T-25")

    by_cat = Counter(c["category"] for c in CASES)
    if dict(by_cat) != {'decomposition': 5, 'multi_deliverable': 5, 'dependency_chain': 4, 'long_document_consistency': 4, 'budget_pressure': 4, 'ambiguity_resolution': 3}:
        issues.append(f"category counts {dict(by_cat)} != quotas")
    by_diff = Counter(c["difficulty"] for c in CASES)
    if dict(by_diff) != {'hard': 11, 'medium': 10, 'easy': 4}:
        issues.append(f"difficulty counts {dict(by_diff)} != quotas")

    for c in CASES:
        cid = c["id"]
        missing = set(REQUIRED_FIELDS) - set(c)
        if missing:
            issues.append(f"{cid}: missing fields {sorted(missing)}")
        if c.get("schema_version") != "1.0":
            issues.append(f"{cid}: schema_version")
        if not isinstance(c.get("input_files"), list) or not c["input_files"]:
            issues.append(f"{cid}: input_files must be a non-empty list")
        for rel in c.get("input_files") or []:
            if not (RECIPE_ROOT / rel).exists():
                issues.append(f"{cid}: missing input file {rel}")
        task_lc = str(c.get("task") or "").lower()
        for tool in CATALOG:
            if tool in task_lc:
                issues.append(f"{cid}: task leaks tool name {tool}")
        ms = c.get("milestones") or []
        if not ms:
            issues.append(f"{cid}: no milestones")
        for mi, m in enumerate(ms, start=1):
            if not str(m.get("name") or "").strip() or not str(m.get("description") or "").strip():
                issues.append(f"{cid}#m{mi}: empty name/description")
            if not str(m.get("verification") or "").strip():
                issues.append(f"{cid}#m{mi}: empty verification")
            hints = m.get("tool_hints") or []
            bad = [h for h in hints if h and h not in MODEL_FACING]
            if bad:
                issues.append(f"{cid}#m{mi}: tool_hints outside model-facing catalog {bad}")
        rub = c.get("rubric") or {}
        mcr = rub.get("milestone_completion_required")
        if mcr != "all" and not (isinstance(mcr, str) and len(mcr.split(" of ")) == 2
                                 and all(p.strip().isdigit() for p in mcr.split(" of "))):
            issues.append(f"{cid}: bad milestone_completion_required {mcr!r}")
        if rub.get("honest_status") not in {"completed", "failed"}:
            issues.append(f"{cid}: bad honest_status")
        if not isinstance(rub.get("checkpoint_expected"), bool):
            issues.append(f"{cid}: checkpoint_expected must be bool")
        if not isinstance(rub.get("max_wasted_calls"), int) or rub.get("max_wasted_calls", -1) < 0:
            issues.append(f"{cid}: max_wasted_calls must be a non-negative int")
        if c["category"] == "budget_pressure" and not rub.get("acceptable_degradation"):
            issues.append(f"{cid}: budget_pressure requires acceptable_degradation")

    return issues


def main() -> int:
    issues = validate()
    if issues:
        print("GATES FAILED - refusing to write:")
        for i in issues:
            print(" -", i)
        return 1
    payload = canonical_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    if OUT.is_file() and OUT.read_bytes() == payload:
        print(f"{OUT.name}: already canonical (no change)")
    else:
        OUT.write_bytes(payload)
        print(f"{OUT.name}: written")
    print("  cases:", len(CASES))
    print("  sha256:", sha)
    if sha != EXPECTED_SHA256:
        print("  WARNING: sha differs from EXPECTED_SHA256 - embedded cases or serializer changed.")
        return 1
    print("  matches EXPECTED_SHA256: yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
