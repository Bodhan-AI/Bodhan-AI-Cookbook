"""Text translation tool with 32K context pagination and Markdown structure preservation."""


from bodhan_mcp import config, errors
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import TranslateOut


def estimate_tokens(text: str) -> int:
    """Rough heuristic estimate of token count (1 token ~= 3 characters for Indic/English)."""
    return max(1, len(text) // 3)


def is_table_line(line: str) -> bool:
    """Check if a line belongs to a Markdown table."""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def split_on_structural_boundaries(
    text: str,
    max_tokens: int = config.TRANSLATE_CONTEXT_TOK,
) -> list[str]:
    """Split text into structural chunks <= max_tokens.

    Never splits inside Markdown tables or math blocks ($$...$$).
    Splits preferentially at headings (#), blank lines, or paragraph boundaries.
    """
    total_tokens = estimate_tokens(text)
    if total_tokens <= max_tokens:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current_chunk_lines: list[str] = []
    current_chunk_tokens = 0
    in_table = False
    in_math_block = False

    for line in lines:
        stripped = line.strip()
        line_tokens = estimate_tokens(line)

        # Track table boundaries
        if is_table_line(line):
            in_table = True
        elif in_table and stripped == "":
            in_table = False

        # Track math block boundaries
        if stripped.startswith("$$"):
            in_math_block = not in_math_block

        # If adding this line would exceed max_tokens AND we are not inside a table or math block:
        if (
            current_chunk_tokens + line_tokens > max_tokens
            and current_chunk_lines
            and not in_table
            and not in_math_block
        ):
            chunks.append("".join(current_chunk_lines))
            current_chunk_lines = [line]
            current_chunk_tokens = line_tokens
        else:
            current_chunk_lines.append(line)
            current_chunk_tokens += line_tokens

    if current_chunk_lines:
        chunks.append("".join(current_chunk_lines))

    return chunks


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    max_chunk_tokens: int = config.TRANSLATE_CONTEXT_TOK,
    client: BodhanClient | None = None,
) -> TranslateOut | errors.ToolErrorPayload:
    """Translate text preserving Markdown structure and absorbing 32K context pagination."""
    if not source_lang or not target_lang:
        return errors.make_tool_error(
            code=errors.INVALID_ARGUMENT,
            message="Both source_lang and target_lang must be specified.",
            hint="Provide valid language codes.",
        )

    lang_name_map = {
        "hindi": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn",
        "marathi": "mr", "gujarati": "gu", "kannada": "kn", "malayalam": "ml",
        "punjabi": "pa", "odia": "or", "assamese": "as", "urdu": "ur", "english": "en"
    }
    src = lang_name_map.get(source_lang.lower().strip(), source_lang.lower().strip())
    tgt = lang_name_map.get(target_lang.lower().strip(), target_lang.lower().strip())

    if src not in config.SUPPORTED_LANGS["translate"]:
        return errors.make_tool_error(
            code=errors.UNSUPPORTED_LANGUAGE,
            message=f"Unsupported source language: '{source_lang}'.",
            hint=f"Supported languages: {', '.join(config.SUPPORTED_LANGS['translate'])}",
            details={"source_lang": source_lang},
        )
    if tgt not in config.SUPPORTED_LANGS["translate"]:
        return errors.make_tool_error(
            code=errors.UNSUPPORTED_LANGUAGE,
            message=f"Unsupported target language: '{target_lang}'.",
            hint=f"Supported languages: {', '.join(config.SUPPORTED_LANGS['translate'])}",
            details={"target_lang": target_lang},
        )

    # Absorb limit: split on structural boundaries if exceeding context
    chunks = split_on_structural_boundaries(text, max_tokens=max_chunk_tokens)
    bodhan = client or BodhanClient()

    translated_parts: list[str] = []
    total_cost = 0.0

    try:
        for chunk in chunks:
            res = await bodhan.translate(text=chunk, source_lang=src, target_lang=tgt)
            raw_data = res.get("data", {})
            part_trans = raw_data.get("translated") or raw_data.get("text", "")
            if not part_trans and "choices" in raw_data:
                choices = raw_data["choices"]
                if choices and "message" in choices[0]:
                    part_trans = choices[0]["message"].get("content", "")

            translated_parts.append(part_trans)
            total_cost += float(res.get("cost_inr", 0.0))

        full_translation = "".join(translated_parts)
        return TranslateOut(
            translated=full_translation,
            cost_inr=round(total_cost, 4),
        )
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.INTERNAL,
            message=f"Translation failed: {exc}",
            hint="Check upstream API status and network connection.",
            details={"error": str(exc)},
        )
