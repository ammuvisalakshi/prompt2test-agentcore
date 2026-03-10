"""
mcp_client.py — Prompt2TestAgentCore edition

Browser client used by /api/replay in server.py.
Supports two modes (auto-selected by env var):

  Local mode  (default): spawns npx @playwright/mcp --headless via stdio
  Cloud mode (AgentCore): connects to AGENTCORE_BROWSER_ENDPOINT over HTTPS SSE
"""

import os
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from dotenv import load_dotenv

load_dotenv()


class AgentCoreBrowserClient:
    """
    Replay browser client — two modes auto-selected by env var:

    AgentCore Browser (cloud):  set AGENTCORE_BROWSER_ENDPOINT in .env
    Local @playwright/mcp:      leave AGENTCORE_BROWSER_ENDPOINT unset
    """

    def __init__(self):
        self.session = None
        self._stack  = AsyncExitStack()
        self.tools   = []

    async def connect(self):
        endpoint = os.getenv("AGENTCORE_BROWSER_ENDPOINT")

        if endpoint:
            print("[replay] Using AgentCore Browser (cloud)")
            read, write = await self._stack.enter_async_context(sse_client(endpoint))
        else:
            print("[replay] Using local @playwright/mcp (headless subprocess)")
            params = StdioServerParameters(
                command="npx",
                args=["@playwright/mcp", "--headless"],
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))

        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()

        response   = await self.session.list_tools()
        self.tools = response.tools

        print(f"[OK] Browser connected — {len(self.tools)} tools available")
        return self

    async def call_tool(self, name: str, input: dict) -> str:
        if not self.session:
            raise RuntimeError("BrowserClient not connected. Call connect() first.")

        result = await self.session.call_tool(name, input)
        parts  = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif hasattr(block, "type") and block.type == "image":
                parts.append("[Screenshot captured]")
            else:
                parts.append(str(block))
        return "\n".join(parts) or "Tool executed (no output)"

    async def close(self):
        await self._stack.aclose()
        print("[disconnected] Browser disconnected")
