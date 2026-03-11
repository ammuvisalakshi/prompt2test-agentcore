"""
server.py — Prompt2TestAgentCore backend

Changes from original:
  - Removed all Windows-specific code (UTF-8 hacks, site-packages injection)
  - CORS origins read from ALLOWED_ORIGINS environment variable
  - run_history backed by AgentCore Memory (via saved_tests module)
"""

import os
import json
import asyncio
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import uvicorn
import saved_tests as _st


app = FastAPI(title="Prompt2Test API")

# Origins read from env — set ALLOWED_ORIGINS="https://app.example.com,http://localhost:3001"
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:3000").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory run history (last 50 runs for the current session)
# Durable history lives in AgentCore Memory via saved_tests module
run_history:    list = []
_stop_flags:    dict = {}   # run_id -> True when stop requested
_pending_saves: dict = {}   # run_id -> save payload, awaiting user approval


class RunRequest(BaseModel):
    task:    str
    visible: bool = True


@app.post("/api/run")
async def run_test(request: Request, req: RunRequest):
    """
    Streams live browser actions using EventSourceResponse.
    Each event is flushed to the browser immediately when it happens.
    """
    run_id  = str(uuid.uuid4())[:8]
    started = datetime.now()

    queue: asyncio.Queue = asyncio.Queue()
    DONE = "__DONE__"

    def push(event_type: str, data: dict):
        queue.put_nowait({"type": event_type, "data": data})

    async def run_automation():
        try:
            from agent_loop import StrandsTestRunner

            runner = StrandsTestRunner()
            _loop  = asyncio.get_event_loop()
            await _loop.run_in_executor(None, runner.connect)

            def on_step(step: dict):
                if _stop_flags.get(run_id):
                    raise Exception("Task stopped by user.")
                push("action", {
                    "tool":    step.get("tool", ""),
                    "summary": _summarise(step.get("tool", ""), step.get("input", {})),
                    "status":  step.get("status", "ok"),
                })

            def on_llm_call(info: dict):
                push("tokens", info)

            async def _bg_close():
                try:
                    await _loop.run_in_executor(None, runner.close)
                except Exception as e:
                    print(f"[WARN] runner.close() error: {e}")

            try:
                result = await runner.run_task(
                    req.task,
                    on_step=on_step,
                    on_llm_call=on_llm_call,
                )
            finally:
                asyncio.create_task(_bg_close())

            all_steps     = result.get("steps", [])
            total_in_tok  = result.get("input_tokens", 0)
            total_out_tok = result.get("output_tokens", 0)
            passed        = result.get("success", False)
            duration_s    = round((datetime.now() - started).total_seconds(), 1)

            push("llm", {"text": result.get("answer", "")})

            run_history.insert(0, {
                "run_id":      run_id,
                "task":        req.task,
                "success":     passed,
                "total_steps": len(all_steps),
                "duration_s":  duration_s,
                "timestamp":   started.isoformat(),
            })
            if len(run_history) > 50:
                run_history.pop()

            if passed and all_steps:
                _pending_saves[run_id] = {
                    "id":            run_id,
                    "name":          _st.auto_name(req.task),
                    "task":          req.task,
                    "mode":          "browser",
                    "steps":         [{"tool": s["tool"], "input": s["input"]} for s in all_steps],
                    "saved_at":      started.isoformat(),
                    "total_steps":   len(all_steps),
                    "duration_s":    duration_s,
                    "input_tokens":  total_in_tok,
                    "output_tokens": total_out_tok,
                }

            push("done", {
                "run_id":         run_id,
                "success":        passed,
                "approvable":     passed and bool(all_steps),
                "answer":         result.get("answer", ""),
                "total_steps":    len(all_steps),
                "duration_s":     duration_s,
                "input_tokens":   total_in_tok,
                "output_tokens":  total_out_tok,
            })

        except Exception as e:
            push("error", {"message": str(e)})

        finally:
            _stop_flags.pop(run_id, None)
            queue.put_nowait(DONE)

    async def event_generator():
        yield {
            "data": json.dumps({"type": "start", "data": {
                "run_id":    run_id,
                "task":      req.task,
                "timestamp": started.isoformat(),
            }})
        }
        asyncio.create_task(run_automation())

        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                yield {"comment": "keepalive"}
                continue
            if item == DONE:
                break
            yield {"data": json.dumps(item)}

    return EventSourceResponse(event_generator())


@app.get("/api/history")
async def get_history():
    return {"runs": run_history}


@app.post("/api/stop/{run_id}")
async def stop_run(run_id: str):
    _stop_flags[run_id] = True
    return {"ok": True}


@app.post("/api/approve/{run_id}")
async def approve_run(run_id: str):
    data = _pending_saves.pop(run_id, None)
    if not data:
        return JSONResponse(status_code=404, content={"error": "Run not found or already processed"})
    _st.save(data)
    return {"ok": True}


@app.post("/api/deny/{run_id}")
async def deny_run(run_id: str):
    _pending_saves.pop(run_id, None)
    return {"ok": True}


@app.get("/api/saved")
async def get_saved():
    return {"tests": _st.load_all()}


@app.delete("/api/saved/{test_id}")
async def delete_saved(test_id: str):
    _st.delete(test_id)
    return {"ok": True}


class RenameRequest(BaseModel):
    name: str


@app.patch("/api/saved/{test_id}")
async def rename_saved(test_id: str, req: RenameRequest):
    _st.rename(test_id, req.name)
    return {"ok": True}


class ReplayRequest(BaseModel):
    test_id: str
    visible: bool = True


@app.post("/api/replay")
async def replay_test(request: Request, req: ReplayRequest):
    """
    Replays a saved test's steps directly via MCP — no LLM call.
    Streams action events identical to /api/run so the UI works unchanged.
    """
    saved = next((t for t in _st.load_all() if t["id"] == req.test_id), None)
    if not saved:
        return JSONResponse(status_code=404, content={"error": "Saved test not found"})

    run_id  = str(uuid.uuid4())[:8]
    started = datetime.now()
    queue:  asyncio.Queue = asyncio.Queue()
    DONE = "__DONE__"

    def push(event_type: str, data: dict):
        queue.put_nowait({"type": event_type, "data": data})

    async def run_replay():
        try:
            from mcp_client import AgentCoreBrowserClient

            mcp = AgentCoreBrowserClient()
            await mcp.connect()

            async def _bg_close_replay():
                try:
                    await mcp.close()
                except Exception as e:
                    print(f"[WARN] replay mcp.close() error: {e}")

            steps = saved.get("steps", [])
            try:
                for step in steps:
                    tool = step["tool"]
                    inp  = step["input"]
                    try:
                        await mcp.call_tool(tool, inp)
                        status = "ok"
                    except Exception as e:
                        status = "error"
                        print(f"  [replay ERR] {tool}: {e}")

                    push("action", {
                        "tool":    tool,
                        "summary": _summarise(tool, inp),
                        "status":  status,
                    })
            finally:
                asyncio.create_task(_bg_close_replay())

            duration_s = round((datetime.now() - started).total_seconds(), 1)
            push("done", {
                "success":       True,
                "answer":        "Replay completed — all steps executed from saved record.",
                "total_steps":   len(steps),
                "duration_s":    duration_s,
                "input_tokens":  0,
                "output_tokens": 0,
            })

        except Exception as e:
            push("error", {"message": str(e)})
        finally:
            queue.put_nowait(DONE)

    async def event_generator():
        yield {
            "data": json.dumps({"type": "start", "data": {
                "run_id":    run_id,
                "task":      f"[REPLAY] {saved['task']}",
                "timestamp": started.isoformat(),
            }})
        }
        asyncio.create_task(run_replay())

        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                yield {"comment": "keepalive"}
                continue
            if item == DONE:
                break
            yield {"data": json.dumps(item)}

    return EventSourceResponse(event_generator())


@app.get("/health")
@app.get("/api/health")
async def health():
    """Health check endpoint - AgentCore requires this"""
    return {"status": "ok"}


@app.post("/")
@app.post("/invoke")
async def invoke_default(request: Request):
    """
    Default AgentCore invocation endpoint.
    Simple test version to debug 502 errors.
    """
    print("[INVOKE] Endpoint called!")
    try:
        body = await request.json()
        print(f"[INVOKE] Received payload: {body}")
        return {"status": "ok", "received": body}
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def _summarise(tool: str, inp: dict) -> str:
    if tool == "browser_snapshot":
        return "Read page structure"
    if tool == "browser_screenshot":
        return "Take screenshot"
    if not inp:
        return tool
    if tool == "browser_navigate":
        return f"-> {inp.get('url', '')}"[:80]
    if tool == "browser_click":
        label = inp.get("element") or inp.get("ref") or inp.get("selector") or inp.get("text", "")
        return f"Click: {label}"[:80]
    if tool in ("browser_type", "browser_fill"):
        text  = inp.get("text") or inp.get("value", "")
        label = inp.get("element") or inp.get("ref", "")
        if label:
            return f"Type into {label}: \"{str(text)[:30]}\""[:80]
        return f"Type: \"{str(text)[:40]}\""
    if tool == "browser_fill_form":
        fields = inp.get("fields", [])
        if fields:
            parts = [
                f"{f.get('name', f.get('element', f.get('ref', '-')))}={str(f.get('value', ''))[:12]}"
                for f in fields
            ]
            return f"Fill form: {', '.join(parts)}"[:80]
        return "Fill form"
    if tool in ("browser_select_option", "browser_select"):
        vals    = inp.get("values") or []
        val_str = vals[0] if vals else inp.get("value", "")
        label   = inp.get("element") or inp.get("ref", "")
        if label:
            return f"Select \"{val_str}\" in {label}"[:80]
        return f"Select \"{val_str}\""[:80]
    if tool in ("browser_wait_for", "browser_wait_for_text", "browser_wait_for_visible"):
        text = inp.get("text") or inp.get("element") or inp.get("selector", "")
        return f"Wait: {text}"[:80]
    if tool in ("browser_check", "browser_uncheck"):
        label  = inp.get("element") or inp.get("ref", "")
        action = "Check" if tool == "browser_check" else "Uncheck"
        return f"{action}: {label}"[:80]
    if tool == "browser_hover":
        return f"Hover: {inp.get('element') or inp.get('ref', '')}"[:80]
    if tool == "browser_press_key":
        return f"Press: {inp.get('key', '')}"
    if tool == "browser_scroll":
        return f"Scroll: {inp.get('direction') or inp.get('coordinate', '')}"[:80]
    if tool in ("http_get", "http_delete"):
        return f"{tool.split('_')[1].upper()} {inp.get('url', '')}"[:80]
    if tool in ("http_post", "http_put", "http_patch"):
        method = tool.split("_")[1].upper()
        url    = inp.get("url", "")
        body   = inp.get("body", {})
        body_p = ", ".join(f"{k}={str(v)[:15]}" for k, v in list(body.items())[:2]) if body else ""
        return (f"{method} {url}  {body_p}" if body_p else f"{method} {url}")[:80]
    if tool == "http_request":
        return f"{inp.get('method', '').upper()} {inp.get('url', '')}"[:80]
    parts = [f"{k}={str(v)[:20]}" for k, v in list((inp or {}).items())[:3]]
    return ", ".join(parts)[:80]


if __name__ == "__main__":
    print("\nPrompt2TestAgentCore server starting...")
    print("   API:    http://localhost:8000")
    print("   Health: http://localhost:8000/api/health\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
