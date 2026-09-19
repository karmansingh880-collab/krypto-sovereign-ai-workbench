"""
WebSockets:
  /ws/tasks/{id}  live progress of one run: the step being worked on, what is next, and the
                  answer as it is written (see backend/app/live.py)
  /ws/system      whether this computer is online, pushed when it changes (backend/app/network.py)

Both need the sign-in cookie (the browser sends it with the WebSocket handshake). A run can only
be watched by its owner. The app also works without WebSockets: the page polls as a fallback.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app import live
from backend.app.auth.deps import user_from_cookies
from backend.app.db.mongo import db
from backend.app.network import monitor

router = APIRouter()

PING_SECONDS = 15
FINAL = ("success", "failed")


async def _until_disconnect(ws: WebSocket) -> None:
    """Returns when the browser closes the connection (messages it sends are ignored)."""
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return


def _saved_snapshot(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A finished run that is no longer in memory, described from what was saved with it."""
    created, finished = doc.get("created_at"), doc.get("finished_at")
    elapsed = (finished - created).total_seconds() if created and finished else 0
    return {
        "task_id": str(doc["_id"]),
        "status": doc.get("status", "failed"),
        "message": doc.get("final_message") or "",
        "elapsed": round(elapsed, 1),
        "stages": [{**stage, "current": None, "total": None} for stage in doc.get("timeline", [])],
        "text": "",
    }


@router.websocket("/ws/tasks/{task_id}")
async def watch_task(ws: WebSocket, task_id: str) -> None:
    user = await user_from_cookies(ws.cookies)
    if user is None or not ObjectId.is_valid(task_id):
        await ws.close(code=4401)  # refused before the handshake completes
        return
    doc = await db.tasks.find_one(
        {"_id": ObjectId(task_id), "user_id": user["_id"]},
        {"status": 1, "timeline": 1, "created_at": 1, "finished_at": 1, "final_message": 1},
    )
    if doc is None:
        await ws.close(code=4404)
        return
    await ws.accept()

    run = live.hub.get(task_id)
    if run is None:
        snapshot = _saved_snapshot(doc)
        await ws.send_json({"type": "snapshot", "run": snapshot})
        await ws.send_json({"type": "status", "status": snapshot["status"], "message": snapshot["message"]})
        await ws.close()
        return

    sub = run.subscribe(asyncio.get_running_loop())
    reader = asyncio.create_task(_until_disconnect(ws))
    try:
        await ws.send_json({"type": "snapshot", "run": run.snapshot()})
        if run.status in FINAL:
            await ws.send_json({"type": "status", "status": run.status, "message": run.message})
            return
        while True:
            getter = asyncio.create_task(sub.queue.get())
            done, _ = await asyncio.wait({getter, reader}, timeout=PING_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                getter.cancel()
                return
            if getter not in done:  # quiet for a while: keep the connection alive
                getter.cancel()
                await ws.send_json({"type": "ping"})
                continue
            event = getter.result()
            if sub.overflowed:  # this browser fell behind: give it the full picture instead of the backlog
                sub.overflowed = False
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                await ws.send_json({"type": "snapshot", "run": run.snapshot()})
                continue
            await ws.send_json(event)
            if event["type"] == "status" and event["status"] in FINAL:
                return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        run.unsubscribe(sub)
        reader.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass


@router.websocket("/ws/system")
async def watch_system(ws: WebSocket) -> None:
    user = await user_from_cookies(ws.cookies)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = monitor.subscribe()
    reader = asyncio.create_task(_until_disconnect(ws))
    try:
        latest = monitor.latest or await monitor.refresh()
        await ws.send_json({"type": "network", "network": latest})
        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({getter, reader}, timeout=PING_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                getter.cancel()
                return
            if getter not in done:
                getter.cancel()
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json({"type": "network", "network": getter.result()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        monitor.unsubscribe(queue)
        reader.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass
