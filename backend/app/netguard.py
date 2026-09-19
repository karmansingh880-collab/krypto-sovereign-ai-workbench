"""
Strict offline mode.

Krypto is built to run entirely on this computer: the AI models, OCR, database and search
are all local. With `KRYPTO_OFFLINE=1` this module ENFORCES that: any attempt to reach a
machine other than this one (an internet site, a name lookup) is refused and recorded, so
"works without a network" is something you can check, not just believe.

Only loopback addresses (127.x, ::1, localhost) and local pipes are allowed. With
`KRYPTO_ALLOW_LAN=1` (two computers joined by an ethernet cable or switch) addresses on a PRIVATE
network (192.168.x.x, 10.x.x.x, 172.16-31.x.x, 169.254.x.x) are allowed as well and are listed
separately; anything on the internet is still refused. Every refused and every LAN connection is
also written to an audit log file. Connections made through the Windows async engine's low-level
API can bypass this guard, but the AI, database and download libraries all go through the patched
calls below -- and sovereignty.py checks the same thing from the operating system's side.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List

_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}

_enabled = False
_installed = False
_lock = threading.Lock()
_blocked_total = 0
_recent: Deque[Dict[str, Any]] = deque(maxlen=25)
_lan_seen: Dict[str, Dict[str, Any]] = {}

AUDIT_LOG = Path(os.environ.get("KRYPTO_AUDIT_LOG") or Path.home() / ".krypto_output" / "network_audit.log")


def lan_allowed() -> bool:
    return os.environ.get("KRYPTO_ALLOW_LAN", "").strip().lower() in ("1", "true", "yes", "on")


def audit(event: Dict[str, Any]) -> None:
    """Append one line to the audit log (best effort: a full disk must never break a request)."""
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **event}, default=str)
        with _lock, open(AUDIT_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def audit_tail(lines: int = 15) -> List[str]:
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in handle.readlines()[-lines:]]
    except OSError:
        return []


def enabled() -> bool:
    return _enabled


def requested() -> bool:
    return os.environ.get("KRYPTO_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def stats() -> Dict[str, Any]:
    with _lock:
        return {"enabled": _enabled, "blocked": _blocked_total, "recent": list(_recent),
                "lan_allowed": lan_allowed(), "lan_connections": list(_lan_seen.values())}


def harden_environment() -> None:
    """Stop libraries from probing or phoning home. Always safe: nothing here needs the network."""
    for name, value in {
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",  # PaddleOCR otherwise probes model hosts on start-up
        "DO_NOT_TRACK": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
    }.items():
        os.environ.setdefault(name, value)
    if requested():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _is_local_host(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    text = str(host).strip().strip("[]")
    if text == "" or text.lower() in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False  # a name that would have to be looked up on a network
    if ip.is_loopback or ip.is_unspecified:
        return True
    return lan_allowed() and (ip.is_private or ip.is_link_local)


def _is_loopback(host: Any) -> bool:
    text = str(host).strip().strip("[]") if host is not None else ""
    if text.lower() in _LOCAL_NAMES or text == "":
        return True
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _note_lan(host: Any, port: Any) -> None:
    """A connection to another computer on the private network (only possible when LAN is allowed)."""
    key = f"{host}:{port}"
    with _lock:
        entry = _lan_seen.get(key)
        first = entry is None
        if first:
            entry = _lan_seen[key] = {"host": str(host), "port": port, "count": 0, "caller": _caller()}
        entry["count"] += 1
    if first:
        audit({"event": "lan_connection", "host": str(host), "port": port, "caller": entry["caller"]})


def _host_of(address: Any) -> Any:
    """The host part of a socket address, or None for non-network addresses (local pipes)."""
    return address[0] if isinstance(address, tuple) and address else None


def _caller() -> str:
    """The first stack frame outside the standard library and network plumbing: who tried."""
    skip = ("socket.py", "netguard.py", "urllib3", "httpx", "httpcore", "anyio", "asyncio", "http\\client.py",
            "http/client.py", "threading.py", "concurrent")
    for frame in reversed(traceback.extract_stack(limit=25)):
        if not any(part in frame.filename for part in skip):
            name = frame.filename.replace("\\", "/").split("site-packages/")[-1]
            return f"{name}:{frame.lineno}"
    return "unknown"


_quiet = threading.local()


class self_test_scope:
    """Inside this block refused connections are logged as self-test events and NOT counted as attempts."""

    def __enter__(self) -> "self_test_scope":
        _quiet.on = True
        return self

    def __exit__(self, *exc: Any) -> None:
        _quiet.on = False


def _record(host: Any, port: Any) -> None:
    global _blocked_total
    if getattr(_quiet, "on", False):
        audit({"event": "self_test_refused", "host": str(host), "port": port})
        return
    entry = {"time": time.strftime("%H:%M:%S"), "host": str(host), "port": port, "caller": _caller()}
    with _lock:
        _blocked_total += 1
        _recent.append(entry)
    print(f"[OFFLINE] blocked connection to {host}:{port} (from {entry['caller']})", file=sys.stderr, flush=True)
    audit({"event": "blocked", "host": str(host), "port": port, "caller": entry["caller"]})


def install() -> None:
    """Patch the socket calls. Idempotent."""
    global _enabled, _installed
    _enabled = True
    if _installed:
        return
    _installed = True

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = _host_of(address)
        if host is not None and not _is_local_host(host):
            _record(host, address[1] if len(address) > 1 else None)
            raise OSError(errno.ENETUNREACH, "Blocked by Krypto offline mode: only this computer can be reached")
        if host is not None and not _is_loopback(host):
            _note_lan(host, address[1] if len(address) > 1 else None)
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        host = _host_of(address)
        if host is not None and not _is_local_host(host):
            _record(host, address[1] if len(address) > 1 else None)
            return errno.ENETUNREACH
        if host is not None and not _is_loopback(host):
            _note_lan(host, address[1] if len(address) > 1 else None)
        return original_connect_ex(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> List[Any]:
        if host is not None and not _is_local_host(host):
            _record(host, args[0] if args else None)
            raise socket.gaierror(socket.EAI_NONAME, "Blocked by Krypto offline mode")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]


def install_if_requested() -> None:
    harden_environment()
    if requested():
        install()
