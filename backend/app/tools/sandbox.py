"""
Code sandbox tool: run untrusted, model-written Python inside an isolated
Docker container.

Isolation:
    --network none    no internet access, can't reach the host or anything else
    --cpus 1          bounded CPU
    --memory 512m     bounded memory
    -v workspace:/workspace  the ONLY filesystem the container can touch is
                              its own throwaway temp dir, mounted read-write
    subprocess timeout  the container is force-killed if it runs too long

Uses the stock `python:3.11-slim` image -- no custom image build.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from typing import Optional, TypedDict

DOCKER_IMAGE = "python:3.11-slim"
TIMEOUT_SECONDS = 30
CPU_LIMIT = "1"
MEMORY_LIMIT = "512m"

# On macOS with Colima (no Docker Desktop here), the Docker daemon runs
# inside a Linux VM that only bind-mounts the host home directory by
# default -- NOT the OS temp dir (/var/folders/... on macOS). A workspace
# created with the default tempfile location would look empty to the
# container even though it exists on the host. Keeping the sandbox
# workspace under the home directory works with Docker Desktop too.
SANDBOX_BASE_DIR = os.path.expanduser("~/.krypto_sandbox")


class SandboxResult(TypedDict):
    passed: bool
    stdout: str
    stderr: str
    output_files: list
    container_id: Optional[str]  # for verifying this was a real container run
    workspace_dir: str


def run_code_sandbox(code: str, tests: Optional[str] = None) -> SandboxResult:
    """
    Run `code` (and optionally `tests` against it) inside an isolated,
    network-less Docker container.

    Convention: `code` is saved as solution.py. If `tests` is given, it's
    saved as test_solution.py and should `from solution import ...` and use
    plain `assert` statements (no pytest -- the container has no network to
    install it, and none is baked into python:3.11-slim).
    """
    os.makedirs(SANDBOX_BASE_DIR, exist_ok=True)
    workspace = tempfile.mkdtemp(prefix="run_", dir=SANDBOX_BASE_DIR)
    container_name = f"krypto-sandbox-{uuid.uuid4().hex[:12]}"

    with open(os.path.join(workspace, "solution.py"), "w") as f:
        f.write(code)

    if tests:
        with open(os.path.join(workspace, "test_solution.py"), "w") as f:
            f.write(tests)
        entrypoint = "test_solution.py"
    else:
        entrypoint = "solution.py"

    cmd = [
        "docker", "run",
        "--name", container_name,
        "--network", "none",
        "--cpus", CPU_LIMIT,
        "--memory", MEMORY_LIMIT,
        "--pids-limit", "128",
        "-v", f"{workspace}:/workspace:rw",
        "-w", "/workspace",
        DOCKER_IMAGE,
        "python", entrypoint,
    ]

    timed_out = False
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        # subprocess's own timeout only kills the `docker run` CLI client --
        # the container itself keeps running in the daemon unless we kill it
        # by name explicitly.
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
        stderr += f"\n[sandbox] killed after exceeding {TIMEOUT_SECONDS}s timeout"
        returncode = -1
        timed_out = True

    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", container_name],
        capture_output=True, text=True,
    )
    container_id = inspect.stdout.strip() if inspect.returncode == 0 else None

    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    output_files = [
        name for name in os.listdir(workspace)
        if name not in ("solution.py", "test_solution.py", "__pycache__")
    ]

    return SandboxResult(
        passed=(returncode == 0) and not timed_out,
        stdout=stdout,
        stderr=stderr,
        output_files=output_files,
        container_id=container_id,
        workspace_dir=workspace,
    )
