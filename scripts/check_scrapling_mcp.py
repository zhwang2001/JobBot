"""Verify the installed Scrapling server over the real MCP stdio transport."""

import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    executable = Path(__file__).resolve().parents[1] / ".venv/bin/scrapling-mcp"
    async with stdio_client(StdioServerParameters(command=str(executable))) as (read, write):
        async with ClientSession(read, write) as session:
            server = await session.initialize()
            listing = await session.list_tools()
            names = {tool.name for tool in listing.tools}
            assert {"make_request", "fetch", "stealthy_fetch", "open_session", "session_fetch"} <= names
            result = await session.call_tool("list_sessions", {})
            if result.is_error:
                raise RuntimeError(result)
            print(f"{server.server_info.name} {server.server_info.version}: {len(names)} tools; list_sessions succeeded")
            print(", ".join(sorted(names)))


if __name__ == "__main__":
    asyncio.run(main())
