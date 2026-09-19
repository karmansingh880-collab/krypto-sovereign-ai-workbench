"""
The one place the app makes a chat call to the local model.

- keeps the model loaded between calls (no reload delay for the next question)
- caps the answer length when asked (generation is the slow part on a CPU)
- when a run is being watched, streams the answer to the live panel as it is written, and says
  roughly how long the model will spend reading the text first
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.app import live

try:
    import ollama
except ImportError:  # pragma: no cover
    ollama = None

KEEP_ALIVE = "20m"
CHARS_PER_TOKEN = 4.2

# How fast this computer reads text (tokens per second). Starts at a typical laptop-CPU value and
# is corrected from what the model actually reports after each call.
_read_speed = 28.0


def read_speed() -> float:
    return _read_speed


def _learn_speed(meta: Any) -> None:
    """Update the reading-speed estimate from a reply's timing fields (when it reports them)."""
    global _read_speed
    try:
        tokens, nanos = int(meta["prompt_eval_count"]), int(meta["prompt_eval_duration"])
    except (KeyError, TypeError, ValueError):
        return
    if tokens >= 200 and nanos > 0:  # a short or cached prompt says nothing about the real speed
        measured = tokens / (nanos / 1e9)
        if 2 <= measured <= 2000:
            _read_speed = 0.6 * _read_speed + 0.4 * measured


def estimate_seconds(prompt: str) -> int:
    return max(1, round(len(prompt) / CHARS_PER_TOKEN / _read_speed))


def chat(model: str, prompt: str, *, options: Optional[Dict[str, Any]] = None, max_tokens: Optional[int] = None,
         stream_to_ui: bool = False, system: Optional[str] = None) -> str:
    """Raw text of one model reply. `stream_to_ui` shows it live if somebody is watching the run."""
    if ollama is None:
        raise RuntimeError("ollama python package is not installed")

    merged: Dict[str, Any] = dict(options or {})
    if max_tokens:
        merged["num_predict"] = max_tokens
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]

    if not live.watching():
        response = ollama.chat(model=model, messages=messages, options=merged or None, keep_alive=KEEP_ALIVE)
        _learn_speed(response)
        return response["message"]["content"]

    tokens = round(len(prompt) / CHARS_PER_TOKEN)
    live.note(f"The model is reading about {tokens:,} tokens of text: roughly {estimate_seconds(prompt)} s on this computer")

    if not stream_to_ui:
        response = ollama.chat(model=model, messages=messages, options=merged or None, keep_alive=KEEP_ALIVE)
        _learn_speed(response)
        return response["message"]["content"]

    live.reset_text()
    pieces = []
    first = True
    for chunk in ollama.chat(model=model, messages=messages, options=merged or None, stream=True,
                             keep_alive=KEEP_ALIVE):
        piece = chunk["message"]["content"]
        if piece:
            if first:
                live.note("Writing the answer")
                first = False
            pieces.append(piece)
            live.token(piece)
        if chunk.get("done"):
            _learn_speed(chunk)
    return "".join(pieces)


def chat_with_images(model: str, prompt: str, images: list, *, max_tokens: Optional[int] = None) -> str:
    """One question about one or more image files, answered by a local vision model."""
    if ollama is None:
        raise RuntimeError("ollama python package is not installed")
    options: Dict[str, Any] = {"num_predict": max_tokens} if max_tokens else {}
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": list(images)}],
        options=options or None,
        keep_alive="5m",  # the vision model is big; do not hold memory the text model needs
    )
    return response["message"]["content"]
