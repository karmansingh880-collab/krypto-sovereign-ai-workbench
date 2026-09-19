"""
The single entry point Backend calls to run an AI/Agent task.

This is the handoff boundary between the AI/Agent side (this package) and
Backend's /tasks route: Backend should import and await run_agent_task()
and never call graph.py, tools.py, or any individual tool directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from backend.app.agent.graph import run_agent

# Tools whose real (unstringified) result is a file path Backend should
# surface as a deliverable. Extend this if a future tool starts producing
# files of its own.
FILE_PRODUCING_TOOLS = {"generate_approval_note"}


async def run_agent_task(goal: str, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Run one agent task end-to-end and return a clean, JSON-serializable result.

    What Backend passes in:
        goal: a plain-language description of what to do, e.g. "Read the
            inspection report, extract wall-thickness readings, calculate
            corrosion rate and remaining life for each location, cite the
            relevant SOP, and generate an approval note."
        file_paths: absolute paths to any files the task needs (scanned
            reports, images, PDFs). Optional -- omit or pass None/[] for
            goals that need no file.

    What Backend gets back, a dict with:
        status: "success" if the agent's plan ran to completion with no
            unresolved error, "failed" otherwise (hit the iteration/retry
            limits, a step errored out with no more retries left, or the
            run paused waiting on a human-approval step).
        steps: the full Plan->Act->Observe->Reflect audit trail, one dict
            per step attempt, in order:
                {"step": str, "tool_used": str|list|None,
                 "tool_args": dict|list|None, "response": str,
                 "error": str|None}
            tool_used/tool_args are lists when a single step made several
            real tool calls (e.g. one calculation per pipe location).
        outputs: absolute paths to every file the task actually produced
            (currently: generate_approval_note's .docx). Empty list if the
            task produced no files.
        final_message: a short, plain-language summary -- the last
            successful step's result on success, or the reason for failure.

    Every real tool call inside the run is a genuine extract_text/
    describe_image/calculate_corrosion/search_knowledge/run_code_sandbox/
    generate_approval_note invocation (see backend/app/agent/tools.py) --
    this function does no work itself beyond flattening the agent's
    internal state into the shape above. The underlying graph.py loop is
    synchronous (it calls Ollama and local tools directly), so it's run in
    a worker thread via asyncio.to_thread to avoid blocking the event loop
    for the run's full duration.

    Import note for Backend: this module (like graph.py and tools.py)
    imports via the `backend.app...` absolute path, so the process must be
    run with the repository root on sys.path -- e.g.
    `uvicorn backend.app.main:app` from the repo root -- not from inside
    backend/ (backend/app/main.py currently imports as `app.db.mongo`,
    which assumes the opposite; the two need to agree on one convention
    before this is wired into a real route).
    """
    try:
        final_state = await asyncio.to_thread(run_agent, goal, file_paths or [])
    except Exception as exc:
        return {
            "status": "failed",
            "steps": [],
            "outputs": [],
            "final_message": f"Agent run crashed before producing any steps: {exc}",
        }

    steps: List[Dict[str, Any]] = []
    outputs: List[str] = []

    for obs in final_state["observations"]:
        steps.append(
            {
                "step": obs["step"],
                "tool_used": obs.get("tool_used"),
                "tool_args": obs.get("tool_args"),
                "response": obs.get("response"),
                "error": obs.get("error"),
            }
        )

        tools_used = obs.get("tool_used")
        tools_used = tools_used if isinstance(tools_used, list) else [tools_used]
        if not any(t in FILE_PRODUCING_TOOLS for t in tools_used):
            continue
        results = obs.get("result")
        results = results if isinstance(results, list) else [results]
        outputs.extend(r for r in results if isinstance(r, str))

    last_obs = final_state["observations"][-1] if final_state["observations"] else None
    plan_finished = bool(final_state["plan"]) and final_state["current_step"] >= len(final_state["plan"])

    if final_state["needs_approval"]:
        status = "failed"
        final_message = "Run paused: a step needs human approval before it can continue."
    elif plan_finished and (last_obs is None or not last_obs.get("error")):
        status = "success"
        final_message = last_obs["response"][:500] if last_obs and last_obs.get("response") else "Plan completed."
    else:
        status = "failed"
        reason = f": {last_obs['error']}" if last_obs and last_obs.get("error") else "."
        final_message = (
            f"Run stopped before completing the plan "
            f"({final_state['current_step']}/{len(final_state['plan'])} steps done){reason}"
        )

    return {
        "status": status,
        "steps": steps,
        "outputs": outputs,
        "final_message": final_message,
    }
