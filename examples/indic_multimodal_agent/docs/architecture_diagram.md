# Architecture Diagram

```mermaid
flowchart TD
    User(["User Task & Media Input"]) --> Agent["Bodhan Task Agent (bodhan_agent)"]
    
    subgraph Planning & Preflight
        Agent --> Inspect["Inspect Media\n(Duration, Format, Pages)"]
        Inspect --> Preflight{"Preflight Cost Check\n<= max_cost_inr?"}
        Preflight -- "Exceeded" --> FailStop["Stop Early & Honestly\n(Status: failed, Cost: ₹0.00)"]
        Preflight -- "OK" --> Route{"Route Pipeline"}
    end

    subgraph MCP Tool Server (bodhan_mcp)
        Route -- "Audio Subtitles" --> VS["Vertical Slice: Subtitles\n- Pause Chunking (<28s)\n- Indic-Transcribe\n- Indic-Translate (Batch)\n- Cue Timing (<42 ch, <=2 lines)"]
        Route -- "Scanned Document" --> PA["Pipeline A: Doc Audio Summary\n- PyMuPDF Page Render\n- Indic-OCR\n- Indic-Translate (Tamil)\n- Indic-Speak (MP3 Stitch)\n- Back-Transcribe Verify"]
        Route -- "Markdown Table" --> PB["Pipeline B: Table Translation\n- Structure Preservation\n- Indic-Translate (Marathi)\n- Row/Column Count Check"]
        Route -- "Language Detection" --> PC["Pipeline C: Auto-Detection\n- Indic-Transcribe (Auto-Detect)\n- Unicode Script & Heuristics"]
        Route -- "Non-Indic Target" --> PImp["Impossible Task\n(Status: impossible, Cost: ₹0.00)"]
    end

    subgraph Verification Suite
        VS --> Verifier["Automated Verifiers\n- Monotonicity & No Overlaps\n- Script & Unicode NFC\n- Numbers Preserved\n- Structure Integrity\n- Back-Transcribe Intelligibility"]
        PA --> Verifier
        PB --> Verifier
        PC --> Verifier
    end

    subgraph Artifacts & Reporting
        Verifier --> Out["Sandboxed out/ Folder\n- artifacts/* (Relative Paths)\n- result.md (Summary & Markdown)\n- report.json (Machine Readable)\n- trace.jsonl (Step Audit Trail)"]
    end
```

