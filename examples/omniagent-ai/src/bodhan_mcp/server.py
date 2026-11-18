
from mcp.server.fastmcp import FastMCP

from .translation_tools import translate_text
from .audio_tools import transcribe_audio
from .tts_tools import generate_speech
from .ocr_tools import extract_text


mcp = FastMCP("OmniAgentAI")


@mcp.tool()
def server_status() -> dict:
    """Check whether the OmniAgentAI MCP server is running."""
    return {
        "status": "running",
        "server": "OmniAgentAI",
        "message": "MCP server initialized successfully"
    }


# Register Bodhan model tools
mcp.tool()(translate_text)
mcp.tool()(transcribe_audio)
mcp.tool()(generate_speech)
mcp.tool()(extract_text)


def main():
    mcp.run(transport="stdio")


