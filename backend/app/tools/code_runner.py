"""
Run a piece of model-written Python and report what really happened.

Preferred: the Docker sandbox (tools/sandbox.py): no network, 1 CPU, 512 MB, a throw-away folder.
If Docker is not installed or not running (a second computer, a venue laptop), the code runs in an
isolated local Python process instead: a fresh temp folder, no network (sockets are disabled before the
code starts), a wall-clock limit, and a scrubbed environment. The result says which one was used, so
nobody is told a container ran when it did not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict

from backend.app.tools.sandbox import run_code_sandbox

LOCAL_TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 6000

# Docker itself failing (as opposed to the program failing): fall back rather than blame the code.
_DOCKER_PROBLEMS = (
    "cannot connect to the docker daemon", "error during connect", "is the docker daemon running",
    "unable to find image", "pull access denied", "no such image", "docker: error response",
    "the system cannot find the file specified", "failed to connect to the docker api",
)

_docker_state: tuple[float, bool] = (0.0, False)

# Runs before the user's program in the local fallback: no sockets at all.
_RUNNER = '''
import runpy, socket, sys
def _no_network(*args, **kwargs):
    raise OSError("network access is disabled in the sandbox")
socket.socket.connect = socket.socket.connect_ex = _no_network
socket.create_connection = socket.getaddrinfo = _no_network
sys.argv = ["solution.py"]
runpy.run_path("solution.py", run_name="__main__")
'''


def docker_available() -> bool:
    """True if `docker` answers and the sandbox image exists (checked at most every 30 s)."""
    global _docker_state
    stamp, ok = _docker_state
    if time.time() - stamp < 30:
        return ok
    ok = False
    if shutil.which("docker"):
        try:
            done = subprocess.run(["docker", "image", "inspect", "python:3.11-slim"], capture_output=True, timeout=8)
            ok = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _docker_state = (time.time(), ok)
    return ok


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "\n… (output shortened)"


def _run_local(code: str) -> Dict[str, Any]:
    workspace = tempfile.mkdtemp(prefix="krypto_run_")
    try:
        with open(os.path.join(workspace, "solution.py"), "w", encoding="utf-8") as handle:
            handle.write(code)
        with open(os.path.join(workspace, "_runner.py"), "w", encoding="utf-8") as handle:
            handle.write(_RUNNER)
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(
                [sys.executable, "-I", "_runner.py"], cwd=workspace, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=LOCAL_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            )
            return {"passed": done.returncode == 0, "stdout": done.stdout, "stderr": done.stderr}
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return {"passed": False, "stdout": out,
                    "stderr": f"[sandbox] stopped after {LOCAL_TIMEOUT_SECONDS}s (the program did not finish)"}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_python(code: str) -> Dict[str, Any]:
    """{passed, stdout, stderr, isolation, isolation_detail, seconds, container_id}"""
    started = time.time()
    if docker_available():
        try:
            result = run_code_sandbox(code)
            problem = (result.get("stderr") or "").lower()
            if result.get("passed") or not any(text in problem for text in _DOCKER_PROBLEMS):
                return {
                    "passed": bool(result.get("passed")),
                    "stdout": _clip(result.get("stdout") or ""),
                    "stderr": _clip(result.get("stderr") or ""),
                    "isolation": "docker",
                    "isolation_detail": "Docker container: network disabled, 1 CPU, 512 MB, 30 s limit",
                    "seconds": round(time.time() - started, 1),
                    "container_id": result.get("container_id"),
                }
        except (OSError, subprocess.SubprocessError):
            pass
        global _docker_state
        _docker_state = (time.time(), False)  # Docker just failed: use the fallback for the next 30 s too
    result = _run_local(code)
    return {
        "passed": result["passed"],
        "stdout": _clip(result["stdout"]),
        "stderr": _clip(result["stderr"]),
        "isolation": "process",
        "isolation_detail": f"isolated local process (Docker is not available): temp folder, sockets disabled, {LOCAL_TIMEOUT_SECONDS} s limit",
        "seconds": round(time.time() - started, 1),
        "container_id": None,
    }
