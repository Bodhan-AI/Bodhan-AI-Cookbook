import asyncio

from bodhan_agent.mcp_client import connect_to_mcp_server


async def main():
    print("Connecting to Bodhan MCP Server...")

    async with connect_to_mcp_server() as session:
        print("\nConnection successful!")
        print("Fetching available tools...\n")

        response = await session.list_tools()

        for tool in response.tools:
            print(f"Tool: {tool.name}")
            print(f"Description: {tool.description}")
            print("-" * 50)

        print(f"\nTotal tools discovered: {len(response.tools)}")


if __name__ == "__main__":
    asyncio.run(main())