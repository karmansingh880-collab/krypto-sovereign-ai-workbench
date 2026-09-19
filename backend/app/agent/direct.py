"""
Direct answers: questions and code requests that come with no document.

With a small local model, planning such a request as a multi-step tool-calling loop goes wrong (it
invents steps, mangles tool calls and never answers). Here the model is asked for the thing itself:

    question -> the model answers, streamed as it is written
    code     -> the model writes Python -> it is RUN in the sandbox -> if it fails, the model sees the
                real error and fixes it (up to MAX_REPAIRS times) -> the answer is the code, the real
                output, and whether it passed

Each step records which model the router picked and why, so the UI can show automatic model selection.
Used only in demo mode with no attached file; everything else keeps the original agent loop.
"""

from __future__ import annotations

import ast
import re
import time
from typing import Any, Dict, List, Optional

from backend.app import live
from backend.app.agent.state import AgentState, Observation
from backend.app.llm import chat as llm_chat
from backend.app.router.router import RoutingDecision, demo_model, describe_route, route_task, route_to, specialist_model
from backend.app.tools.code_runner import run_python

MAX_REPAIRS = 2
CODE_TOKENS = 600
CHAT_TOKENS = 260
LONG_CHAT_TOKENS = 520

_CODE_NOUN = re.compile(r"\b(code|program|programme|script|function|algorithm|snippet|python|method|class)\b", re.I)
_ASK_TO_WRITE = re.compile(
    r"\b(write|create|make|generate|implement|build|develop|give|show|need|want|fix|debug|solve|"
    r"(?:code|program|programme|script|function|algorithm)\s+(?:for|to|that|which))\b", re.I)
_STARTS_WITH_NOUN = re.compile(
    r"^\s*(?:a |an |the |some )?(?:simple |basic |small |short |easy |sample |example )*(?:python )?"
    r"(?:code|program|script|function)\b", re.I)
_EXPLAIN_ONLY = re.compile(r"^\s*(?:what|why|how does|how do|explain|define|difference|when|who|which)\b", re.I)
_WRITE_VERB = re.compile(r"\b(write|create|make|generate|implement|build|develop)\b", re.I)
_OTHER_LANGUAGE = re.compile(r"\b(javascript|typescript|java|c\+\+|c#|golang|rust|php|ruby|kotlin|swift|bash|powershell|html|css)\b", re.I)
_PYTHON = re.compile(r"\b(python|py)\b", re.I)
_WANTS_LONG = re.compile(r"\b(explain|describe|detail|detailed|compare|difference|steps|guide|essay|tutorial|why|how does)\b", re.I)
_FENCE = re.compile(r"```[ \t]*(?:python|py|python3)?[ \t]*\r?\n(.*?)```", re.I | re.S)
_OPEN_FENCE = re.compile(r"```[ \t]*(?:python|py|python3)?[ \t]*\r?\n(.*)$", re.I | re.S)

SYSTEM = (
    "You are Krypto, a helpful assistant that runs on the user's own computer. Answer the question directly and "
    "correctly in a few sentences. Always give your best answer; for a figure you are not sure of, say 'about' and "
    "that it is approximate. Do not talk about the internet, browsing or your knowledge cutoff."
)

# A follow-up that asks to change the code from the previous answer ("now make it handle a list").
_EDITS_EARLIER_CODE = re.compile(
    r"\b(make|change|modify|add|update|fix|improve|rewrite|refactor|extend|handle|support|now|also|instead|convert|"
    r"optimi[sz]e|simplify|use|print|return|remove|rename)\b", re.I)


def _earlier_answer_had_code(earlier: str) -> bool:
    return "```" in earlier or "run in a sandbox" in earlier.lower()


def kind_of(goal: str, earlier: str = "") -> str:
    """'code' (write and verify a program), 'code-other' (code in a language the sandbox cannot run) or 'chat'.

    `earlier` is the question and answer this one follows up on: "now make it print the primes" is a code
    request only because the answer before it was code.
    """
    kind = _kind_of_request(goal)
    if kind == "chat" and earlier and _earlier_answer_had_code(earlier) and _EDITS_EARLIER_CODE.search(goal) \
            and not _EXPLAIN_ONLY.match(goal):
        return "code-other" if _OTHER_LANGUAGE.search(goal) and not _PYTHON.search(goal) else "code"
    return kind


def _kind_of_request(goal: str) -> str:
    decision = route_task(goal)
    if decision.model_key != "coder" and not _CODE_NOUN.search(goal):
        return "chat"
    if _EXPLAIN_ONLY.match(goal) and not _WRITE_VERB.search(goal):
        return "chat"  # "what is a python decorator?" is a question, not a request for a program
    if not (_ASK_TO_WRITE.search(goal) or _STARTS_WITH_NOUN.match(goal)):
        return "chat"
    if _OTHER_LANGUAGE.search(goal) and not _PYTHON.search(goal):
        return "code-other"
    return "code"


def applies(state: AgentState) -> bool:
    """Demo mode, nothing attached, and not the approval-note workflow."""
    return bool(demo_model()) and not state["files"] and "approval note" not in state["goal"].lower()


def extract_code(reply: str) -> str:
    """The Python in a model reply: the first fenced block (also one cut off before its closing fence), else the raw text."""
    match = _FENCE.search(reply)
    if match:
        return match.group(1).strip("\n")
    match = _OPEN_FENCE.search(reply)
    if match:
        return match.group(1).strip("\n")
    return reply.strip() if re.search(r"^\s*(def |import |from |print\(|class |for |while )", reply, re.M) else ""


def _intro_of(reply: str) -> str:
    """The sentence the model wrote before its code block (kept if short)."""
    head = reply.split("```", 1)[0].strip()
    head = " ".join(head.split())
    return head if 0 < len(head) <= 300 else ""


def _observation(step: str, route: Optional[Dict[str, Any]] = None) -> Observation:
    return {
        "step": step, "model_used": route["task_type"] if route else "none",
        "model_tag": route["model"] if route else "none", "reason": route["reason"] if route else "",
        "tool_used": None, "tool_args": None, "response": "", "result": None, "error": None,
        "route": route,
    }  # type: ignore[typeddict-item]


def _route_for(goal: str, kind: str = "chat") -> tuple[RoutingDecision, str, Dict[str, Any]]:
    """The router's pick for this request and the model that will actually run it."""
    decision = route_task(goal)
    if kind.startswith("code") and decision.model_key != "coder":
        decision = route_to("coder", "the request asks for a program")
    tag = specialist_model(decision.model_key) or decision.model_tag
    return decision, tag, describe_route(decision, tag)


def _options() -> Dict[str, Any]:
    return {"num_ctx": 4096}


# Code replies end at the closing fence: anything after it is chatter that costs seconds on a CPU.
_CODE_STOP = ["\n```\n", "\n```\r\n"]


def _ask(tag: str, prompt: str, max_tokens: int, stream: bool = True, temperature: float = 0.2,
         stop: Optional[List[str]] = None) -> str:
    """One model call. A low temperature keeps code and facts steady; repairs use a higher one to try something different."""
    options: Dict[str, Any] = {**_options(), "temperature": temperature}
    if stop:
        options["stop"] = stop
    return llm_chat(tag, prompt, options=options, max_tokens=max_tokens, stream_to_ui=stream, system=SYSTEM).strip()


_EXAMPLE = (
    "This program adds two numbers and checks the result.\n"
    "```python\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "print(add(2, 3))\n"
    "assert add(2, 3) == 5\n"
    "assert add(-1, 1) == 0\n"
    "print('All checks passed')\n"
    "```"
)


def _code_prompt(goal: str, earlier: str) -> str:
    return (
        "You write Python 3 programs. Reply in exactly this format: one sentence, then one ```python code block.\n\n"
        f"Example request: add two numbers\nExample reply:\n{_EXAMPLE}\n\n"
        + (f"Earlier in this conversation:\n{earlier}\n\n" if earlier else "")
        + f"Real request: {goal}\n"
        "(If the request is vague, choose a small, useful example yourself.)\n"
        "Rules: standard library only; never call input(); print the results; finish with a few assert checks "
        "and print('All checks passed').\n"
        "Reply:"
    )


def _repair_prompt(goal: str, code: str, result: Dict[str, Any]) -> str:
    error = (result.get("stderr") or result.get("stdout") or "no output").strip()[-1500:]
    return (
        f"This Python program was written for the request: {goal}\n\n```python\n{code}\n```\n\n"
        f"When it was run it FAILED with:\n{error}\n\n"
        "The mistake may be in the program OR in the expected values of the assert checks (work them out by hand "
        "before you trust them). Fix whichever is wrong so that it runs and its assert checks pass. "
        "Use only the standard library and never call input(). "
        "Reply with ONE short sentence on what was wrong, then ONE corrected ```python code block."
    )


def _run_report(result: Dict[str, Any]) -> str:
    lines = [f"passed: {result['passed']}", f"isolation: {result['isolation_detail']}", f"time: {result['seconds']} s"]
    if result.get("container_id"):
        lines.append(f"container: {result['container_id'][:12]}")
    if result.get("stdout"):
        lines.append("--- output ---\n" + result["stdout"].rstrip())
    if result.get("stderr"):
        lines.append("--- errors ---\n" + result["stderr"].rstrip())
    return "\n".join(lines)


def without_asserts(code: str) -> str:
    """The program with its top-level assert statements removed ("" if it cannot be parsed or has none)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    def _passed_banner(node: ast.stmt) -> bool:  # print('All checks passed') would be false once the checks are gone
        return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and node.value.args
                and isinstance(node.value.args[0], ast.Constant) and isinstance(node.value.args[0].value, str)
                and "passed" in node.value.args[0].value.lower())

    kept = [node for node in tree.body if not isinstance(node, ast.Assert) and not _passed_banner(node)]
    if not any(isinstance(node, ast.Assert) for node in tree.body):
        return ""
    tree.body = kept or [ast.Pass()]
    try:
        return ast.unparse(tree)
    except Exception:
        return ""


def _final_code_answer(intro: str, code: str, result: Dict[str, Any], attempts: int, checks_dropped: bool = False) -> str:
    verified = result["passed"] and not checks_dropped
    if verified:
        head = "**Verified:** this code was run in a sandbox and passed" + (
            f" (after {attempts - 1} automatic fix{'es' if attempts > 2 else ''})." if attempts > 1 else ".")
    elif result["passed"]:
        head = ("**Runs, but not fully verified:** the program ran without errors and printed the output below, but the "
                "model's own test values were wrong, so those checks were removed. Please check the output yourself.")
    else:
        head = "**Not verified:** the code below did not run cleanly in the sandbox, so check it before you use it."
    parts = [head, ""]
    if intro:
        parts += [intro, ""]
    parts += ["```python", code.rstrip(), "```", ""]
    if result.get("stdout", "").strip():
        parts += ["**Output**", "```", result["stdout"].rstrip(), "```", ""]
    if not result["passed"] and (result.get("stderr") or "").strip():
        parts += ["**Error**", "```", result["stderr"].rstrip()[-1200:], "```", ""]
    parts.append(f"*Sandbox: {result['isolation_detail']}; {result['seconds']} s.*")
    return "\n".join(parts)


def _answer_chat(state: AgentState, decision: RoutingDecision, tag: str, route: Dict[str, Any]) -> None:
    goal = state["goal"]
    long = bool(_WANTS_LONG.search(goal)) or len(goal) > 300
    earlier = live.conversation()
    prompt = (f"Earlier in this conversation:\n{earlier}\n\n" if earlier else "") + f"Question: {goal}\n\nAnswer:"
    obs = _observation("Answer the question", route)
    live.start("answer", "Writing the answer", f"{route['model']} is writing the reply on this computer")
    try:
        obs["response"] = _ask(tag, prompt, LONG_CHAT_TOKENS if long else CHAT_TOKENS)
        live.done("answer")
    except Exception as exc:
        live.fail("answer", str(exc))
        obs["error"] = str(exc)
    state["observations"].append(obs)


def _answer_code_other_language(state: AgentState, decision: RoutingDecision, tag: str, route: Dict[str, Any]) -> None:
    goal = state["goal"]
    obs = _observation("Write the code", route)
    live.start("write", "Writing the code", f"{route['model']} is writing the code")
    try:
        reply = _ask(tag, f"{goal}\n\nGive the code in one fenced code block and at most two sentences of explanation.", CODE_TOKENS)
        live.done("write")
        obs["response"] = reply + (
            "\n\n*Not run: the sandbox executes Python only, so code in another language is shown without being run.*")
    except Exception as exc:
        live.fail("write", str(exc))
        obs["error"] = str(exc)
    state["observations"].append(obs)


def _answer_code(state: AgentState, decision: RoutingDecision, tag: str, route: Dict[str, Any]) -> None:
    goal = state["goal"]
    earlier = live.conversation()
    write = _observation("Write the Python code", route)
    live.start("write", "Writing the code", f"{route['model']} is writing the program")
    try:
        reply = _ask(tag, _code_prompt(goal, earlier), CODE_TOKENS, stop=_CODE_STOP)
    except Exception as exc:
        live.fail("write", str(exc))
        write["error"] = str(exc)
        state["observations"].append(write)
        return
    code = extract_code(reply)
    # Up to two more tries when the model answers in prose: the same model with the prompt ending inside a code
    # fence, then (in demo mode) the larger general model.
    fallbacks = [tag] + ([demo_model()] if demo_model() and demo_model() != tag else [])
    for retry_tag in fallbacks:
        if code:
            break
        live.update("write", "The model did not give code; asking again")
        live.reset_text()
        try:
            reply = _ask(retry_tag, _code_prompt(goal, earlier) + "\n```python\n", CODE_TOKENS, stop=_CODE_STOP)
            # The prompt ended inside a code fence, so the reply may be bare code (with no fences of its own).
            code = extract_code(reply) or (extract_code("```python\n" + reply) if _LOOKS_LIKE_CODE.search(reply) else "")
        except Exception:
            pass
    intro = _intro_of(reply)
    write["response"] = reply
    live.done("write", f"{len(code.splitlines())} lines" if code else "no code found")
    state["observations"].append(write)
    if not code:
        # The model answered in prose. Show that as the answer rather than failing.
        answer = _observation("Answer the request", route)
        answer["response"] = reply
        state["observations"].append(answer)
        return

    result: Dict[str, Any] = {}
    attempts = 0
    big = demo_model() or tag
    repair_models = [tag] + ([big] if big != tag else [tag])  # the specialist first, then the larger general model
    while True:
        attempts += 1
        key = "run" if attempts == 1 else f"run:{attempts}"
        live.start(key, "Running the code in the sandbox" if attempts == 1 else f"Running the fixed code (attempt {attempts})",
                   "Docker container with the network switched off")
        step = _observation("Run the code in the sandbox" if attempts == 1 else f"Run the fixed code (attempt {attempts})")
        step.update({"tool_used": "run_code_sandbox", "tool_args": {"code": code[:2000]}})
        result = run_python(code)
        step["response"] = _run_report(result)
        step["result"] = {k: v for k, v in result.items() if k != "stdout"}
        live.done(key, "Passed" if result["passed"] else "Failed: " + ((result.get("stderr") or "").strip().splitlines() or ["no output"])[-1][:120])
        state["observations"].append(step)
        if result["passed"] or attempts > MAX_REPAIRS:
            break

        fixed = ""
        for fix_tag in repair_models[attempts - 1:]:
            fix_key = f"fix:{attempts}:{fix_tag}"
            live.start(fix_key, f"Fixing the code (attempt {attempts + 1})", f"{fix_tag} reads the real error and rewrites the code")
            fix = _observation(f"Fix the code using the error (attempt {attempts + 1})", route)
            try:
                live.reset_text()
                fix_reply = _ask(fix_tag, _repair_prompt(goal, code, result), CODE_TOKENS, temperature=0.5, stop=_CODE_STOP)
                fixed = extract_code(fix_reply)
                fix["response"] = fix_reply
                fix["model_tag"] = fix_tag
                live.done(fix_key, f"{len(fixed.splitlines())} lines" if fixed else "no code found")
            except Exception as exc:
                live.fail(fix_key, str(exc))
                fix["error"] = str(exc)
            state["observations"].append(fix)
            if fixed and fixed.strip() != code.strip():
                break
            fixed = ""  # nothing new: let the next (larger) model try
        if not fixed:
            break
        code = fixed

    checks_dropped = False
    if not result.get("passed") and "AssertionError" in (result.get("stderr") or ""):
        # The program itself may be fine and only the model's own expected values wrong (small models are bad at
        # working those out). Run it once without its asserts; the answer says clearly that this happened.
        relaxed = without_asserts(code)
        if relaxed:
            live.start("relaxed", "Running again without the faulty self-checks", "The program may be right and its test values wrong")
            step = _observation("Run the code again without its own (faulty) assert checks")
            step.update({"tool_used": "run_code_sandbox", "tool_args": {"code": relaxed[:2000]}})
            second = run_python(relaxed)
            step["response"] = _run_report(second)
            step["result"] = {k: v for k, v in second.items() if k != "stdout"}
            state["observations"].append(step)
            live.done("relaxed", "Ran cleanly" if second["passed"] else "Still failing")
            if second["passed"]:
                code, result, checks_dropped = relaxed, second, True

    final = _observation("Answer the request", route)
    live.start("answer", "Preparing the result")
    final["response"] = _final_code_answer(intro, code, result, attempts, checks_dropped)
    final["result"] = {
        "verified": bool(result.get("passed")) and not checks_dropped,
        "ran": bool(result.get("passed")), "checks_removed": checks_dropped,
        "attempts": attempts, "isolation": result.get("isolation"),
    }
    state["observations"].append(final)
    live.set_extra("code_check", final["result"])
    live.done("answer", "Verified" if final["result"]["verified"] else ("Ran, checks removed" if checks_dropped else "Not verified"))


def announce_plan(goal: str) -> None:
    kind = kind_of(goal, live.conversation())
    if kind == "code":
        items = [("write", "Writing the code"), ("run", "Running the code in the sandbox"), ("answer", "Preparing the result")]
    elif kind == "code-other":
        items = [("write", "Writing the code")]
    else:
        items = [("answer", "Writing the answer")]
    live.plan([("route", "Choosing the best local model for this task")] + items)


def handle(state: AgentState) -> bool:
    """Answer `state["goal"]` directly. Returns True when it did (the state is then complete)."""
    goal = state["goal"]
    kind = kind_of(goal, live.conversation())
    live.start("route", "Choosing the best local model for this task")
    decision, tag, route = _route_for(goal, kind)
    live.done("route", f"{route['task_type']} task -> {route['model']}" + (f" (design calls for {route['designed_model']})" if route["substituted"] else ""))
    print(f"[DIRECT] {kind} request -> router picked '{decision.model_key}' ({decision.reason}); running on {tag}")

    routed = _observation("Choose the model for this task", route)
    routed["response"] = (
        f"Task type: {route['task_type']} ({route['reason']}).\n"
        f"Model the design calls for: {route['designed_model']}.\n"
        f"Model used on this computer: {route['model']}"
        + (" (a smaller stand-in, because this computer has no GPU)." if route["substituted"] else ".")
    )
    routed["tool_used"] = "route_task"
    routed["tool_args"] = {"task": goal[:200]}
    routed["result"] = route
    state["observations"].append(routed)

    started = time.time()
    if kind == "code":
        _answer_code(state, decision, tag, route)
    elif kind == "code-other":
        _answer_code_other_language(state, decision, tag, route)
    else:
        _answer_chat(state, decision, tag, route)
    print(f"[DIRECT] finished in {time.time() - started:.1f}s")

    state["plan"] = [o["step"] for o in state["observations"]]
    state["current_step"] = len(state["plan"])
    state["outputs"] = [o["response"] for o in state["observations"] if o.get("response") and not o.get("error")]
    return True
