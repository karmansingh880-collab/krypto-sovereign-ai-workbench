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
import os
import re
from typing import Optional

from langgraph.graph import StateGraph, START, END

from backend.app import live
from backend.app.agent import direct, vision_context
from backend.app.agent import tools as agent_tools
from backend.app.agent.demo_fallback import ensure_approval_note
from backend.app.agent.doc_context import (
    compact_text, is_summary_request, long_document_notes, needs_whole_document_notes, select_context_details,
)
from backend.app.agent.state import AgentState, Observation, new_state
from backend.app.llm import chat as llm_chat
from backend.app.router.router import demo_model, resolve_model_tag, route_task
from backend.app.tools.calc import calculate_corrosion
from backend.app.tools.ocr import extract_text

try:
    import ollama
except ImportError:  # pragma: no cover
    ollama = None

MAX_ITERATIONS = 10
MAX_RETRIES = 3

GENERAL_MODEL_TAG = "qwen3:14b"  # used directly by the planner, not routed

# Ollama's default context is small (4096 tokens) and silently drops the START
# of an over-long prompt -- i.e. the goal. Demo mode feeds whole attached
# documents into step prompts, so ask for a bigger window there.
DEMO_NUM_CTX = 8192
# How much of an attached file's text a step prompt may carry (demo mode).
MAX_FILE_CONTEXT_CHARS = 8000
PLAN_EXCERPT_CHARS = 1500
# How much of a document a question is answered from: passages beyond this are picked by relevance.
DOC_QA_CONTEXT_CHARS = 8000
# Instructions that call for a longer answer than the default short one.
_WANTS_DETAIL_RE = re.compile(r"\b(list|all|every|each|steps|detail|detailed|explain|describe|compare|table)\b", re.I)


def _call_ollama(model_tag: str, prompt: str, *, max_tokens: Optional[int] = None, stream: bool = False) -> str:
    """One local-model call (kept loaded between calls). `stream` shows the reply live to a watching browser;
    `max_tokens` caps its length."""
    return llm_chat(
        model_tag,
        prompt,
        options={"num_ctx": DEMO_NUM_CTX} if demo_model() else None,
        max_tokens=max_tokens,
        stream_to_ui=stream,
    ).strip()


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


def _planning_context(state: AgentState) -> str:
    """Tell the planner what the user attached and what the steps can NOT do.

    Without this the planner only sees the goal text, so a question about an
    attached document (e.g. "which SQL tables were used?") gets planned as a
    live-database task ("run SHOW TABLES", "pip install ...") that can never
    work: steps run offline in a sandbox with no database and no packages.
    """
    lines = [
        "Constraints: steps run on a local machine with NO internet, NO database "
        "connection and NO package installation. Never plan to connect to a "
        "database, run SQL against a server, or pip install anything."
    ]
    if state["files"]:
        names = ", ".join(os.path.basename(f) for f in state["files"])
        lines.append(
            f"The user attached: {names}. That document is the source of truth for the "
            "goal: the first step must be to read it (extract_text), and the remaining "
            "steps must answer from what the document actually says."
        )
        for obs in state["observations"]:
            if obs.get("forced") and obs.get("tool_used") == "extract_text" and isinstance(obs.get("result"), str):
                lines.append("Beginning of the attached document:\n" + obs["result"][:PLAN_EXCERPT_CHARS])
                break
    return "\n".join(lines)


def _has_preread_document(state: AgentState) -> bool:
    return any(
        o.get("forced") and o.get("tool_used") == "extract_text"
        and not o.get("error") and isinstance(o.get("result"), str)
        for o in state["observations"]
    )


def plan_node(state: AgentState) -> AgentState:
    # Demo mode: a small model plans a question about an attached document badly
    # (it wanders off into "execute the installation steps" etc.). With the file
    # text already in state, answering is a single step. The corrosion workflow
    # (approval note) keeps its normal multi-step plan.
    if (
        not state["plan"]
        and _has_preread_document(state)
        and "approval note" not in state["goal"].lower()
    ):
        state["plan"] = [state["goal"]]
        state["current_step"] = 0
        print("[PLAN] (demo mode: question about an attached document -> single step) ->")
        print(f"    1. {state['goal']}")
        return state

    context = _planning_context(state)
    if not state["plan"]:
        prompt = (
            "Break the following goal into a short numbered list of concrete, "
            "actionable steps (3-6 steps). Only output the numbered list.\n\n"
            f"{context}\n\n"
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
            f"{context}\n\n"
            f"Goal: {state['goal']}\n"
            f"Remaining steps: {remaining}\n"
            f"Failure reason: {last_error}"
        )
        reason = "replan after repeated failure"

    live.start("plan", "Planning the steps", "The local model is breaking the task into steps")
    try:
        raw_plan = _call_ollama(resolve_model_tag(GENERAL_MODEL_TAG), prompt)
        new_plan = _parse_plan(raw_plan)
        live.done("plan", f"{len(new_plan)} steps planned")
    except Exception as exc:
        # If planning itself fails, fall back to treating the whole goal as one step.
        new_plan = [state["goal"]]
        raw_plan = f"(planning failed: {exc})"
        live.fail("plan", str(exc))

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
            limit = MAX_CONTEXT_CHARS_PER_STEP
            if obs.get("forced") and obs.get("tool_used") == "extract_text":
                label = "FULL TEXT OF THE ATTACHED FILE, already read by the system -- answer from this"
                limit = MAX_FILE_CONTEXT_CHARS
            elif obs.get("forced"):
                label = "SYSTEM-COMPUTED, AUTHORITATIVE -- copy these exact numbers, do not recompute or alter them"
            elif obs.get("tool_used"):
                label = f"tool '{obs['tool_used']}'"
            else:
                label = "model"
            parts.append(f"  Step {i} ({obs['step']}) [{label}]:")
            parts.append(f"  {obs['response'][:limit]}")
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
    if _has_preread_document(state):
        parts.append(
            "The attached file has ALREADY been read -- its full text is in the results above. "
            "Do NOT call extract_text or describe_image. Answer this step in plain text, "
            "using only what that text says."
        )
        parts.append("")
    parts.append(f"Current step to perform: {step}")
    parts.append(
        "Answer only for the current step, using the results above where relevant. "
        "Be concise."
    )
    return "\n".join(parts)


def _doc_qa_mode(state: AgentState) -> bool:
    """Demo mode, attached document already read, and not the corrosion workflow."""
    return _has_preread_document(state) and "approval note" not in state["goal"].lower()


def _answer_from_document(state: AgentState, step: str, model_tag: str) -> str:
    """Answer an instruction about the attached document in plain text, with NO
    tools: given the tool list, a small model calls extract_text/search_knowledge
    again and its tool output ends up as the "answer"."""
    documents = [
        o["result"] for o in state["observations"]
        if o.get("forced") and o.get("tool_used") == "extract_text"
        and not o.get("error") and isinstance(o.get("result"), str)
    ]
    # One file: exactly its text. Several files: mark where each one starts.
    document = documents[0] if len(documents) == 1 else "\n\n".join(
        f"=== DOCUMENT {n} ===\n{text}" for n, text in enumerate(documents, 1)
    )
    document = compact_text(document)  # fewer wasted tokens for the model to read
    picture_reply = (
        vision_context.plain_description_reply(state["observations"], step)
        or vision_context.verbatim_text_reply(state["observations"], step)
    )
    if picture_reply:
        # "Describe this picture": the vision model has already answered; do not let a second model rewrite it.
        live.start("retrieve", "Finding the relevant passages")
        live.done("retrieve", "Using the recognised text and picture description as they are")
        live.start("answer", "Writing the answer")
        live.done("answer", "No rewriting needed")
        return picture_reply
    live.start("retrieve", "Finding the relevant passages")
    context = ""
    sources: list = []
    if needs_whole_document_notes(document, step):
        # A summary of a LONG document: notes section by section, so the whole document counts.
        live.update("retrieve", "Reading the long document section by section")
        notes, covered, total = long_document_notes(
            document,
            lambda prompt, max_tokens: _call_ollama(model_tag, prompt, max_tokens=max_tokens),
            progress=lambda n, of: live.update("retrieve", f"Reading section {n} of {of}", n, of),
        )
        if notes:
            context = f"(Notes taken from {covered} of {total} sections of a long document)\n{notes}"
            sources = [{"note": f"Notes from {covered} of {total} evenly spread sections"}]
    if not context:
        context, sources = select_context_details(document, step, DOC_QA_CONTEXT_CHARS)
    print(f"[DOC-QA] document {len(document)} chars -> {len(context)} chars sent to the model")
    live.set_extra("sources", sources)
    live.done(
        "retrieve",
        "Using the whole document" if not sources
        else f"Using {len(context):,} of {len(document):,} characters",
    )

    earlier = live.conversation()
    prompt = (
        "You are given the text of a document and an instruction about it. Follow the "
        "instruction using ONLY the document text. If the document does not contain the "
        "answer, say so plainly. Write the answer directly in plain text (use bullet points "
        "or a list if the instruction asks for one). Do not output JSON and do not mention tools. "
        "Be concise: at most about 120 words, unless the instruction asks for a summary, a list, "
        "or more detail. Quote exact figures and names from the document.\n\n"
        f"DOCUMENT TEXT:\n{context}\n\n"
        + (f"EARLIER IN THIS CONVERSATION:\n{earlier}\n\n" if earlier else "")
        + f"INSTRUCTION: {step}\n\n"
        "ANSWER:"
    )
    wants_more = is_summary_request(step) or bool(_WANTS_DETAIL_RE.search(step))
    live.start("answer", "Writing the answer", "The local model is writing the text")
    try:
        reply = _call_ollama(model_tag, prompt, max_tokens=600 if wants_more else 320, stream=True)
    except Exception as exc:
        live.fail("answer", str(exc))
        raise
    if _looks_like_refusal(reply) and context.strip():
        # Small models sometimes refuse ("I can't fulfill this request") when the question mentions an
        # image, although the text is right there. Ask once more, saying plainly what the text is.
        print("[DOC-QA] the model refused although the text is available -> asking once more")
        live.update("answer", "Trying again with a clearer instruction")
        live.reset_text()
        retry = (
            "Below is text that has ALREADY been extracted from the user's file (for an image it was "
            "read with OCR). Reading it is allowed and possible. Answer the instruction using only this "
            "text. If the instruction asks what the file or image says, give the text.\n\n"
            f"EXTRACTED TEXT:\n{context}\n\nINSTRUCTION: {step}\n\nANSWER:"
        )
        try:
            second = _call_ollama(model_tag, retry, max_tokens=320, stream=True)
        except Exception:
            second = ""
        if second.strip() and not _looks_like_refusal(second):
            reply = second
    live.done("answer")
    return reply


_REFUSAL_RE = re.compile(
    r"^\W*(i(?:\s+(?:can(?:'|’)?t|cannot|can\s+not|am\s+(?:not\s+able|unable))|(?:'|’)m\s+(?:not\s+able|unable))|"
    r"sorry|i\s+apologi[sz]e|as\s+an\s+ai)",
    re.IGNORECASE,
)


def _looks_like_refusal(reply: str) -> bool:
    """A short answer that starts by declining, e.g. "I can't fulfill this request."."""
    text = (reply or "").strip()
    if not (0 < len(text) < 200 and _REFUSAL_RE.match(text)):
        return False
    # "I can't find that in the document" is an honest answer about the text, not a refusal.
    return not re.search(r"\b(find|found|mention|contain|document|provided|text says)", text, re.IGNORECASE)


def act_node(state: AgentState) -> AgentState:
    """One step of the loop, reported live. (The document-question path reports its own steps.)"""
    index = state["current_step"]
    step = state["plan"][index]
    report = not _doc_qa_mode(state)
    key = f"step:{index + 1}"
    if report:
        live.start(key, f"Step {index + 1}: {step[:100]}", "The local model is working on this step")
    state = _act_node(state)
    if report:
        last = state["observations"][-1]
        if last.get("error"):
            live.fail(key, str(last["error"]))
        else:
            tool = last.get("tool_used")
            live.done(key, f"used {tool}" if isinstance(tool, str) else "answered")
    return state


def _act_node(state: AgentState) -> AgentState:
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
        if _doc_qa_mode(state):
            observation["response"] = _answer_from_document(state, step, decision.model_tag)
            state["observations"].append(observation)
            return state

        raw_reply = _call_ollama(decision.model_tag, _build_step_prompt(state, step))
        tool_calls = agent_tools.try_parse_tool_calls(raw_reply)

        if tool_calls is None and agent_tools.looks_like_tool_call(raw_reply):
            # The model tried to call a tool but the JSON is broken (truncated,
            # unbalanced braces, Python None/True/False instead of null/true/
            # false). Treating that as a plain-text answer would silently mark
            # the step done with nothing executed.
            raise ValueError(
                "Your reply started a tool call but was not valid, complete JSON "
                "(truncated or unbalanced braces? JSON uses null/true/false, not "
                "None/True/False). Reply again with ONLY the complete JSON object."
            )

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


def _finish_if_note_ready(state: AgentState) -> AgentState:
    """Demo mode: once the approval note exists, skip the remaining narration
    steps (each costs minutes of CPU inference) by jumping to the last step, so
    reflect_node's next advance ends the run."""
    if ensure_approval_note(state) and state["plan"]:
        state["current_step"] = max(state["current_step"], len(state["plan"]) - 1)
    return state


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
        return _finish_if_note_ready(state)

    readings: list = []
    required_thickness: Optional[float] = None

    for obs in state["observations"]:
        if obs.get("error"):
            continue
        if obs.get("tool_used") == "extract_text" and isinstance(obs.get("result"), str) and not readings:
            readings = _parse_readings_from_ocr_text(obs["result"])
        elif obs.get("tool_used") == "search_knowledge" and isinstance(obs.get("result"), list) and required_thickness is None:
            required_thickness = _extract_required_thickness(obs["result"])

    # Demo-mode assist (small models only): a 3B model often searches the
    # knowledge base with a query that misses the t-min clause, so the forced
    # calculation below would never fire. Once readings exist, look the clause
    # up directly in code, once. Locked-model runs are unaffected.
    if readings and required_thickness is None and demo_model():
        already_forced_search = any(
            o.get("forced") and o.get("tool_used") == "search_knowledge"
            for o in state["observations"]
        )
        if not already_forced_search:
            query = "minimum allowable remaining wall thickness t-min for carbon steel process piping"
            print(f"[FORCE-SEARCH] readings present but SOP t-min not found yet -> search_knowledge({query!r}) in code")
            live.start("search", "Looking up the SOP minimum thickness", "Searching the local knowledge base")
            try:
                search_result = agent_tools.execute_tool(
                    {"tool": "search_knowledge", "args": {"query": query, "top_k": 3}}
                )
            except Exception as exc:
                search_result = f"forced search failed: {exc}"
                live.fail("search", str(exc))
            state["observations"].append({
                "step": "[SYSTEM] forced search_knowledge for the SOP minimum wall thickness",
                "model_used": "none",
                "model_tag": "none",
                "reason": "demo-mode assist -- small model missed the t-min clause",
                "tool_used": "search_knowledge",
                "tool_args": {"query": query, "top_k": 3},
                "response": search_result if isinstance(search_result, str) else json.dumps(search_result, default=str),
                "result": search_result,
                "error": None,
                "forced": True,
            })
            if isinstance(search_result, list):
                required_thickness = _extract_required_thickness(search_result)
                live.done("search", f"Minimum allowable thickness: {required_thickness} mm")

    if not readings or required_thickness is None:
        return state  # prerequisites not met yet -- wait for more steps

    live.start("calc", "Calculating corrosion for each location", f"{len(readings)} locations")

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
    live.done("calc", f"{len(corrosion_table)} locations calculated")
    return _finish_if_note_ready(state)


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


def _preread_attached_files(state: AgentState) -> None:
    """Demo mode: read every attached file in code before planning.

    A small model rarely gets "call extract_text on the attached file" right,
    and then has no document to answer from. Reading up front puts the text in
    state so the planner and every step prompt can use it. Locked-model runs
    are unchanged (the model calls extract_text itself).
    """
    for index, path in enumerate(state["files"]):
        name = live.file_label(path)
        key = f"read:{index}"
        slow = os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS
        live.start(key, f"Reading {name}", "Recognizing the text with OCR; this can take a minute" if slow else "")
        observation: Observation = {
            "step": f"[SYSTEM] read attached file {name}",
            "model_used": "none",
            "model_tag": "none",
            "reason": "demo-mode assist -- the small model is given the file text up front",
            "tool_used": "extract_text",
            "tool_args": {"file_path": path},
            "response": "",
            "result": None,
            "error": None,
            "forced": True,
        }
        picture: Optional[dict] = None
        try:
            text = extract_text(path)
            print(f"[PREREAD] {name}: {len(text)} characters extracted")
            live.done(key, f"{len(text):,} characters read")
            if (
                slow
                and "approval note" not in state["goal"].lower()
                and vision_context.wants_picture_understanding(state["goal"], text)
            ):
                # The words in the image are not enough for this question: let the vision model look at it.
                # (When they are, the planned "Looking at ..." step is simply shown as skipped.)
                live.start(f"vision:{index}", f"Looking at {name}",
                           "The local vision model is describing the picture; this can take a minute")
                try:
                    picture = vision_context.describe(path, state["goal"])
                    text = f"{text}\n\n[What the picture shows]\n{picture['text']}".strip()
                    live.done(f"vision:{index}", "Described by " + (picture["model"] or "plain image facts"))
                except Exception as exc:
                    picture = None
                    live.fail(f"vision:{index}", str(exc))
            observation["result"] = text
            observation["response"] = text
        except Exception as exc:
            message = _friendly_read_error(name, path, exc)
            observation["error"] = message
            print(f"[PREREAD] {name}: failed: {exc}")
            live.fail(key, message)
        state["observations"].append(observation)
        if picture:
            state["observations"].append({
                "step": f"[SYSTEM] look at the picture {name}",
                "model_used": "vision", "model_tag": picture["route"]["model"], "reason": picture["route"]["reason"],
                "tool_used": "describe_image", "tool_args": {"file": name},
                "response": picture["text"], "result": picture["text"], "error": None, "forced": True,
                "route": picture["route"],
            })


def _friendly_read_error(name: str, path: str, exc: Exception) -> str:
    """A plain sentence for a file that could not be read (a damaged or renamed file is the usual cause)."""
    kind = os.path.splitext(path)[1].lower().lstrip(".") or "file"
    detail = str(exc).strip().splitlines()[0][:160] if str(exc).strip() else exc.__class__.__name__
    return f"Could not read {name}: the file looks damaged or is not a real {kind.upper()} file ({detail})."


def _nothing_readable(state: AgentState) -> bool:
    """True when files were attached and every one of them failed to read: no question can be answered."""
    if not state["files"]:
        return False
    reads = [o for o in state["observations"] if o.get("forced") and o.get("tool_used") == "extract_text"]
    return len(reads) == len(state["files"]) and all(o.get("error") for o in reads)


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
_CORROSION_PLAN = [
    "Read the inspection report",
    "Look up the minimum allowable thickness in the SOP",
    "Calculate corrosion rate and remaining life for every location",
    "Generate the approval note",
]


def _announce_plan(goal: str, files: list[str]) -> None:
    """Tell a watching browser which steps to expect (demo mode, where the steps are known up front)."""
    if not (live.watching() and demo_model() and files):
        return
    items = []
    for i, path in enumerate(files):
        items.append((f"read:{i}", f"Reading {live.file_label(path)}"))
        if os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS and "approval note" not in goal.lower() and vision_context.available():
            items.append((f"vision:{i}", f"Looking at {live.file_label(path)}"))  # skipped if the words alone are enough
    if "approval note" in goal.lower():
        items += [
            ("search", "Looking up the SOP minimum thickness"),
            ("calc", "Calculating corrosion for each location"),
            ("note", "Drafting the approval note"),
        ]
    else:
        items += [("retrieve", "Finding the relevant passages"), ("answer", "Writing the answer")]
    live.plan(items)


def _corrosion_fast_path(state: AgentState) -> bool:
    """Demo mode: build the corrosion result straight from the OCR text, in code.

    The readings, the SOP lookup, the calculation and the approval note are all computed by code, so
    asking a small model to plan and to call tools first only adds minutes (and chances to go wrong).
    Returns False (leaving the normal agent loop to run) if the report has no readings table or the
    note could not be produced.
    """
    text = next(
        (o["result"] for o in state["observations"]
         if o.get("forced") and o.get("tool_used") == "extract_text"
         and not o.get("error") and isinstance(o.get("result"), str)),
        None,
    )
    if not text or not _parse_readings_from_ocr_text(text):
        return False
    state["plan"] = list(_CORROSION_PLAN)
    state["current_step"] = 0
    force_corrosion_node(state)
    if any(o.get("tool_used") == "generate_approval_note" and not o.get("error") for o in state["observations"]):
        state["current_step"] = len(state["plan"])
        return True
    state["plan"] = []  # fall back to the normal loop, which plans for itself
    state["current_step"] = 0
    return False


def run_agent(goal: str, files: Optional[list[str]] = None) -> AgentState:
    app = build_graph()
    initial_state = new_state(goal, files)
    if direct.applies(initial_state):
        # A question or a code request with no document: answered directly (and code is run and verified)
        # instead of being planned as tool calls, which a small model does badly.
        try:
            direct.announce_plan(goal)
            direct.handle(initial_state)
            return initial_state
        except Exception as exc:  # never lose the run: fall back to the original loop
            print(f"[DIRECT] failed ({exc}); using the agent loop instead")
            initial_state = new_state(goal, files)
    _announce_plan(goal, initial_state["files"])
    if demo_model() and initial_state["files"]:
        _preread_attached_files(initial_state)
        if _nothing_readable(initial_state):
            # A damaged or renamed file can never be answered from: say so now instead of letting the model
            # plan and retry for minutes. The run ends as failed with the reason (the last step's error).
            initial_state["plan"] = ["Read the attached file"]
            initial_state["current_step"] = 0
            return initial_state
        if "approval note" in goal.lower() and _corrosion_fast_path(initial_state):
            return initial_state
    # LangGraph's recursion_limit counts every node execution (plan/act/
    # observe/reflect/approve), not our own semantic "iterations". One of
    # our loop iterations is up to 4 node executions, plus replans add an
    # extra plan node each time, so the default limit of 25 would trip
    # before our own MAX_ITERATIONS hard cap ever gets a chance to fire.
    recursion_limit = MAX_ITERATIONS * 5 + 10
    final_state = app.invoke(initial_state, config={"recursion_limit": recursion_limit})
    return final_state
