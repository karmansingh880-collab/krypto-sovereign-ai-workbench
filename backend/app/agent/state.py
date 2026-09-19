"""
Shared state for Krypto's LangGraph agent loop.

Every node (plan, act, observe, reflect) reads from and writes to this
same structure. Keeping it as a single TypedDict is what lets LangGraph
pass state between nodes automatically.
"""

from __future__ import annotations

from typing import Any, List, Optional, TypedDict


class Observation(TypedDict, total=False):
    step: str              # the plan step this observation is for
    model_used: str        # internal router key, e.g. "coder" -- proves auto-selection
    model_tag: str         # ollama tag actually called
    reason: str            # router's reason string
    tool_used: Optional[str]   # real tool name the model invoked, or None for plain text
    tool_args: Optional[dict]  # args the model supplied for that tool call
    response: str          # text form of the result (model's text, or the tool's result)
    result: Any            # the tool's real, unstringified return value (None if no tool)
    error: Optional[str]   # set if the step failed (bad JSON, bad args, or tool exception)
    # True only for observations force_corrosion_node injects directly in code
    # (never via the model's tool-call JSON). reflect_node must skip these when
    # deciding retry/advance for the CURRENT plan step -- they aren't an
    # attempt at the current step, they're an out-of-band, always-run
    # deterministic calculation. _build_step_prompt still shows them to the
    # model as authoritative context.
    forced: bool


class AgentState(TypedDict):
    goal: str
    files: List[str]
    plan: List[str]
    current_step: int
    observations: List[Observation]
    retries: int
    iterations: int
    outputs: List[str]
    needs_approval: bool
    # Internal routing field set by reflect_node and read by the graph's
    # conditional edge. Not part of the "real" agent state the rest of the
    # app cares about, but LangGraph conditional-edge functions can't
    # persist mutations themselves, so the decision has to live in state.
    _decision: str


def new_state(goal: str, files: Optional[List[str]] = None) -> AgentState:
    """Build a fresh AgentState for a new run."""
    return AgentState(
        goal=goal,
        files=files or [],
        plan=[],
        current_step=0,
        observations=[],
        retries=0,
        iterations=0,
        outputs=[],
        needs_approval=False,
        _decision="",
    )
