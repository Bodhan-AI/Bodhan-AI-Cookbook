from .server import create_server


def main() -> None:
    server = create_server()
    server.run()  # FastMCP stdio transport


if __name__ == "__main__":
    main()
