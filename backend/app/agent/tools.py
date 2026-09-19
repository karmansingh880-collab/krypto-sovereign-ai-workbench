"""
Tool registry for the agent loop: what tools exist, how to describe them to
the model, how to validate a model's proposed tool call, and how to run the
real Python function behind it.

The Act node prompts the model to reply with JSON like
    {"tool": "extract_text", "args": {"file_path": "..."}}
when a step needs a tool, or plain text otherwise. Everything here is about
turning that JSON into a real function call -- never a placeholder result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Type

from backend.app.rag.search import search_knowledge
from backend.app.tools.calc import calculate_corrosion
from backend.app.tools.docgen import generate_approval_note
from backend.app.tools.ocr import extract_text
from backend.app.tools.sandbox import run_code_sandbox
from backend.app.tools.vision import describe_image


@dataclass
class ToolSpec:
    description: str
    function: Callable[..., Any]
    # arg_name -> accepted python type(s) for that arg
    required: Dict[str, Any]
    optional: Dict[str, Any]


TOOL_REGISTRY: Dict[str, ToolSpec] = {
    "extract_text": ToolSpec(
        description=(
            "extract_text(file_path): read text out of a PDF or image file. "
            "Digital PDFs are read directly; scanned PDFs/images are OCR'd."
        ),
        function=extract_text,
        required={"file_path": str},
        optional={},
    ),
    "describe_image": ToolSpec(
        description=(
            "describe_image(file_path, question): ask a vision model a question "
            "about an image (drawings, photos, handwriting)."
        ),
        function=describe_image,
        required={"file_path": str, "question": str},
        optional={},
    ),
    "calculate_corrosion": ToolSpec(
        description=(
            "calculate_corrosion(previous_thickness, current_thickness, years, "
            "required_thickness): pure-math corrosion rate and remaining-life "
            "calculation. ALWAYS use this tool for any such calculation -- "
            "never compute corrosion rate or remaining life yourself."
        ),
        function=calculate_corrosion,
        required={
            "previous_thickness": (int, float),
            "current_thickness": (int, float),
            "years": (int, float),
            "required_thickness": (int, float),
        },
        optional={},
    ),
    "search_knowledge": ToolSpec(
        description=(
            "search_knowledge(query, top_k=3): search the internal knowledge "
            "base and return cited chunks, or a not-found message."
        ),
        function=search_knowledge,
        required={"query": str},
        optional={"top_k": int},
    ),
    "run_code_sandbox": ToolSpec(
        description=(
            "run_code_sandbox(code, tests=None): run Python in an isolated, "
            "network-less Docker container and return {passed, stdout, stderr, "
            "output_files}. 'code' is your implementation/solution (e.g. the "
            "function definition(s) itself) -- it is saved as solution.py. "
            "'tests' (optional) is a SEPARATE script that verifies 'code' -- it "
            "must start with `from solution import <your function names>` and "
            "then use plain assert statements (no pytest -- no network to "
            "install it). Never put your implementation inside 'tests', and "
            "never put test/assert statements inside 'code' -- 'code' must be "
            "runnable on its own with nothing missing. ALWAYS use this tool to "
            "run/verify code you write -- never claim code works without "
            "executing it here first."
        ),
        function=run_code_sandbox,
        required={"code": str},
        optional={"tests": str},
    ),
    "generate_approval_note": ToolSpec(
        description=(
            "generate_approval_note(data): fill the approval-note Word template "
            "with inspection findings and write a real .docx file, returning its "
            "path. 'data' must be a JSON object with keys: title (str), date (str), "
            "prepared_by (str), findings_summary (str), required_thickness (float, "
            "the SOP's minimum allowable thickness), corrosion_table (list of "
            "objects with location, previous_thickness, current_thickness, "
            "corrosion_rate, remaining_life, status), sop_citations (list of "
            "objects with doc_name, page, text), recommendation (str). "
            "corrosion_rate/remaining_life in corrosion_table are NEVER computed "
            "by you -- find the SYSTEM-COMPUTED corrosion_table already present "
            "in the results from previous steps and copy those exact numbers; "
            "this call is REJECTED if any are null/missing, or if a row's status "
            "says 'Replace' while its current_thickness is actually above "
            "required_thickness. ALWAYS use this tool to produce the final "
            "approval note -- never describe or fake a .docx file without "
            "actually calling this tool."
        ),
        function=generate_approval_note,
        required={"data": dict},
        optional={},
    ),
}


def tools_prompt_block() -> str:
    """Describe the available tools and the JSON call format for the model."""
    lines = ["Available tools:"]
    for name, spec in TOOL_REGISTRY.items():
        lines.append(f"  - {spec.description}")
    lines.append("")
    lines.append(
        "To use a tool, reply with ONLY a JSON object and nothing else, e.g.:\n"
        '  {"tool": "extract_text", "args": {"file_path": "/path/to/file.pdf"}}'
    )
    lines.append(
        "If this step does not need a tool (pure writing/reasoning), just "
        "answer normally in plain text -- do not output JSON in that case."
    )
    lines.append(
        "If this step asks you to repeat the same action once per item "
        "(e.g. a calculation for each of several locations), reply with "
        "multiple separate tool-call JSON objects back to back, one per "
        "item, nothing else in between -- each one will be executed for "
        "real and you do not need to wait for a separate turn per item."
    )
    lines.append(
        "MANDATORY: if this step asks you to run, execute, call, test, or "
        "verify code (not just define/write it), you MUST actually invoke "
        "run_code_sandbox by making your ENTIRE reply the JSON tool call. "
        "Do NOT print the JSON as a markdown example, describe the command "
        "to run, or claim a result without having actually executed it -- "
        "an unexecuted claim ('this would pass') is not evidence it works."
    )
    return "\n".join(lines)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_objects(text: str) -> list[str]:
    """
    Return every top-level, balanced {...} substring in `text`, in order.

    Quote-aware brace counting (so a '}' inside a JSON string value doesn't
    end the match early) applied repeatedly, so this finds each object even
    when the model replies with several back-to-back tool calls -- e.g. one
    calculate_corrosion call per pipe location -- or leaves trailing prose
    after the JSON. A naive "first '{' to last '}'" slice (the previous
    approach) breaks in both of those cases: it either spans multiple
    objects into one invalid blob, or swallows trailing text that makes
    json.loads fail on "extra data", silently discarding a real tool call.
    """
    objects = []
    i, n = 0, len(text)
    while i < n:
        start = text.find("{", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escape = False
        end = None
        for j in range(start, n):
            ch = text[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
        if end is None:
            break  # unbalanced -- nothing more to find
        objects.append(text[start : end + 1])
        i = end + 1
    return objects


def try_parse_tool_calls(raw_text: str) -> Optional[list]:
    """
    Return a list of {"tool": ..., "args": ...} dicts if the model's reply
    contains one or more tool calls, or None if it should be treated as a
    plain text answer.

    A step that needs the same tool applied once per item (e.g. a
    corrosion calculation per pipe location) is commonly answered as
    several separate JSON tool-call objects back to back in one reply --
    all of them are returned here, in order, so the caller can execute
    each one for real.
    """
    text = raw_text.strip()

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    calls = []
    for candidate in _extract_json_objects(text):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and "tool" in parsed:
            calls.append(parsed)

    return calls or None


def validate_tool_call(tool_call: dict) -> None:
    """Raise ValueError with a model-readable message if the call is invalid."""
    tool_name = tool_call.get("tool")
    if tool_name not in TOOL_REGISTRY:
        valid = ", ".join(TOOL_REGISTRY.keys())
        raise ValueError(f"Unknown tool '{tool_name}'. Valid tools: {valid}")

    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("'args' must be a JSON object")

    spec = TOOL_REGISTRY[tool_name]
    allowed = {**spec.required, **spec.optional}

    for arg_name in args:
        if arg_name not in allowed:
            raise ValueError(
                f"Unexpected arg '{arg_name}' for tool '{tool_name}'. "
                f"Allowed args: {list(allowed.keys())}"
            )

    for arg_name, expected_type in spec.required.items():
        if arg_name not in args:
            raise ValueError(f"Missing required arg '{arg_name}' for tool '{tool_name}'")
        if not isinstance(args[arg_name], expected_type):
            raise ValueError(
                f"Arg '{arg_name}' for tool '{tool_name}' must be of type "
                f"{expected_type}, got {type(args[arg_name]).__name__}"
            )

    for arg_name, expected_type in spec.optional.items():
        if arg_name in args and not isinstance(args[arg_name], expected_type):
            raise ValueError(
                f"Arg '{arg_name}' for tool '{tool_name}' must be of type "
                f"{expected_type}, got {type(args[arg_name]).__name__}"
            )


def execute_tool(tool_call: dict) -> Any:
    """Run the real Python function behind a validated tool call."""
    tool_name = tool_call["tool"]
    args = tool_call.get("args", {})
    spec = TOOL_REGISTRY[tool_name]
    return spec.function(**args)
