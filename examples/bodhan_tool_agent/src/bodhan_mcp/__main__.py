from dotenv import load_dotenv

from .server import create_server


def main() -> None:
    load_dotenv()
    server = create_server()
    server.run()  # FastMCP stdio transport


if __name__ == "__main__":
    main()
