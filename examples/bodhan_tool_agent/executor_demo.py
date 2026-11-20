
import asyncio

from bodhan_agent.planner import create_plan
from bodhan_agent.executor import execute_plan
from bodhan_agent.mcp_client import connect_to_mcp_server


async def main():

    task = (
        "Extract the Bengali text from this image "
        "and translate it into English."
    )

    files = ["sample_data/test_document.png"]

    # Step 1: Ask the LLM to create a plan.
    plan = create_plan(task, files)

    print("\nGenerated Plan:")
    print(plan)

    # Step 2: Connect to the MCP server.
    async with connect_to_mcp_server() as session:

        # Step 3: Execute the plan.
        results = await execute_plan(
            session=session,
            plan=plan,
            files=files,
            task=task,
        )

    print("\nFinal Tool Results:")

    for result in results:
        print("\nTool:", result["tool"])
        print("Result:", result["result"]["text"])


if __name__ == "__main__":
    asyncio.run(main())
