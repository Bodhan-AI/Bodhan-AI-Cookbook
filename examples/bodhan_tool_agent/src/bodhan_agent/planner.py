"""Agent task planner, schema validation, and deterministic fallback (SPEC §8.4)."""

import json
import re
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bodhan_agent.resolver import FileRef
from bodhan_mcp import config
from bodhan_mcp.utils.lang import contains_indic_unicode

REGISTERED_TOOLS = {
    "transcribe_audio",
    "translate_text",
    "synthesize_speech",
    "ocr_page",
    "ocr_document",
    "inspect_media",
    "chunk_audio",
    "get_voice_library",
}


class Step(BaseModel):
    """Single execution step in a task plan."""
    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    verify: str | None = None


class Plan(BaseModel):
    """Structured, validated execution plan."""
    model_config = ConfigDict(extra="forbid")

    steps: list[Step] = Field(default_factory=list)
    rationale: str = ""
    is_fallback: bool = False


def validate_plan(plan_data: dict[str, Any], valid_file_ids: set[str]) -> tuple[Plan | None, str | None]:
    """Validate plan dictionary against schema and strict agent constraints.

    Rejects:
      - Unknown tool names
      - Raw filesystem paths in args
      - Indic characters emitted by the LLM (Constraint C2)
      - Cycles in step dependencies
    """
    try:
        plan = Plan(**plan_data)
    except ValidationError as err:
        return None, f"Schema validation failed: {err}"

    step_ids = {s.id for s in plan.steps}

    # Constraint C2: Check for Indic Unicode in plan rationale and string args
    if contains_indic_unicode(plan.rationale):
        return None, "Constraint C2 violation: Planner emitted Indic characters in rationale."

    for s in plan.steps:
        # Check tool registration
        if s.tool not in REGISTERED_TOOLS:
            return None, f"Unknown tool requested: '{s.tool}'."

        # Check dependencies exist
        for dep in s.depends_on:
            if dep not in step_ids:
                return None, f"Step '{s.id}' depends on non-existent step '{dep}'."

        # Check for path emission or Indic characters in args
        for k, v in s.args.items():
            if isinstance(v, str):
                if contains_indic_unicode(v):
                    return None, f"Constraint C2 violation: Indic text emitted in arg '{k}'."
                if "/" in v or "\\" in v or v.startswith("~") or v.startswith("."):
                    return None, f"Constraint C4 violation: Raw filesystem path emitted in arg '{k}': '{v}'."

    # Check for dependency cycles
    if _has_cycle(plan.steps):
        return None, "Cyclic dependency detected in plan steps."

    return plan, None


def _has_cycle(steps: list[Step]) -> bool:
    """Detect cycles in dependency graph using DFS."""
    adj: dict[str, list[str]] = {s.id: list(s.depends_on) for s in steps}
    visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited

    def dfs(node: str) -> bool:
        visited[node] = 1
        for neighbor in adj.get(node, []):
            if visited.get(neighbor, 0) == 1:
                return True
            if visited.get(neighbor, 0) == 0:
                if dfs(neighbor):
                    return True
        visited[node] = 2
        return False

    for s in steps:
        if visited.get(s.id, 0) == 0:
            if dfs(s.id):
                return True
    return False


LANG_NAME_TO_CODE = {
    "tamil": "ta",
    "hindi": "hi",
    "telugu": "te",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "punjabi": "pa",
    "odia": "or",
    "assamese": "as",
    "urdu": "ur",
    "english": "en",
}


def _detect_target_lang(task_lower: str, default: str = "hi") -> str:
    """Detect target language from task description."""
    for name, code in LANG_NAME_TO_CODE.items():
        if f"to {name}" in task_lower or f"into {name}" in task_lower or f"→ {name}" in task_lower:
            return code
    for name, code in LANG_NAME_TO_CODE.items():
        if name in task_lower:
            return code
    for code in ["ta", "hi", "te", "bn", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur", "en"]:
        if f"to {code}" in task_lower or f"into {code}" in task_lower:
            return code
    return default


def _detect_voice_id(task_lower: str, lang: str) -> str:
    """Determine appropriate voice ID based on language and gender hints."""
    gender = "female" if "female" in task_lower else ("male" if "male" in task_lower else "female")
    tts_lang = lang if lang in config.SUPPORTED_LANGS["speak"] else "hi"
    return f"{tts_lang}_{gender}_1"


def create_fallback_plan(task: str, selected_files: list[FileRef]) -> Plan:
    """Deterministic fallback planner for standard task shapes when LLM fails or is unavailable."""
    task_lower = task.lower()
    steps: list[Step] = []
    step_num = 1
    tgt_lang = _detect_target_lang(task_lower, default="hi")
    voice_id = _detect_voice_id(task_lower, tgt_lang)

    for f in selected_files:
        if f.kind == "audio":
            # Transcription step
            step_id = f"s{step_num}"
            steps.append(
                Step(
                    id=step_id,
                    tool="transcribe_audio",
                    args={"file_id": f.id},
                    depends_on=[],
                    verify="audio_back_transcribe" if "verify" in task_lower else None,
                )
            )
            prev_id = step_id
            step_num += 1

            # Translation step if requested
            if (
                "translate" in task_lower
                or "english" in task_lower
                or "srt" in task_lower
                or any(name in task_lower for name in LANG_NAME_TO_CODE if name != "hindi")
            ):
                trans_id = f"s{step_num}"
                steps.append(
                    Step(
                        id=trans_id,
                        tool="translate_text",
                        args={"from_step": prev_id, "source_lang": "hi", "target_lang": tgt_lang},
                        depends_on=[prev_id],
                        verify="translation_coverage",
                    )
                )
                prev_id = trans_id
                step_num += 1

            # Speech synthesis if requested
            if "speak" in task_lower or "aloud" in task_lower or "audio" in task_lower or "voice" in task_lower:
                speak_id = f"s{step_num}"
                tts_lang = tgt_lang if tgt_lang in config.SUPPORTED_LANGS["speak"] else "hi"
                steps.append(
                    Step(
                        id=speak_id,
                        tool="synthesize_speech",
                        args={"from_step": prev_id, "language": tts_lang, "voice_id": voice_id},
                        depends_on=[prev_id],
                        verify="audio_back_transcribe",
                    )
                )
                step_num += 1

        elif f.kind in ("pdf", "image"):
            tool_name = "ocr_document" if f.kind == "pdf" else "ocr_page"
            ocr_id = f"s{step_num}"
            steps.append(
                Step(
                    id=ocr_id,
                    tool=tool_name,
                    args={"file_id": f.id},
                    depends_on=[],
                )
            )
            prev_id = ocr_id
            step_num += 1

            # Translation if target language or translation mentioned
            needs_translation = (
                "translate" in task_lower
                or "summary" in task_lower
                or any(name in task_lower for name in ["tamil", "marathi", "telugu", "bengali", "hindi", "gujarati"])
            )
            if needs_translation:
                trans_id = f"s{step_num}"
                steps.append(
                    Step(
                        id=trans_id,
                        tool="translate_text",
                        args={"from_step": prev_id, "source_lang": "en", "target_lang": tgt_lang},
                        depends_on=[prev_id],
                        verify="translation_coverage",
                    )
                )
                prev_id = trans_id
                step_num += 1

            # Speech synthesis if audio summary or speaking requested
            if "audio" in task_lower or "summary" in task_lower or "speak" in task_lower or "read aloud" in task_lower:
                speak_id = f"s{step_num}"
                tts_lang = tgt_lang if tgt_lang in config.SUPPORTED_LANGS["speak"] else "hi"
                steps.append(
                    Step(
                        id=speak_id,
                        tool="synthesize_speech",
                        args={"from_step": prev_id, "language": tts_lang, "voice_id": voice_id},
                        depends_on=[prev_id],
                        verify="audio_back_transcribe",
                    )
                )
                step_num += 1

        elif f.kind == "text":
            # Text file input
            prev_id = f"text_{f.id}"
            if "translate" in task_lower:
                trans_id = f"s{step_num}"
                steps.append(
                    Step(
                        id=trans_id,
                        tool="translate_text",
                        args={"file_id": f.id, "source_lang": "en", "target_lang": tgt_lang},
                        depends_on=[],
                        verify="translation_coverage",
                    )
                )
                prev_id = trans_id
                step_num += 1

            if "speak" in task_lower or "aloud" in task_lower or "audio" in task_lower or "voice" in task_lower:
                speak_id = f"s{step_num}"
                tts_lang = tgt_lang if tgt_lang in config.SUPPORTED_LANGS["speak"] else "hi"
                args = {"language": tts_lang, "voice_id": voice_id}
                if prev_id != f"text_{f.id}":
                    args["from_step"] = prev_id
                    deps = [prev_id]
                else:
                    args["file_id"] = f.id
                    deps = []
                steps.append(
                    Step(
                        id=speak_id,
                        tool="synthesize_speech",
                        args=args,
                        depends_on=deps,
                        verify="audio_back_transcribe",
                    )
                )
                step_num += 1

    return Plan(
        steps=steps,
        rationale=f"Deterministic fallback plan generated for task: '{task}'.",
        is_fallback=True,
    )


class GroqPlannerClient:
    """OpenAI-compatible LLM client for Groq with automatic secondary key failover on 429."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        secondary_api_key: str | None = None,
        model: str | None = None,
    ):
        import sys
        self.base_url = base_url or config.LLM_BASE_URL or "https://api.groq.com/openai/v1"
        self.primary_key = api_key or config.LLM_API_KEY
        self.secondary_key = secondary_api_key or getattr(config, "LLM_API_KEY_SECONDARY", "")
        self.current_key = self.primary_key
        configured_model = model or config.LLM_MODEL or "openai/gpt-oss-120b"
        self.model = "openai/gpt-oss-120b" if "gpt-oss" in configured_model else configured_model
        self._init_client()

    def _init_client(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.current_key)

    async def acomplete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Execute chat completion with automatic failover to secondary key on 429."""
        import sys
        from bodhan_agent.prompts import SYSTEM_PROMPT
        sys_p = system_prompt or SYSTEM_PROMPT

        for attempt in range(2):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": sys_p},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                err_str = str(exc)
                if "429" in err_str and self.secondary_key and self.current_key != self.secondary_key:
                    sys.stderr.write("[WARN] Groq rate limit (429) on primary key. Rotating to LLM_API_KEY_SECONDARY.\n")
                    self.current_key = self.secondary_key
                    self._init_client()
                    continue
                raise exc
        return ""


async def plan(
    task: str,
    inventory: list[FileRef],
    selected_files: list[FileRef],
    llm_client: Any = None,
) -> Plan:
    """Generate and validate plan from LLM, or invoke deterministic fallback."""
    import re
    valid_ids = {f.id for f in inventory}
    client = llm_client
    if not client and config.LLM_API_KEY and "pytest" not in sys.modules:
        try:
            client = GroqPlannerClient()
        except Exception:
            client = None

    if not client:
        return create_fallback_plan(task, selected_files)

    # Prompt LLM with inventory metadata only
    inventory_summary = [
        {"id": f.id, "kind": f.kind, "duration_s": f.duration_s, "pages": f.pages}
        for f in selected_files
    ]
    prompt_text = (
        f"Task: {task}\n"
        f"Selected File IDs: {[f.id for f in selected_files]}\n"
        f"File Metadata: {inventory_summary}\n"
        "Return plan JSON:"
    )

    try:
        raw_resp = await client.acomplete(prompt_text)
        match = re.search(r"(\{.*\})", raw_resp, re.DOTALL)
        clean_text = match.group(1).strip() if match else raw_resp.strip()
        plan_dict = json.loads(clean_text)
        valid_p, err = validate_plan(plan_dict, valid_ids)
        if valid_p:
            return valid_p
    except Exception:
        pass

    # Second attempt or fallback
    return create_fallback_plan(task, selected_files)
