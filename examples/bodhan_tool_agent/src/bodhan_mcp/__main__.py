from .config import load_env


def main() -> None:
    load_env()
    from .server import mcp
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
