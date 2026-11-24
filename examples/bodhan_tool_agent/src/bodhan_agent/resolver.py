"""Input file discovery, deterministic matching, and tie-breaking resolver."""

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bodhan_mcp.tools.media import inspect_media


class FileRef(BaseModel):
    """Reference to an inventoried media file."""
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    filename: str
    kind: str
    duration_s: float | None = None
    pages: int | None = None
    size_mb: float = 0.0


class ResolvedInputs(BaseModel):
    """Result of file resolution."""
    model_config = ConfigDict(extra="forbid")

    inventory: list[FileRef] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)  # list of FileRef.id
    rationale: str = ""
    missing_file_requested: str | None = None


def resolve(
    files_dir: Path | str,
    task: str,
    llm_tiebreaker: Any = None,
) -> ResolvedInputs:
    """Resolve input files from files_dir matching the given plain-text task.

    Strictly code-driven:
      1. Inventory via inspect_media (cached).
      2. Exact filename match -> ordinal -> plural -> singular.
      3. Ambiguity resolved by type-consistency with task verb, then metadata-only LLM tiebreak.
      4. Reject path traversal.
    """
    directory = Path(files_dir).resolve()
    if not directory.exists() or not directory.is_dir():
        return ResolvedInputs(
            inventory=[],
            selected=[],
            rationale=f"Input directory does not exist: {directory}",
        )

    # 1. Build inventory
    file_list = sorted([p for p in directory.iterdir() if p.is_file()])
    inventory: list[FileRef] = []
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    for idx, p in enumerate(file_list):
        # Prevent path traversal
        try:
            p.resolve().relative_to(directory)
        except ValueError:
            continue

        file_id = alphabet[idx] if idx < len(alphabet) else f"F{idx + 1}"
        insp = inspect_media(str(p))
        if hasattr(insp, "kind"):
            inventory.append(
                FileRef(
                    id=file_id,
                    path=str(p),
                    filename=p.name,
                    kind=insp.kind,
                    duration_s=insp.duration_s,
                    pages=insp.pages,
                    size_mb=insp.size_mb,
                )
            )

    task_lower = task.lower()
    file_mention_match = re.search(r"[\w-]+\.(wav|mp3|m4a|pdf|png|jpg|jpeg|mp4|mov|txt)\b", task_lower)

    if not inventory:
        missing_name = file_mention_match.group(0) if file_mention_match else None
        return ResolvedInputs(
            inventory=[],
            selected=[],
            rationale="No valid input files found in directory."
            + (f" Specifically requested '{missing_name}' was not found." if missing_name else ""),
            missing_file_requested=missing_name,
        )

    # 2a. Match explicit filename mentioned in task
    for f in inventory:
        if f.filename.lower() in task_lower:
            return ResolvedInputs(
                inventory=inventory,
                selected=[f.id],
                rationale=f"Selected '{f.filename}' (ID: {f.id}) via exact filename match in task text.",
            )

    # Check if task named an explicit extension like 'foo.wav' that was not found
    file_mention_match = re.search(r"[\w-]+\.(wav|mp3|m4a|pdf|png|jpg|jpeg|mp4|mov|txt)\b", task_lower)
    if file_mention_match:
        missing_name = file_mention_match.group(0)
        return ResolvedInputs(
            inventory=inventory,
            selected=[],
            rationale=f"Task explicitly requested '{missing_name}', which does not exist in inputs directory.",
            missing_file_requested=missing_name,
        )

    # 2b. Ordinal match ("first", "second", "last")
    if "first" in task_lower or "1st" in task_lower:
        chosen = inventory[0]
        return ResolvedInputs(
            inventory=inventory,
            selected=[chosen.id],
            rationale=f"Selected first file '{chosen.filename}' (ID: {chosen.id}) by alphabetical order.",
        )
    if "second" in task_lower or "2nd" in task_lower:
        if len(inventory) >= 2:
            chosen = inventory[1]
            return ResolvedInputs(
                inventory=inventory,
                selected=[chosen.id],
                rationale=f"Selected second file '{chosen.filename}' (ID: {chosen.id}) by alphabetical order.",
            )
    if "last" in task_lower:
        chosen = inventory[-1]
        return ResolvedInputs(
            inventory=inventory,
            selected=[chosen.id],
            rationale=f"Selected last file '{chosen.filename}' (ID: {chosen.id}) by alphabetical order.",
        )

    # 2c. Plural match ("all", "these files", "every", "clips")
    plural_keywords = ["all", "every", "these", "each", "both", "multiple"]
    if any(re.search(rf"\b{kw}\b", task_lower) for kw in plural_keywords) or "clips" in task_lower:
        # If type specified in plural, filter by type
        if "audio" in task_lower or "clip" in task_lower or "recording" in task_lower:
            matched = [f for f in inventory if f.kind == "audio"]
        elif "pdf" in task_lower or "document" in task_lower:
            matched = [f for f in inventory if f.kind == "pdf"]
        elif "image" in task_lower or "photo" in task_lower:
            matched = [f for f in inventory if f.kind == "image"]
        else:
            matched = inventory

        if matched:
            selected_ids = [f.id for f in matched]
            return ResolvedInputs(
                inventory=inventory,
                selected=selected_ids,
                rationale=f"Selected {len(selected_ids)} plural files matching query: {selected_ids}.",
            )

    # 2d. Singular match ("this file", "the audio", single file available)
    if len(inventory) == 1:
        chosen = inventory[0]
        return ResolvedInputs(
            inventory=inventory,
            selected=[chosen.id],
            rationale=f"Selected lone file in inventory: '{chosen.filename}' (ID: {chosen.id}).",
        )

    # 3. Ambiguity resolution: filter by type-consistency with task verb
    verb_type_mapping = {
        "audio": ["transcribe", "listen", "speech", "audio", "wav", "mp3", "subtitles", "srt"],
        "pdf": ["pdf", "scanned", "document", "pages"],
        "image": ["ocr", "image", "photo", "worksheet", "picture"],
    }
    candidate_type = None
    for kind, keywords in verb_type_mapping.items():
        if any(kw in task_lower for kw in keywords):
            candidate_type = kind
            break

    if candidate_type:
        typed_candidates = [f for f in inventory if f.kind == candidate_type]
        if len(typed_candidates) == 1:
            chosen = typed_candidates[0]
            return ResolvedInputs(
                inventory=inventory,
                selected=[chosen.id],
                rationale=f"Resolved ambiguity via task verb type consistency ({candidate_type}): '{chosen.filename}' (ID: {chosen.id}).",
            )
        if len(typed_candidates) > 1:
            # If multiple typed candidates and LLM tiebreaker provided, consult metadata only
            if llm_tiebreaker:
                meta_summary = [
                    {"id": f.id, "filename": f.filename, "kind": f.kind, "duration_s": f.duration_s, "pages": f.pages}
                    for f in typed_candidates
                ]
                decision_id = llm_tiebreaker(task, meta_summary)
                if any(f.id == decision_id for f in typed_candidates):
                    return ResolvedInputs(
                        inventory=inventory,
                        selected=[decision_id],
                        rationale=f"LLM tie-break selected ID {decision_id} from candidates based on task metadata.",
                    )
            # Default deterministic tie-break: pick the first matching type
            chosen = typed_candidates[0]
            return ResolvedInputs(
                inventory=inventory,
                selected=[chosen.id],
                rationale=f"Deterministic tie-break picked first type-consistent file: '{chosen.filename}' (ID: {chosen.id}).",
            )

    # Default fallback: pick first file
    chosen = inventory[0]
    return ResolvedInputs(
        inventory=inventory,
        selected=[chosen.id],
        rationale=f"Default selection picked first file '{chosen.filename}' (ID: {chosen.id}).",
    )
