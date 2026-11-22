"""Entry point: run the MCP server over stdio."""
import sys
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from .server import mcp  # noqa: E402


def main() -> None:
    print(
        "Bodhan MCP server running on stdio (listening for MCP client requests)...",
        file=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
