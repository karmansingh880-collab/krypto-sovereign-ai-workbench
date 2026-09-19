"""
Model router for Krypto.

Decides which local Ollama model should handle a given task/step.
Rules are tried first (cheap, deterministic). Only when the rules can't
decide do we ask the small qwen3:1.7b model to classify the task.
"""

from __future__ import annotations

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


def _load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)["models"]


def _decision_for(model_key: str, config: dict, reason: str) -> RoutingDecision:
    entry = config[model_key]
    return RoutingDecision(
        model_key=model_key,
        model_tag=entry["tag"],
        reason=reason,
        context_window=entry["context_window"],
    )


def _classify_with_small_model(task_description: str, config: dict) -> Optional[str]:
    """Ask qwen3:1.7b to pick one of general/coder/vision. Returns None on failure."""
    if ollama is None:
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
