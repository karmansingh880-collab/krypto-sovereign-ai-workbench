"""
Krypto's core agent loop: Plan -> Act -> Observe -> Reflect.

Reflect decides what happens next by returning one of:
    "retry"      - re-run the same step (same model call, same step index)
    "replan"     - ask the planner to revise the remaining steps
    "next_step"  - move on to the next step in the plan
    "approve"    - stop and wait for human approval
    "stop"       - done (either finished successfully, or hit a hard limit)

Hard limits (checked first, before any retry/replan logic):
    MAX_ITERATIONS = 10  total loop passes for the whole run
    MAX_RETRIES    = 3   retries allowed per individual step
"""

from __future__ import annotations

import json
import re
from typing import Optional

from langgraph.graph import StateGraph, START, END

from backend.app.agent import tools as agent_tools
from backend.app.agent.state import AgentState, Observation, new_state
from backend.app.router.router import route_task
from backend.app.tools.calc import calculate_corrosion

try:
    import ollama
except ImportError:  # pragma: no cover
    ollama = None

MAX_ITERATIONS = 10
MAX_RETRIES = 3

GENERAL_MODEL_TAG = "qwen3:14b"  # used directly by the planner, not routed


def _call_ollama(model_tag: str, prompt: str) -> str:
    if ollama is None:
        raise RuntimeError("ollama python package is not installed")
    response = ollama.chat(
        model=model_tag,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"].strip()


def _parse_plan(raw_text: str) -> list[str]:
    """Turn a numbered-list response into a clean list of step strings."""
    steps = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip common list markers: "1.", "1)", "-", "*"
        for marker_len in range(len(line)):
            if not (line[marker_len].isdigit() or line[marker_len] in ".)-* "):
                break
        else:
            marker_len = len(line)
        cleaned = line[marker_len:].strip() if marker_len else line
        if cleaned:
            steps.append(cleaned)
    return steps or [raw_text.strip()]


def plan_node(state: AgentState) -> AgentState:
    if not state["plan"]:
        prompt = (
            "Break the following goal into a short numbered list of concrete, "
            "actionable steps (3-6 steps). Only output the numbered list.\n\n"
            f"Goal: {state['goal']}"
        )
        reason = "initial plan"
    else:
        remaining = state["plan"][state["current_step"] :]
        last_error = state["observations"][-1].get("error") if state["observations"] else None
        prompt = (
            "The following plan step kept failing. Revise the remaining steps "
            "into a new short numbered list that avoids the same failure. "
            "Only output the numbered list.\n\n"
            f"Goal: {state['goal']}\n"
            f"Remaining steps: {remaining}\n"
            f"Failure reason: {last_error}"
        )
        reason = "replan after repeated failure"

    try:
        raw_plan = _call_ollama(GENERAL_MODEL_TAG, prompt)
        new_plan = _parse_plan(raw_plan)
    except Exception as exc:
        # If planning itself fails, fall back to treating the whole goal as one step.
        new_plan = [state["goal"]]
        raw_plan = f"(planning failed: {exc})"

    print(f"[PLAN] ({reason}) ->")
    for i, step in enumerate(new_plan, 1):
        print(f"    {i}. {step}")

    if reason == "initial plan":
        state["plan"] = new_plan
        state["current_step"] = 0
    else:
        state["plan"] = state["plan"][: state["current_step"]] + new_plan
        state["retries"] = 0

    return state


# A step that fans out into several real tool calls (e.g. one
# calculate_corrosion per pipe location) can easily produce a combined JSON
# result past 1000 chars. Truncating that below its actual length silently
# hides later items' real numbers from any step that needs to transcribe
# them (e.g. into the final approval note) -- the model then has nothing
# to copy from and guesses, which produced a wrong remaining-life value in
# testing. qwen3:14b's 32k-token context has ample room per step, so this
# is sized to comfortably fit several structured tool results rather than
# to fit the model's window.
MAX_CONTEXT_CHARS_PER_STEP = 4000


def _build_step_prompt(state: AgentState, step: str) -> str:
    """Give the model the goal, prior step results, attached files, and the
    tool schema -- not just the bare step.

    Without the prior-results context, steps like "combine the above into one
    sentence" arrive with nothing to combine and the model asks the user what
    it is meant to work on. Without the tool schema, the model has no way to
    trigger a real OCR/vision/calc/search call instead of guessing an answer.
    """
    parts = [f"Overall goal: {state['goal']}", ""]

    if state["files"]:
        parts.append(f"Attached files: {state['files']}")
        parts.append("")

    previous = [o for o in state["observations"] if not o.get("error")]
    if previous:
        parts.append("Results from previous steps:")
        for i, obs in enumerate(previous, 1):
            if obs.get("forced"):
                label = "SYSTEM-COMPUTED, AUTHORITATIVE -- copy these exact numbers, do not recompute or alter them"
            elif obs.get("tool_used"):
                label = f"tool '{obs['tool_used']}'"
            else:
                label = "model"
            parts.append(f"  Step {i} ({obs['step']}) [{label}]:")
            parts.append(f"  {obs['response'][:MAX_CONTEXT_CHARS_PER_STEP]}")
        parts.append("")

    # If the immediately preceding attempt was for this same step and failed
    # (bad tool JSON, bad args, or a tool exception), tell the model why, so
    # the retry can actually correct the mistake instead of repeating it.
    if state["observations"]:
        last = state["observations"][-1]
        if last.get("error") and last["step"] == step:
            parts.append(
                f"Your previous attempt at this exact step failed: {last['error']}\n"
                "Correct the mistake and try again. If your previous attempt "
                "called a tool, you MUST call that tool again with corrected "
                "arguments -- a plain-text description of the fix does NOT "
                "count as fixing it, the fix has to be re-verified by actually "
                "re-running the tool."
            )
            parts.append("")

    parts.append(agent_tools.tools_prompt_block())
    parts.append("")
    parts.append(f"Current step to perform: {step}")
    parts.append(
        "Answer only for the current step, using the results above where relevant. "
        "Be concise."
    )
    return "\n".join(parts)


def act_node(state: AgentState) -> AgentState:
    step = state["plan"][state["current_step"]]
    decision = route_task(step)

    print(
        f"[ACT] step {state['current_step'] + 1}/{len(state['plan'])}: {step!r} "
        f"-> model decision by: {decision.model_tag} (reason: {decision.reason})"
    )

    observation: Observation = {
        "step": step,
        "model_used": decision.model_key,
        "model_tag": decision.model_tag,
        "reason": decision.reason,
        "tool_used": None,
        "tool_args": None,
        "response": "",
        "result": None,
        "error": None,
    }

    try:
        raw_reply = _call_ollama(decision.model_tag, _build_step_prompt(state, step))
        tool_calls = agent_tools.try_parse_tool_calls(raw_reply)

        if tool_calls is None:
            # Plain text answer -- no tool needed for this step.
            observation["response"] = raw_reply
        else:
            tool_names, tool_args_list, results = [], [], []
            for tool_call in tool_calls:
                agent_tools.validate_tool_call(tool_call)
                tool_name = tool_call["tool"]
                tool_args = tool_call.get("args", {})
                print(f"[ACT]   -> tool call: {tool_name}({tool_args})")

                result = agent_tools.execute_tool(tool_call)

                # run_code_sandbox can "succeed" as a tool call (real container ran)
                # while the code/tests inside it failed. That failure should feed
                # back into the SAME retry loop as any other step error, so the
                # model sees the real stdout/stderr and can fix its code.
                if tool_name == "run_code_sandbox" and isinstance(result, dict) and not result.get("passed"):
                    raise RuntimeError(
                        f"Sandbox run failed (passed=False).\n"
                        f"stdout: {result.get('stdout', '')}\n"
                        f"stderr: {result.get('stderr', '')}"
                    )

                tool_names.append(tool_name)
                tool_args_list.append(tool_args)
                results.append(result)

            # A step that repeats one tool per item (e.g. one calculation per
            # pipe location) yields several results here -- keep the plain
            # single-value shape for the common one-tool-call case, and only
            # fall back to lists when the step actually made more than one
            # call, so downstream context-building doesn't have to guess.
            single = len(tool_calls) == 1
            observation["tool_used"] = tool_names[0] if single else tool_names
            observation["tool_args"] = tool_args_list[0] if single else tool_args_list
            observation["result"] = results[0] if single else results
            final_result = observation["result"]
            observation["response"] = (
                final_result if isinstance(final_result, str) else json.dumps(final_result, default=str)
            )
    except Exception as exc:
        observation["error"] = str(exc)

    state["observations"].append(observation)
    return state


def observe_node(state: AgentState) -> AgentState:
    last = state["observations"][-1]
    if last.get("error"):
        print(f"[OBSERVE] step failed: {last['error']}")
    else:
        preview = last["response"][:200].replace("\n", " ")
        if last.get("tool_used"):
            print(
                f"[OBSERVE] tool '{last['tool_used']}' executed for real "
                f"-> result preview: {preview!r}"
            )
        else:
            print(f"[OBSERVE] model answered directly -> preview: {preview!r}")
        state["outputs"].append(last["response"])
    return state


# --- Structural enforcement of calculate_corrosion --------------------------
#
# Relying on the model to remember to call calculate_corrosion for every
# location -- and to remember correctly, across a multi-location report --
# has repeatedly failed in testing: the model has skipped it outright and
# invented numbers, or left corrosion_rate/remaining_life null while still
# recommending "Replace". Prompt-strengthening alone did not fix this.
#
# So this is no longer left to the model's discretion at all. The moment
# BOTH a real extract_text result (readings) and a real search_knowledge
# result (the SOP's required thickness) are present in state, this node
# parses them deterministically and calls calculate_corrosion directly in
# Python -- once per location, every time, unconditionally. There is no
# code path from "readings + SOP limit known" to "approval note drafted"
# that skips this.

_LOCATION_LINE_RE = re.compile(r"^[A-Za-z]{1,6}-?\d{1,5}[A-Za-z]?$")

_REQUIRED_THICKNESS_RE = re.compile(
    r"(?:minimum allowable(?: remaining)? wall thickness|t-?min)"
    r"[^\d]{0,150}?(\d+(?:\.\d+)?)\s*mm",
    re.IGNORECASE | re.DOTALL,
)


def _parse_readings_from_ocr_text(text: str) -> list[dict]:
    """
    Parse (location, previous_thickness, current_thickness, years) rows out
    of OCR'd inspection-report text.

    PaddleOCR returns one table CELL per line, not one line per row (e.g.
    "P-101", "12.5", "11.8", "5" as four separate lines) -- a single-line
    "4 columns separated by whitespace" regex silently matches nothing
    against real OCR output. This instead walks the lines positionally:
    whenever a line looks like a location code, the next 3 lines are taken
    as its previous/current thickness and years, if they parse as numbers.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    readings = []
    i = 0
    while i < len(lines):
        if _LOCATION_LINE_RE.match(lines[i]) and i + 3 < len(lines):
            try:
                previous_thickness = float(lines[i + 1])
                current_thickness = float(lines[i + 2])
                years = float(lines[i + 3])
            except ValueError:
                i += 1
                continue
            readings.append({
                "location": lines[i],
                "previous_thickness": previous_thickness,
                "current_thickness": current_thickness,
                "years": years,
            })
            i += 4
        else:
            i += 1
    return readings


def _extract_required_thickness(sop_chunks: list) -> Optional[float]:
    """Pull the numeric t-min (mm) out of search_knowledge's returned chunks."""
    combined_text = " ".join(chunk.get("text", "") for chunk in sop_chunks)
    match = _REQUIRED_THICKNESS_RE.search(combined_text)
    return float(match.group(1)) if match else None


def force_corrosion_node(state: AgentState) -> AgentState:
    """
    Runs after every single step, before reflect. A no-op until both a real
    OCR extraction and a real SOP search result exist in state; the moment
    both are present, it calls calculate_corrosion directly in code -- once
    per parsed location -- and never asks the model to do it.
    """
    already_forced = any(
        obs.get("forced") and obs.get("tool_used") == "calculate_corrosion"
        for obs in state["observations"]
    )
    if already_forced:
        return state

    readings: list = []
    required_thickness: Optional[float] = None

    for obs in state["observations"]:
        if obs.get("error"):
            continue
        if obs.get("tool_used") == "extract_text" and isinstance(obs.get("result"), str) and not readings:
            readings = _parse_readings_from_ocr_text(obs["result"])
        elif obs.get("tool_used") == "search_knowledge" and isinstance(obs.get("result"), list) and required_thickness is None:
            required_thickness = _extract_required_thickness(obs["result"])

    if not readings or required_thickness is None:
        return state  # prerequisites not met yet -- wait for more steps

    print(
        f"[FORCE-CALC] readings for {len(readings)} location(s) + required_thickness="
        f"{required_thickness}mm both present -> calling calculate_corrosion directly "
        f"in code (NOT via model JSON) for every location"
    )

    corrosion_table = []
    for reading in readings:
        result = calculate_corrosion(
            previous_thickness=reading["previous_thickness"],
            current_thickness=reading["current_thickness"],
            years=reading["years"],
            required_thickness=required_thickness,
        )
        row = {
            "location": reading["location"],
            "previous_thickness": reading["previous_thickness"],
            "current_thickness": reading["current_thickness"],
            "corrosion_rate": result["corrosion_rate_mm_per_year"],
            "remaining_life": result["remaining_life_years"],
        }
        corrosion_table.append(row)
        print(
            f"[FORCE-CALC]   {row['location']}: rate={row['corrosion_rate']} mm/yr, "
            f"remaining_life={row['remaining_life']} yrs "
            f"(required_thickness={required_thickness}mm)"
        )

    forced_observation: Observation = {
        "step": "[SYSTEM] forced calculate_corrosion for every extracted location",
        "model_used": "none",
        "model_tag": "none",
        "reason": "structural enforcement -- never left to model discretion",
        "tool_used": "calculate_corrosion",
        "tool_args": {
            "locations": [r["location"] for r in readings],
            "required_thickness": required_thickness,
        },
        "response": json.dumps(
            {"required_thickness": required_thickness, "corrosion_table": corrosion_table},
            default=str,
        ),
        "result": {"required_thickness": required_thickness, "corrosion_table": corrosion_table},
        "error": None,
        "forced": True,
    }
    state["observations"].append(forced_observation)
    state["outputs"].append(forced_observation["response"])
    return state


def reflect_node(state: AgentState) -> AgentState:
    # All state mutation (retries/current_step/iterations) MUST happen here,
    # inside an actual graph node. Conditional-edge functions in LangGraph
    # are routing-only: any dict mutation performed inside one is discarded
    # and never persisted, which previously caused retries to never advance.
    state["iterations"] += 1
    print(f"[REFLECT] iteration {state['iterations']}/{MAX_ITERATIONS}")

    if state["iterations"] >= MAX_ITERATIONS:
        print("[REFLECT] hit MAX_ITERATIONS, stopping.")
        state["_decision"] = "stop"
        return state

    # force_corrosion_node may have just appended a synthetic observation
    # after the real step's own observation. That forced entry is not an
    # attempt at the current plan step -- it's an always-run, out-of-band
    # calculation -- so retry/advance decisions must be based on the last
    # REAL (non-forced) observation, or a forced success on an unrelated
    # step would get mistaken for the current step's outcome.
    real_observations = [o for o in state["observations"] if not o.get("forced")]
    last = real_observations[-1]

    if last.get("error"):
        if state["retries"] < MAX_RETRIES:
            state["retries"] += 1
            print(f"[REFLECT] error -> retry ({state['retries']}/{MAX_RETRIES})")
            state["_decision"] = "retry"
        else:
            print("[REFLECT] retries exhausted -> replanning")
            state["_decision"] = "replan"
        return state

    if state["needs_approval"]:
        print("[REFLECT] step flagged needs_approval -> pausing for human")
        state["_decision"] = "approve"
        return state

    state["current_step"] += 1
    state["retries"] = 0

    if state["current_step"] >= len(state["plan"]):
        print("[REFLECT] plan complete -> stopping")
        state["_decision"] = "stop"
    else:
        print("[REFLECT] step succeeded -> next_step")
        state["_decision"] = "next_step"

    return state


def _decide_next(state: AgentState) -> str:
    """Pure read of the decision reflect_node already computed and stored."""
    return state["_decision"]


def approve_node(state: AgentState) -> AgentState:
    print("[APPROVE] waiting for human approval before continuing (stopping run).")
    return state


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("force_corrosion", force_corrosion_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("approve", approve_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "act")
    graph.add_edge("act", "observe")
    # force_corrosion runs unconditionally on every pass (it's a no-op until
    # its prerequisites exist) -- this is what makes calling
    # calculate_corrosion structural rather than dependent on the model
    # remembering to ask for it.
    graph.add_edge("observe", "force_corrosion")
    graph.add_edge("force_corrosion", "reflect")

    graph.add_conditional_edges(
        "reflect",
        _decide_next,
        {
            "retry": "act",
            "replan": "plan",
            "next_step": "act",
            "approve": "approve",
            "stop": END,
        },
    )
    graph.add_edge("approve", END)

    return graph.compile()


def run_agent(goal: str, files: Optional[list[str]] = None) -> AgentState:
    app = build_graph()
    initial_state = new_state(goal, files)
    # LangGraph's recursion_limit counts every node execution (plan/act/
    # observe/reflect/approve), not our own semantic "iterations". One of
    # our loop iterations is up to 4 node executions, plus replans add an
    # extra plan node each time, so the default limit of 25 would trip
    # before our own MAX_ITERATIONS hard cap ever gets a chance to fire.
    recursion_limit = MAX_ITERATIONS * 5 + 10
    final_state = app.invoke(initial_state, config={"recursion_limit": recursion_limit})
    return final_state
