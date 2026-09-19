"""
Model router for Krypto.

Decides which local Ollama model should handle a given task/step.
Rules are tried first (cheap, deterministic). Only when the rules can't
decide do we ask the small qwen3:1.7b model to classify the task.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

try:
    import ollama
except ImportError:  # pragma: no cover - ollama is a required runtime dep
    ollama = None

CONFIG_PATH = Path(__file__).parent / "models.yaml"

# Keywords used by the rule-based pass. Kept small and obvious on purpose;
# anything ambiguous should fall through to the LLM classifier instead of
# growing this list forever.
CODER_KEYWORDS = [
    "code", "python", "javascript", "typescript", "java", "c++", "function",
    "debug", "bug", "script", "algorithm", "compile", "stack trace",
    "exception", "refactor", "unit test", "regex", "sql query",
]

VISION_KEYWORDS = [
    "image", "photo", "picture", "scan", "screenshot", "diagram",
    "handwriting", "document photo", "attached drawing",
]

IMAGE_FILE_TYPES = {
    "image", "jpg", "jpeg", "png", "bmp", "gif", "tiff", "webp",
    "scan", "scanned_pdf",
}


@dataclass
class RoutingDecision:
    model_key: str      # internal key, e.g. "coder"
    model_tag: str       # ollama tag to call, e.g. "qwen2.5-coder:7b"
    reason: str          # human-readable explanation for the UI
    context_window: int  # from models.yaml, so callers can size prompts
    designed_tag: str = ""  # the model models.yaml names for this task type (differs from model_tag in demo mode)


def demo_model() -> Optional[str]:
    """Single small model to use for every LLM call, or None for the locked models.

    Set KRYPTO_DEMO_MODEL (e.g. "llama3.2:3b") to run the whole agent on
    hardware that can't host the 14B/7B models in models.yaml. models.yaml is
    never modified; unsetting the variable restores the normal routing.
    """
    return os.environ.get("KRYPTO_DEMO_MODEL") or None


def resolve_model_tag(tag: str) -> str:
    return demo_model() or tag


# Demo mode runs everything on one small model. Where a small SPECIALIST for a task type is installed
# it is used for that type instead (a real coder model for code, a real vision model for images).
# Override with KRYPTO_DEMO_CODER_MODEL / KRYPTO_DEMO_VISION_MODEL ("none" turns the specialist off).
_SPECIALISTS = {"coder": ("KRYPTO_DEMO_CODER_MODEL", "qwen2.5-coder:1.5b"),
                "vision": ("KRYPTO_DEMO_VISION_MODEL", "moondream")}
_installed_cache: tuple[float, set[str]] = (0.0, set())


def installed_models() -> set[str]:
    """Tags Ollama has downloaded (cached for a minute). Empty if Ollama is unreachable."""
    global _installed_cache
    stamp, names = _installed_cache
    if time.time() - stamp < 60:
        return names
    found: set[str] = set()
    if ollama is not None:
        try:
            listing = ollama.list()
            models = listing["models"] if isinstance(listing, dict) else getattr(listing, "models", [])
            for model in models:
                name = model.get("model") or model.get("name") if isinstance(model, dict) else getattr(model, "model", "")
                if name:
                    found.add(name)
                    if name.endswith(":latest"):
                        found.add(name[: -len(":latest")])
        except Exception:
            found = set()
    _installed_cache = (time.time(), found)
    return found


def specialist_model(model_key: str) -> Optional[str]:
    """In demo mode: the small specialist model for this task type, if it is installed; otherwise None."""
    if not demo_model() or model_key not in _SPECIALISTS:
        return None
    env_name, default = _SPECIALISTS[model_key]
    tag = os.environ.get(env_name, default)
    if not tag or tag.lower() == "none":
        return None
    return tag if tag in installed_models() else None


def describe_route(decision: "RoutingDecision", chosen_tag: Optional[str] = None) -> dict:
    """What the UI shows for one routed step: task type, the model the design calls for, the model that ran."""
    actual = chosen_tag or decision.model_tag
    return {
        "task_type": decision.model_key,
        "designed_model": decision.designed_tag or decision.model_tag,
        "model": actual,
        "reason": decision.reason,
        "substituted": actual != (decision.designed_tag or decision.model_tag),
    }


def _load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)["models"]


def _decision_for(model_key: str, config: dict, reason: str) -> RoutingDecision:
    entry = config[model_key]
    return RoutingDecision(
        model_key=model_key,
        model_tag=resolve_model_tag(entry["tag"]),
        reason=reason,
        context_window=entry["context_window"],
        designed_tag=entry["tag"],
    )


def _classify_with_small_model(task_description: str, config: dict) -> Optional[str]:
    """Ask qwen3:1.7b to pick one of general/coder/vision. Returns None on failure."""
    # In demo mode there is no separate small model, and an extra full-size
    # LLM call per step just to classify is too slow on CPU -- use rules only.
    if ollama is None or demo_model():
        return None

    small_tag = config["small_router"]["tag"]
    prompt = (
        "Classify the following task into exactly one word: "
        "general, coder, or vision.\n"
        "- coder: writing, reviewing, or debugging code\n"
        "- vision: understanding an image, scan, or screenshot\n"
        "- general: anything else (writing, summarizing, reasoning, planning)\n\n"
        f"Task: {task_description}\n\n"
        "Answer with only one word."
    )
    try:
        response = ollama.chat(
            model=small_tag,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response["message"]["content"].strip().lower()
    except Exception:
        return None

    for candidate in ("coder", "vision", "general"):
        if candidate in answer:
            return candidate
    return None


def route_to(model_key: str, reason: str) -> RoutingDecision:
    """A decision for a task type the caller has already identified (e.g. by its own rules)."""
    return _decision_for(model_key, _load_config(), reason)


def route_task(task_description: str, file_type: Optional[str] = None) -> RoutingDecision:
    """
    Decide which model should handle a task.

    Args:
        task_description: plain-language description of the step/task.
        file_type: optional hint about an attached file (e.g. "image", "pdf", "png").

    Returns:
        RoutingDecision with the chosen model key, its Ollama tag, and a reason.
    """
    config = _load_config()
    description_lower = task_description.lower()

    # 1. File-type rule: an attached image always routes to vision.
    if file_type and file_type.lower() in IMAGE_FILE_TYPES:
        return _decision_for(
            "vision", config,
            f"file_type='{file_type}' indicates an image/scan",
        )

    # 2. Keyword rules on the task description.
    if any(kw in description_lower for kw in CODER_KEYWORDS):
        return _decision_for(
            "coder", config,
            "keyword match: task description contains coding-related terms",
        )

    if any(kw in description_lower for kw in VISION_KEYWORDS):
        return _decision_for(
            "vision", config,
            "keyword match: task description references an image/scan",
        )

    # 3. No rule matched — ask the small model to classify.
    classification = _classify_with_small_model(task_description, config)
    if classification:
        return _decision_for(
            classification, config,
            f"small-model classifier (qwen3:1.7b) picked '{classification}'",
        )

    # 4. Last resort: default to general.
    return _decision_for(
        "general", config,
        "no rule matched and classifier was unavailable; defaulting to general",
    )
