"""
Is this computer online? Krypto never needs the internet; this only tells you the state
(so you can see it working offline). The check is a quick TCP connection to well-known
public addresses -- nothing is sent -- and, on Windows, the Wi-Fi state.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from backend.app import netguard

PROBES = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)]
PROBE_TIMEOUT = 1.2
CHECK_EVERY_SECONDS = 8
HEARTBEAT_SECONDS = 20


def probe_internet(timeout: float = PROBE_TIMEOUT, probes: Optional[List[tuple]] = None) -> Optional[int]:
    """Milliseconds to reach a public address, or None if none can be reached."""
    for host, port in probes or PROBES:
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return max(1, int((time.perf_counter() - started) * 1000))
        except OSError:
            continue
    return None


_wifi_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def wifi_info() -> Optional[Dict[str, Any]]:
    """Wi-Fi state from Windows (`netsh`), or None where that is not available."""
    if sys.platform != "win32":
        return None
    if time.time() - _wifi_cache["at"] < 6:
        return _wifi_cache["value"]
    value: Optional[Dict[str, Any]] = None
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        state = re.search(r"^\s*State\s*:\s*(.+)$", out, re.M)
        if state:
            ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
            signal = re.search(r"^\s*Signal\s*:\s*(.+)$", out, re.M)
            value = {
                "connected": state.group(1).strip().lower() == "connected",
                "ssid": ssid.group(1).strip() if ssid else None,
                "signal": signal.group(1).strip() if signal else None,
            }
    except (OSError, subprocess.SubprocessError):
        value = None
    _wifi_cache.update(at=time.time(), value=value)
    return value


_SKIP_ADAPTERS = ("loopback", "vethernet", "virtual", "vmware", "vbox", "bluetooth", "docker", "wsl", "hyper-v",
                  "tap-", "tailscale", "zerotier", "pseudo", "npcap", "isatap", "teredo", "connection*")


def link_info() -> Dict[str, Any]:
    """Physical network adapters that are connected: ethernet and/or Wi-Fi, with their addresses.

    `lan_urls` are the addresses another computer on the same cable/switch/network can open.
    """
    try:
        import psutil

        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
    except Exception:
        return {"kind": "unknown", "adapters": [], "lan_urls": []}
    port = os.environ.get("KRYPTO_PORT", "8010")
    adapters: List[Dict[str, Any]] = []
    for name, st in stats.items():
        low = name.lower()
        if any(skip in low for skip in _SKIP_ADAPTERS):
            continue
        ipv4 = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET and not a.address.startswith("127.")]
        if any(k in low for k in ("wi-fi", "wifi", "wlan", "wireless")):
            kind = "wifi"
        elif low.startswith(("ethernet", "eth", "en", "local area")) or "ethernet" in low or "lan" in low:
            kind = "ethernet"
        else:
            kind = "other"
        adapters.append({"name": name, "kind": kind, "up": bool(st.isup and ipv4), "speed_mbps": st.speed or None, "ipv4": ipv4})
    connected = [a for a in adapters if a["up"]]
    has_eth = any(a["kind"] == "ethernet" for a in connected)
    has_wifi = any(a["kind"] == "wifi" for a in connected)
    kind = "both" if has_eth and has_wifi else "ethernet" if has_eth else "wifi" if has_wifi else "none"
    ordered = sorted(connected, key=lambda a: 0 if a["kind"] == "ethernet" else 1)
    urls = [f"http://{ip}:{port}/ui/" for a in ordered for ip in a["ipv4"]]
    return {"kind": kind, "adapters": adapters, "lan_urls": urls}


def status() -> Dict[str, Any]:
    strict = netguard.enabled()
    latency = None if strict else probe_internet()  # in strict mode the probe itself would be refused
    online = latency is not None
    guard = netguard.stats()
    lan = netguard.lan_allowed()
    if strict and lan:
        message = "Offline mode is on: only this computer and computers on your local network can be reached. The internet is blocked."
    elif strict:
        message = "Offline mode is on: connections to other computers are blocked."
    elif online:
        message = "Internet is reachable. Krypto does not need it: everything runs on this computer."
    else:
        message = "No internet connection. Krypto works fully offline."
    return {
        "online": online,
        "latency_ms": latency,
        "strict": strict,
        "blocked_attempts": guard["blocked"],
        "recent_blocked": guard["recent"],
        "wifi": wifi_info(),
        "lan_allowed": lan,
        "link": link_info(),
        "message": message,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    keys = ("online", "strict", "blocked_attempts", "wifi", "lan_allowed", "link")
    return all(a.get(k) == b.get(k) for k in keys)


class NetworkMonitor:
    """Re-checks now and then, and tells connected browsers when the state changes."""

    def __init__(self) -> None:
        self.latest: Optional[Dict[str, Any]] = None
        self._subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()
        self._task: Optional[asyncio.Task] = None

    def subscribe(self) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=20)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self._subscribers.discard(queue)

    async def refresh(self) -> Dict[str, Any]:
        current = await asyncio.to_thread(status)
        changed = self.latest is None or not _same(self.latest, current)
        self.latest = current
        if changed:
            self._broadcast(current)
        return current

    def _broadcast(self, payload: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def _loop(self) -> None:
        since_broadcast = 0.0
        while True:
            try:
                await self.refresh()
            except Exception:
                pass
            since_broadcast += CHECK_EVERY_SECONDS
            if since_broadcast >= HEARTBEAT_SECONDS and self.latest:
                self._broadcast(self.latest)  # periodic refresh so the "checked" time stays fresh
                since_broadcast = 0.0
            await asyncio.sleep(CHECK_EVERY_SECONDS)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


monitor = NetworkMonitor()
