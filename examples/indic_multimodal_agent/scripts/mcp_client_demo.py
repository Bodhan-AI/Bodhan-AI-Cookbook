"""Demonstration script using the official MCP Python SDK client.
Connects to the standalone Bodhan MCP server over stdio,
lists tools, and executes tool calls with readable output.
"""

import asyncio
import json
import os
import sys
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession


async def main():
    print("=" * 72, flush=True)
    print("      OFFICIAL MCP PYTHON SDK CLIENT - STANDALONE SERVER DEMO", flush=True)
    print("=" * 72, flush=True)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(repo_root, "src")

    env = dict(os.environ)
    env["PYTHONPATH"] = src_dir
    env["PYTHONUNBUFFERED"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", "-m", "bodhan_mcp"],
        env=env,
        cwd=repo_root,
    )

    print(f"\n[1/4] Spawning server process: {sys.executable} -u -m bodhan_mcp", flush=True)
    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            print("[2/4] Initializing protocol session with server...", flush=True)
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                print(f"      Protocol Version: {init_result.protocol_version}", flush=True)
                print(f"      Server Name:      {init_result.server_info.name}", flush=True)
                print(f"      Server Version:   {init_result.server_info.version}", flush=True)

                print("\n[3/4] Calling 'tools/list'...", flush=True)
                tools_response = await session.list_tools()
                tools = tools_response.tools
                print(f"      Registered MCP tools ({len(tools)} total):", flush=True)
                for idx, tool in enumerate(tools, 1):
                    desc_first_line = tool.description.split("\n")[0] if tool.description else ""
                    print(f"        {idx}. {tool.name:<26} : {desc_first_line[:48]}...", flush=True)

                print("\n[4/4] Executing tool calls over stdio ('tools/call'):", flush=True)

                # Call 1: bodhan_list_voices
                print("\n  --- Tool Call 1: bodhan_list_voices ---", flush=True)
                voices_call = await session.call_tool("bodhan_list_voices", {})
                for content in voices_call.content:
                    if hasattr(content, "text"):
                        voices_data = json.loads(content.text)
                        print(f"      Returned {len(voices_data)} voices across Indic languages:", flush=True)
                        for v in voices_data[:7]:
                            print(f"        - {v.get('name', 'N/A'):<12} | Lang: {v.get('language', 'N/A'):<4} | Gender: {v.get('gender', 'N/A')}", flush=True)

                # Call 2: bodhan_detect_language
                sample_text = "नमस्कार मित्रांनो"
                print(f"\n  --- Tool Call 2: bodhan_detect_language(text='{sample_text}') ---", flush=True)
                detect_call = await session.call_tool("bodhan_detect_language", {"text": sample_text})
                for content in detect_call.content:
                    if hasattr(content, "text"):
                        det_data = json.loads(content.text)
                        print(f"      Result: language={det_data.get('language')}, "
                              f"script={det_data.get('script')}, "
                              f"confidence={det_data.get('confidence')}, "
                              f"is_assumed={det_data.get('is_assumed')}", flush=True)

                # Call 3: Malformed call test (verifying error handling)
                print("\n  --- Tool Call 3 (Negative test): Non-existent tool call ---", flush=True)
                err_call = await session.call_tool("non_existent_tool", {})
                if err_call.is_error or (err_call.content and "failed" in getattr(err_call.content[0], "text", "")):
                    msg = err_call.content[0].text if err_call.content else "Tool error returned"
                    print(f"      Expected error handled cleanly by server: {msg}", flush=True)
                else:
                    print("      Call completed.", flush=True)

        print("\n" + "=" * 72, flush=True)
        print("      MCP CLIENT DEMONSTRATION COMPLETE - ALL CALLS SUCCEEDED", flush=True)
        print("=" * 72, flush=True)
    except Exception as exc:
        print(f"\n[ERROR] MCP Client demonstration failed: {type(exc).__name__}: {exc}", flush=True)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
