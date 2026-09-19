"""Service/model readiness, so the UI can show exactly what is up or missing."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

import yaml
from fastapi import APIRouter, Depends

from backend.app.auth.deps import current_user
from backend.app.db.mongo import ping
from backend.app.router.router import CONFIG_PATH, demo_model

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(current_user)])

EMBED_MODEL = "nomic-embed-text"


def _required_models() -> list[str]:
    override = demo_model()
    if override:
        return sorted({override, EMBED_MODEL})
    with open(CONFIG_PATH, "r") as f:
        models = yaml.safe_load(f)["models"]
    return sorted({entry["tag"] for entry in models.values()} | {EMBED_MODEL})


def _check_qdrant() -> Dict[str, Any]:
    from qdrant_client import QdrantClient

    from backend.app.rag.ingest import COLLECTION_NAME, QDRANT_HOST, QDRANT_PORT

    try:
        # A generous timeout: while the model is working the CPU is saturated and a healthy
        # service can take several seconds to answer -- that is not an outage.
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=20)
        if not client.collection_exists(COLLECTION_NAME):
            return {"up": True, "knowledge_base_chunks": 0}
        return {"up": True, "knowledge_base_chunks": client.count(COLLECTION_NAME).count}
    except Exception as exc:
        return {"up": False, "error": str(exc)[:200]}


def _check_ollama() -> Dict[str, Any]:
    import ollama

    try:
        installed = {m.model for m in ollama.list().models}
    except Exception as exc:
        return {"up": False, "error": str(exc)[:200], "models": {}}

    def present(tag: str) -> bool:
        return tag in installed or f"{tag}:latest" in installed

    result: Dict[str, Any] = {"up": True, "models": {tag: present(tag) for tag in _required_models()}}
    if demo_model():
        # Small specialists that are used for code and for images when installed (optional: not a problem if absent).
        from backend.app.router.router import _SPECIALISTS

        result["optional"] = {
            os.environ.get(env_name, default): present(os.environ.get(env_name, default))
            for env_name, default in _SPECIALISTS.values()
            if os.environ.get(env_name, default).lower() != "none"
        }
    return result


@router.get("/status")
async def system_status():
    async def mongo() -> Dict[str, Any]:
        try:
            await asyncio.wait_for(ping(), timeout=12)
            return {"up": True}
        except Exception as exc:
            return {"up": False, "error": str(exc)[:200]}

    mongo_status, qdrant_status, ollama_status = await asyncio.gather(
        mongo(),
        asyncio.to_thread(_check_qdrant),
        asyncio.to_thread(_check_ollama),
    )
    return {"mongo": mongo_status, "qdrant": qdrant_status, "ollama": ollama_status}


@router.get("/sovereignty")
async def sovereignty_report():
    """Evidence that nothing leaves this computer: the operating system's own list of open connections."""
    from backend.app import sovereignty

    return await asyncio.to_thread(sovereignty.snapshot)


@router.post("/sovereignty/selftest")
async def sovereignty_selftest():
    """Try to reach the internet on purpose and show that it is refused (strict offline mode only)."""
    from backend.app import sovereignty

    return await asyncio.to_thread(sovereignty.self_test)


@router.get("/models")
async def model_routing():
    """Which model each kind of task goes to: what the design calls for, and what runs on this computer."""
    from backend.app.router.router import _load_config, demo_model, installed_models, specialist_model

    config = _load_config()
    installed = installed_models()
    rows = []
    for key, entry in config.items():
        if key == "small_router":
            continue
        specialist = specialist_model(key)
        running = specialist or (demo_model() or entry["tag"])
        rows.append({
            "task_type": key,
            "roles": entry.get("roles", []),
            "designed_model": entry["tag"],
            "running_model": running,
            "installed": running in installed or f"{running}:latest" in installed,
            "stand_in": running != entry["tag"],
        })
    return {"demo_mode": bool(demo_model()), "models": rows}


@router.get("/network")
async def network_status(fresh: bool = False):
    """Is this computer online? (Krypto never needs the internet; this only reports the state.)"""
    from backend.app.network import monitor

    if fresh or monitor.latest is None:
        return await monitor.refresh()
    return monitor.latest
