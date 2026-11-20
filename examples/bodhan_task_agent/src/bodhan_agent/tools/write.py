from __future__ import annotations

from bodhan_agent import guards
from bodhan_agent.schemas.tools import LocalResult, WriteInput
from bodhan_agent.tools.base import LocalTool, LocalToolError, ToolContext, output_file, register, write_new
from bodhan_mcp.utils.script import dominant_script

WRITABLE_SUFFIXES: tuple[str, ...] = (".md", ".txt")
DEFAULT_NAME = "note.md"


class WriteTool(LocalTool):
    name = "write"
    description = (
        "Save English text you wrote, such as a summary or notes, as a .md or .txt file in the run's work folder. "
        "Use it to prepare English text for translate_text or to deliver an English file. Never write "
        "Indian-language or romanized Indian-language text: write English and translate it with translate_text. "
        "Existing files are never replaced; choose a new name each time."
    )
    Input = WriteInput

    async def run(self, params: WriteInput, ctx: ToolContext) -> LocalResult:
        problem = guards.llm_text_problem(params.content)
        if problem is not None:
            code, message = problem
            raise LocalToolError(code, f"content {message}")
        path = output_file(ctx, params.path, DEFAULT_NAME, WRITABLE_SUFFIXES)
        write_new(path, params.content.encode("utf-8"))
        artifact = register(
            ctx,
            path,
            "markdown" if path.suffix.lower() == ".md" else "text",
            "llm_english",
            language="en",
            language_source="requested",
            script=dominant_script(params.content),
            chars=len(params.content),
        )
        ctx.ledger.set_status(artifact.id, "ok")
        return LocalResult(
            data={"artifact": artifact.id, "path": artifact.path, "chars": artifact.chars, "language": "en"},
            artifact_ids=[artifact.id],
        )
