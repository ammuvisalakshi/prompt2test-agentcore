"""
agent_loop.py — Prompt2TestAgentCore edition

Changes from original Prompt2TestQAAgent:
  - Removed Windows site-packages path injection
  - APP_USERNAME / APP_PASSWORD removed from system prompt
    → credentials are injected by AgentCore Identity at the tool call level
  - _playwright_params() replaced by _browser_mcp_client()
    → connects to AgentCore Browser over HTTPS (no local Chromium needed)
  - REST tools removed for now — browser-only mode
    → add REST tools later via AgentCore Gateway (Path 2) when needed
  - _make_callback() simplified — deep token parsing replaced by OTEL tracing
    → AgentCore Observability captures per-call token usage via OTEL automatically
    → on_llm_call still fires for live SSE token streaming to the UI
  - Added OpenTelemetry tracing per agent phase and tool call
"""

import os
import re
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands.agent.conversation_manager import SlidingWindowConversationManager
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters

from opentelemetry import trace

load_dotenv()

tracer = trace.get_tracer("prompt2test.agent")

MAX_STEPS = 60

# ── System prompt ─────────────────────────────────────────────────────────────
# Credentials are NO LONGER injected here.
# AgentCore Identity intercepts browser_navigate calls and injects auth tokens
# automatically via the configured OAuth / SSO provider.
BASE_SYSTEM_PROMPT = """
You are an expert QA automation engineer executing test cases.

## Tool selection — follow this strictly:

### Use REST tools when:
- The instruction mentions "API", "endpoint", "invoke", "call", "request", "GET", "POST", "PUT", "PATCH", "DELETE"
- The URL path contains /api/, /v1/, /v2/, /graphql, or similar API patterns
- The task is to verify a JSON response value, status code, or response body
- No browser interaction (clicking, typing, navigation) is needed

### Use browser tools when:
- The instruction involves UI actions: click, type, fill form, navigate, screenshot
- The task requires seeing or interacting with a web page visually

## Rules:
- NEVER open a browser to test an API endpoint — use REST tools instead
- Take ONE browser_snapshot at the start of each phase to orient yourself
- Use exact element ref (e.g. ref=e17) or element text from the snapshot — never guess selectors
- Re-snapshot only when the page has likely changed: after navigation, form submission, modal open/close, or a failed action
- Do NOT snapshot after simple inputs (browser_type, browser_select, browser_fill_form) — trust the tool result
- If something fails, take a snapshot to diagnose, then try once with an alternative approach
- Be methodical — execute every step, do not skip any
- Always end with: CHUNK X/Y: PASS or FAIL
""".strip()

# ── ReAct task system prompt ───────────────────────────────────────────────────
# Used by run_task() — single-pass continuous agent loop, no phases.
TASK_SYSTEM_PROMPT = """
You are a browser automation agent. Your job is to complete the given task using browser tools.

## Rules:
- Take ONE browser_snapshot at the start to orient yourself on the current page state
- Use exact element ref (e.g. ref=e17) from the snapshot — never guess selectors
- Re-snapshot only when the page has likely changed: after navigation, form submit, modal open/close, or a failed action
- Do NOT snapshot after simple inputs (browser_type, browser_fill, browser_select) — trust the tool result
- If an action fails, re-snapshot to diagnose, then try once with an alternative approach
- Be methodical — complete every part of the task before stopping
- When the task is fully complete, end your final message with exactly:
  TASK: COMPLETE – <one sentence describing what was accomplished>
- If the task cannot be completed, end with:
  TASK: FAILED – <reason why it could not be completed>
""".strip()


# ── Tool hint detection ────────────────────────────────────────────────────────
_API_KEYWORDS = frozenset([
    "/api/", "http_get", "http_post", "http_put", "http_patch", "http_delete",
    "endpoint", "status code", "json response", "get /", "post /", "put /",
    "patch /", "delete /", "request", "api call",
])
_BROWSER_KEYWORDS = frozenset([
    "navigate", "click", "type", "fill", "screenshot", "login", "button",
    "form", "dropdown", "select", "browser", "page", "url", "sidebar",
    "menu", "input", "field", "checkbox", "link", "search", "verify the",
])


def _detect_tool_hint(prompt: str) -> str:
    lower = prompt.lower()
    has_api     = any(kw in lower for kw in _API_KEYWORDS)
    has_browser = any(kw in lower for kw in _BROWSER_KEYWORDS)
    if has_api and has_browser:
        return "mixed"
    if has_api:
        return "api"
    return "browser"


def _parse_success(text: str) -> bool:
    m = re.search(r'CHUNK\s+\d+/\d+\s*:\s*(PASS|FAIL)', text, re.IGNORECASE)
    if m:
        return m.group(1).upper() == "PASS"
    has_pass = bool(re.search(r'\bPASS\b', text, re.IGNORECASE))
    has_fail = bool(re.search(r'\bFAIL\b', text, re.IGNORECASE))
    return has_pass and not has_fail


def _parse_task_success(text: str) -> bool:
    """Parse TASK: COMPLETE / TASK: FAILED from run_task() output."""
    m = re.search(r'TASK\s*:\s*(COMPLETE|FAILED)', text, re.IGNORECASE)
    if m:
        return m.group(1).upper() == "COMPLETE"
    return _parse_success(text)


# ── Browser MCP client factory ────────────────────────────────────────────────
def _browser_mcp_client() -> MCPClient:
    """
    Two modes — selected automatically based on env vars:

    1. AgentCore Browser (cloud, no local install needed):
       Set AGENTCORE_BROWSER_ENDPOINT in .env
       → connects over HTTPS SSE to managed Chromium

    2. Local @playwright/mcp (no AWS needed):
       Leave AGENTCORE_BROWSER_ENDPOINT unset + install Node.js
       → spawns `npx @playwright/mcp --headless` as a subprocess
    """
    endpoint = os.getenv("AGENTCORE_BROWSER_ENDPOINT")
    if endpoint:
        print("[browser] Using AgentCore Browser (cloud)")
        return MCPClient(lambda: sse_client(endpoint))

    print("[browser] Using local @playwright/mcp (headless subprocess)")
    params = StdioServerParameters(
        command="npx",
        args=["@playwright/mcp", "--headless"],
    )
    return MCPClient(lambda: stdio_client(params))


# ── Path 2 — REST tools via AgentCore Gateway (add when ready) ─────────────────
# When you want to add API testing for a specific app:
#   1. Export or write an OpenAPI spec for that app
#   2. Register it with AgentCore Gateway in the AWS Console
#   3. Set AGENTCORE_GATEWAY_ENDPOINT in .env
#   4. Uncomment and wire up:
#
# def _gateway_mcp_client() -> MCPClient:
#     endpoint = os.getenv("AGENTCORE_GATEWAY_ENDPOINT")
#     return MCPClient(lambda: sse_client(endpoint))


# ── Simplified callback ────────────────────────────────────────────────────────
def _make_callback(on_step=None, on_llm_call=None, steps_out=None, tokens_out=None):
    """
    Strands callback bridging agent events to SSE events and OTEL spans.

    AgentCore Observability automatically captures full token usage and traces
    via the OTEL exporter in the runtime — manual deep parsing is not needed.

    on_llm_call still fires for live token streaming to the UI (tokens SSE event).
    on_step fires for live tool-call streaming to the UI (action SSE event).
    """
    call_count = [0]
    total_in   = [0]
    total_out  = [0]

    def handler(**kwargs):
        # ── Token tracking for live UI SSE stream ─────────────────────────
        # Intercept Bedrock Converse metadata chunk (fires once per LLM call).
        # OTEL captures the same data for durable observability — this is only
        # for the real-time tokens SSE event consumed by the React frontend.
        event = kwargs.get("event")
        if isinstance(event, dict):
            usage   = event.get("metadata", {}).get("usage", {})
            in_tok  = usage.get("inputTokens",  0)
            out_tok = usage.get("outputTokens", 0)
            if in_tok > 0:
                call_count[0] += 1
                total_in[0]   += in_tok
                total_out[0]  += out_tok
                if tokens_out is not None:
                    tokens_out["total_in"]  = total_in[0]
                    tokens_out["total_out"] = total_out[0]
                if on_llm_call:
                    on_llm_call({
                        "call":      call_count[0],
                        "in_tok":    in_tok,
                        "out_tok":   out_tok,
                        "total_in":  total_in[0],
                        "total_out": total_out[0],
                    })

        # ── Tool call tracking for live UI SSE stream ──────────────────────
        # ModelMessageEvent fires once per complete model response with full
        # tool inputs — use this so action cards show real details (URL, text, etc.)
        message = kwargs.get("message")
        if message is not None and steps_out is not None:
            try:
                role    = message.get("role", "") if isinstance(message, dict) else getattr(message, "role", "")
                content = message.get("content", []) if isinstance(message, dict) else getattr(message, "content", [])
                if role == "assistant":
                    for block in (content or []):
                        tu = block.get("toolUse") if isinstance(block, dict) else None
                        if tu and isinstance(tu, dict):
                            tid  = tu.get("toolUseId", "")
                            inp  = tu.get("input", {})
                            name = tu.get("name", "")
                            # Avoid duplicates — only fire once per tool use ID
                            if not any(s.get("_tid") == tid for s in steps_out):
                                step = {"tool": name, "input": inp, "status": "ok", "output": "", "_tid": tid}
                                steps_out.append(step)
                                # Create OTEL span for this tool call
                                with tracer.start_as_current_span(f"tool.{name}") as span:
                                    span.set_attribute("tool.name", name)
                                    span.set_attribute("tool.input", str(inp)[:500])
                                if on_step:
                                    on_step({"tool": name, "input": inp, "status": "ok", "output": ""})
            except Exception:
                pass

    return handler


# ── Main runner ────────────────────────────────────────────────────────────────
class StrandsTestRunner:
    """
    Manages AgentCore MCP connections (Browser + Gateway) and runs each
    test phase through a fresh strands.Agent.

    Browser session state persists across phases because both MCP connections
    stay open for the duration of the test run.
    """

    def __init__(self):
        self._pw_client = None
        self._pw_tools  = []
        self._model     = None

    def connect(self):
        """
        Open AgentCore Browser connection and load tools.
        Synchronous — called via run_in_executor from the async server.
        """
        self._pw_client = _browser_mcp_client()
        self._pw_client.__enter__()
        self._pw_tools = self._pw_client.list_tools_sync()
        print(f"[OK] AgentCore Browser connected — {len(self._pw_tools)} browser tools")

        self._model = BedrockModel(
            model_id    = os.getenv("BEDROCK_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
            region_name = os.getenv("AWS_REGION", "us-east-1"),
            max_tokens  = 4096,
            temperature = 0.7,
            streaming   = True,
        )

    async def run_chunk(
        self,
        prompt: str,
        system_prompt: str = None,
        on_step=None,
        on_llm_call=None,
    ) -> dict:
        """
        Run one test phase through a fresh strands.Agent.
        Each phase gets a new Agent (fresh conversation history) but reuses
        the same MCP connections so browser session state is preserved.
        """
        hint = _detect_tool_hint(prompt)
        tools = self._pw_tools
        print(f"  [tools] {len(tools)} browser tools")

        steps_out  = []
        tokens_out = {"total_in": 0, "total_out": 0}

        with tracer.start_as_current_span("agent.phase") as span:
            span.set_attribute("phase.hint", hint)
            span.set_attribute("phase.tool_count", len(tools))

            agent = Agent(
                model                = self._model,
                tools                = tools,
                system_prompt        = system_prompt or BASE_SYSTEM_PROMPT,
                callback_handler     = _make_callback(
                    on_step    = on_step,
                    on_llm_call= on_llm_call,
                    steps_out  = steps_out,
                    tokens_out = tokens_out,
                ),
                conversation_manager = SlidingWindowConversationManager(window_size=20),
            )

            result_obj = await agent.invoke_async(prompt)
            answer     = str(result_obj)

            total_in  = tokens_out["total_in"]
            total_out = tokens_out["total_out"]
            span.set_attribute("tokens.input",  total_in)
            span.set_attribute("tokens.output", total_out)
            span.set_attribute("phase.steps",   len(steps_out))
            print(f"\n  [phase complete] total_in={total_in:,}  total_out={total_out:,}")

        clean_steps = [{k: v for k, v in s.items() if k != "_tid"} for s in steps_out]

        return {
            "success":       _parse_success(answer),
            "answer":        answer,
            "steps":         clean_steps,
            "step_count":    len(clean_steps),
            "input_tokens":  total_in,
            "output_tokens": total_out,
        }

    async def run_task(
        self,
        prompt: str,
        on_step=None,
        on_llm_call=None,
    ) -> dict:
        """
        Run a task as a single continuous ReAct loop (no phases, no chunking).
        One strands.Agent receives the full prompt and runs think→act→observe
        until it outputs TASK: COMPLETE or TASK: FAILED.
        """
        steps_out  = []
        tokens_out = {"total_in": 0, "total_out": 0}

        with tracer.start_as_current_span("agent.task") as span:
            span.set_attribute("task.prompt_len", len(prompt))
            span.set_attribute("task.tool_count", len(self._pw_tools))

            agent = Agent(
                model                = self._model,
                tools                = self._pw_tools,
                system_prompt        = TASK_SYSTEM_PROMPT,
                callback_handler     = _make_callback(
                    on_step    = on_step,
                    on_llm_call= on_llm_call,
                    steps_out  = steps_out,
                    tokens_out = tokens_out,
                ),
                conversation_manager = SlidingWindowConversationManager(window_size=30),
            )

            result_obj = await agent.invoke_async(prompt)
            answer     = str(result_obj)

            total_in  = tokens_out["total_in"]
            total_out = tokens_out["total_out"]
            span.set_attribute("tokens.input",  total_in)
            span.set_attribute("tokens.output", total_out)
            span.set_attribute("task.steps",    len(steps_out))
            print(f"\n  [task complete] steps={len(steps_out)}  total_in={total_in:,}  total_out={total_out:,}")

        clean_steps = [{k: v for k, v in s.items() if k != "_tid"} for s in steps_out]

        return {
            "success":       _parse_task_success(answer),
            "answer":        answer,
            "steps":         clean_steps,
            "step_count":    len(clean_steps),
            "input_tokens":  total_in,
            "output_tokens": total_out,
        }

    def close(self):
        """Disconnect from AgentCore Browser."""
        if self._pw_client:
            try:
                self._pw_client.__exit__(None, None, None)
                print("[disconnected] AgentCore Browser disconnected")
            except Exception as e:
                print(f"[WARN] AgentCore Browser close error: {e}")


def _summarise(d: dict, max_len: int = 80) -> str:
    s = ", ".join(f"{k}={str(v)[:30]}" for k, v in (d or {}).items())
    return s[:max_len]
