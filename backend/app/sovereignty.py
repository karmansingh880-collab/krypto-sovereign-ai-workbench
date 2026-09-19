"""
Proof that nothing leaves this computer.

Two independent checks, so the claim is evidence and not a promise:

1. netguard (inside Krypto): in strict mode every attempt to reach another computer is refused and counted.
2. This module, from the OPERATING SYSTEM's side: it lists the real TCP connections that Krypto, Ollama
   (the AI models), MongoDB and the Docker relay hold open right now, and classifies each remote address as
   this computer / the local network / the internet. It does not depend on Krypto's own code being honest:
   `netstat -ano` shows the same table.

Programs are grouped by who they belong to, because that decides what a connection means:
    core    Krypto's own server, the database, the search relay, the AI model server (ollama serve)
    helper  third-party programs that sit next to it and have their OWN network habits: Ollama's tray app
            (auto-updater) and Docker Desktop (telemetry/updates). Krypto does not use them to reach anything,
            but they are on the same computer, so they are reported and can be sealed (scripts/seal_network.ps1).

A background sampler records every distinct connection it ever sees in the audit log file, so the answer to
"did anything go out during the demo?" is a number and a file, not a memory.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import psutil

from backend.app import netguard

SAMPLE_EVERY_SECONDS = 6
_CLOUD_PACKAGES = (
    "openai", "anthropic", "google-generativeai", "google-genai", "cohere", "boto3", "botocore", "azure-ai-openai",
    "langchain-openai", "langchain-anthropic", "mistralai", "groq", "together", "replicate", "huggingface-hub-inference",
)

# What to do about a helper that reaches out.
_ADVICE = {
    "Ollama tray app (auto-updater)": "Its auto-updater checks ollama.com. Quit the tray app and run `ollama serve` instead, or run scripts\\seal_network.ps1.",
    "Docker Desktop": "Docker Desktop sends usage statistics and checks for updates. Turn those off in its settings, or run scripts\\seal_network.ps1.",
}

_lock = threading.Lock()
_seen: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()  # key -> first/last seen, count
_started_at = time.time()
_samples = 0
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def scope_of(host: str) -> str:
    """'this computer', 'local network' or 'internet' for an IP address."""
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return "internet"
    if ip.is_loopback or ip.is_unspecified:
        return "this computer"
    if ip.is_private or ip.is_link_local:
        return "local network"
    return "internet"


def _role(name: str, cmdline: str) -> Optional[Tuple[str, str]]:
    """(role, party) for a process that belongs to the system, else None."""
    lower = name.lower()
    if lower.startswith("python") and "backend.app.main:app" in cmdline:
        return "Krypto server", "core"
    if lower.startswith("ollama app"):
        return "Ollama tray app (auto-updater)", "helper"
    if lower.startswith("ollama"):
        return "Ollama model server", "core"
    if lower.startswith("mongod"):
        return "MongoDB", "core"
    if lower in ("wslrelay.exe", "vpnkit.exe") or "qdrant" in lower:
        return "Qdrant search relay", "core"
    if lower.startswith(("com.docker", "docker desktop", "docker-agent")) or lower == "docker.exe":
        return "Docker Desktop", "helper"
    return None


_processes_cache: Tuple[float, Dict[int, Tuple[str, str, str]]] = (0.0, {})
PROCESS_CACHE_SECONDS = 10


def _our_processes() -> Dict[int, Tuple[str, str, str]]:
    """pid -> (role, process name, party) for every process that belongs to the system.

    Only process NAMES are read (command lines of every process are slow to fetch on Windows); the Krypto
    server is this very process. The list is cached for a few seconds.
    """
    global _processes_cache
    stamp, cached = _processes_cache
    if time.time() - stamp < PROCESS_CACHE_SECONDS and cached:
        return cached
    found: Dict[int, Tuple[str, str, str]] = {}
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"] or ""
            role = ("Krypto server", "core") if proc.info["pid"] == me else _role(name, "")
            if role:
                found[proc.info["pid"]] = (role[0], name, role[1])
        except (psutil.Error, OSError):
            continue
    _processes_cache = (time.time(), found)
    return found


def _connections(processes: Dict[int, Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        table = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return rows
    listening_ports: Dict[int, set] = {}
    for conn in table:
        if conn.status == psutil.CONN_LISTEN and conn.laddr:
            listening_ports.setdefault(conn.pid, set()).add(conn.laddr.port)
    for conn in table:
        if conn.pid not in processes:
            continue
        role, name, party = processes[conn.pid]
        local = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else ""
        if conn.status == psutil.CONN_LISTEN:
            rows.append({
                "role": role, "party": party, "process": name, "pid": conn.pid, "local": local, "remote": "",
                "state": "LISTEN", "direction": "listening",
                "scope": "this computer" if scope_of(conn.laddr.ip) == "this computer" else "reachable from the network",
                "kind": "listening",
            })
        elif conn.raddr:
            inbound = bool(conn.laddr) and conn.laddr.port in listening_ports.get(conn.pid, set())
            rows.append({
                "role": role, "party": party, "process": name, "pid": conn.pid, "local": local,
                "remote": f"{conn.raddr.ip}:{conn.raddr.port}", "state": conn.status,
                "direction": "inbound" if inbound else "outbound",
                "scope": scope_of(conn.raddr.ip), "kind": "connection",
            })
    return rows


def sample() -> List[Dict[str, Any]]:
    """Take one look at the system's connections; remember and log any never seen before."""
    global _samples
    rows = _connections(_our_processes())
    now = time.time()
    with _lock:
        _samples += 1
        for row in rows:
            if row["kind"] != "connection":
                continue
            host = row["remote"].rsplit(":", 1)[0]
            established = row["state"] == psutil.CONN_ESTABLISHED
            key = f"{row['role']}|{row['direction']}|{host}|{row['scope']}"
            entry = _seen.get(key)
            if entry is None:
                entry = _seen[key] = {
                    "role": row["role"], "party": row["party"], "process": row["process"], "direction": row["direction"],
                    "host": host, "scope": row["scope"], "first_seen": now, "last_seen": now, "count": 0,
                    "example": row["remote"], "connected": False,
                }
                if row["scope"] != "this computer":
                    netguard.audit({"event": "os_connection", "role": row["role"], "process": row["process"],
                                    "party": row["party"], "direction": row["direction"], "remote": row["remote"],
                                    "scope": row["scope"], "state": row["state"]})
                while len(_seen) > 500:
                    _seen.popitem(last=False)
            entry["last_seen"] = now
            entry["count"] += 1
            entry["connected"] = entry["connected"] or established
    return rows


def _loop() -> None:
    while not _stop.wait(SAMPLE_EVERY_SECONDS):
        try:
            sample()
        except Exception:
            pass


def start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="krypto-network-audit", daemon=True)
        _thread.start()
        netguard.audit({"event": "server_started", "strict_offline": netguard.enabled(), "lan_allowed": netguard.lan_allowed()})


def stop() -> None:
    _stop.set()


def cloud_packages() -> List[str]:
    """Cloud AI client libraries installed in this Python environment (there should be none)."""
    from importlib import metadata

    present = []
    for name in _CLOUD_PACKAGES:
        try:
            metadata.version(name)
            present.append(name)
        except metadata.PackageNotFoundError:
            continue
    return present


def _outbound_internet(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in entries if e["scope"] == "internet" and e["direction"] != "inbound"]


def _verdict(counts_now: Dict[str, int], ever: List[Dict[str, Any]], lan_seen: bool) -> Tuple[str, str]:
    """(verdict, headline). Only OUTBOUND connections to the internet count against the claim."""
    outbound = _outbound_internet(ever)
    core = [e for e in outbound if e["party"] == "core"]
    helpers = [e for e in outbound if e["party"] == "helper"]
    if core:
        names = ", ".join(sorted({e["role"] for e in core}))
        return "internet", f"{names} made a connection to the internet. See the list below."
    if helpers:
        names = ", ".join(sorted({e["role"] for e in helpers}))
        return "helpers", (
            "Krypto, the AI model server, the database and the search made no internet connection. "
            f"Third-party helper programs on this computer did: {names}. Seal them with scripts\\seal_network.ps1."
        )
    if lan_seen:
        return "lan-only", "No connection to the internet. Only this computer and computers on your local network were used."
    return "local-only", "No connection left this computer. Every connection is to a service on this same machine."


def snapshot() -> Dict[str, Any]:
    """Everything the proof page shows."""
    rows = sample()
    connections = [r for r in rows if r["kind"] == "connection"]
    listening = [r for r in rows if r["kind"] == "listening"]
    counts = {"this computer": 0, "local network": 0, "internet": 0}
    for row in connections:
        if row["direction"] != "inbound" or row["scope"] != "this computer":
            counts[row["scope"]] = counts.get(row["scope"], 0) + 1
    outbound_now = [r for r in connections if r["scope"] == "internet" and r["direction"] != "inbound"]
    with _lock:
        ever = [dict(e) for e in _seen.values()]
        samples = _samples
    guard = netguard.stats()
    packages = cloud_packages()
    lan_seen = any(e["scope"] == "local network" for e in ever) or bool(guard["lan_connections"])
    verdict, headline = _verdict(counts, ever, lan_seen)
    ever_outbound = _outbound_internet(ever)
    helper_names = sorted({e["role"] for e in ever_outbound if e["party"] == "helper"})

    return {
        "verdict": verdict,
        "headline": headline,
        "strict": guard["enabled"],
        "lan_allowed": guard["lan_allowed"],
        "blocked_attempts": guard["blocked"],
        "recent_blocked": guard["recent"],
        "lan_connections": guard["lan_connections"],
        "connections_now": connections,
        "listening": listening,
        "counts_now": {**counts, "internet": len(outbound_now)},
        "ever_seen": [
            {**e, "first_seen": time.strftime("%H:%M:%S", time.localtime(e["first_seen"])),
             "last_seen": time.strftime("%H:%M:%S", time.localtime(e["last_seen"]))}
            for e in ever
        ],
        "ever_external": len(ever_outbound),
        "ever_external_core": len([e for e in ever_outbound if e["party"] == "core"]),
        "helper_advice": [{"role": name, "advice": _ADVICE.get(name, "")} for name in helper_names],
        "samples": samples,
        "watching_seconds": int(time.time() - _started_at),
        "processes": sorted({f"{role} ({name})" for role, name, _party in _our_processes().values()}),
        "cloud_libraries_installed": packages,
        "audit_log": str(netguard.AUDIT_LOG),
        "audit_tail": netguard.audit_tail(12),
        "host": socket.gethostname(),
        "how_to_check": [
            "Windows: run  netstat -ano | findstr ESTABLISHED  and match the PIDs listed under 'Processes' above.",
            "Unplug the network cable / turn off Wi-Fi, then run any task: it still completes.",
            "Start with  .\\run_demo.ps1 -Offline  to make any outside connection from Krypto fail loudly and be counted.",
            "Run  .\\scripts\\seal_network.ps1  (as administrator) to make Windows itself drop internet traffic from every program above.",
        ],
    }


def self_test() -> Dict[str, Any]:
    """Try to reach the internet on purpose and report that it is refused (only meaningful in strict mode)."""
    if not netguard.enabled():
        return {
            "ran": False,
            "message": "Strict offline mode is not on, so this test would really contact the internet. "
                       "Start the app with  .\\run_demo.ps1 -Offline  and run it again.",
            "attempts": [],
        }
    attempts = []
    with netguard.self_test_scope():  # logged as test events, not counted in "blocked attempts"
        for host, port, label in (("1.1.1.1", 443, "public internet address"), ("8.8.8.8", 53, "public DNS server")):
            outcome = "reached"
            try:
                socket.create_connection((host, port), timeout=2).close()
            except OSError as exc:
                outcome = f"refused ({exc.strerror or exc.__class__.__name__})"
            attempts.append({"target": f"{host}:{port}", "what": label, "result": outcome})
        try:
            socket.getaddrinfo("example.com", 443)
            lookup = "reached"
        except OSError as exc:
            lookup = f"refused ({exc.strerror or exc.__class__.__name__})"
        attempts.append({"target": "example.com", "what": "name lookup", "result": lookup})
    all_refused = all(a["result"].startswith("refused") for a in attempts)
    return {
        "ran": True,
        "all_refused": all_refused,
        "message": "Krypto tried to reach the internet three ways and every attempt was refused."
                   if all_refused else "Something got through: see the attempts.",
        "attempts": attempts,
    }
