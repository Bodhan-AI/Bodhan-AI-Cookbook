
import json


def format_tool_result(tool_name: str, tool_result) -> dict:
    """
    Convert an MCP response into structured data.

    Preserve the complete Bodhan response, including text,
    layout blocks, usage and file information.
    """

    if tool_result.isError:
        raise RuntimeError(
            f"MCP tool failed: {tool_name}"
        )

    # Prefer structured content when MCP provides it.
    data = tool_result.structuredContent

    # Otherwise, decode the JSON returned in text content.
    if data is None:
        text_parts = []

        for item in tool_result.content:
            if hasattr(item, "text") and item.text:
                text_parts.append(item.text)

        combined_text = "\n".join(text_parts)

        try:
            data = json.loads(combined_text)
        except json.JSONDecodeError:
            data = {
                "text": combined_text
            }

    if not isinstance(data, dict):
        data = {
            "data": data
        }

    return {
        "tool": tool_name,
        "data": data,
        "text": data.get(
        "text",
        data.get("translation", "")
        ),
        "usage": data.get("usage"),
        "file": data.get("file"),
        "model": data.get("model"),
    }
