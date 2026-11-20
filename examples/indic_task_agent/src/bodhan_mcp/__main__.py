"""Run the MCP server on stdio: ``python -m bodhan_mcp``."""

from dotenv import find_dotenv, load_dotenv

from .server import build_server, context_from_env


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    build_server(context_from_env()).run(transport="stdio")


if __name__ == "__main__":
    main()
