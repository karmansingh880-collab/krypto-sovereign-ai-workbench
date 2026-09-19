"""
Picture understanding for attached images (demo mode).

OCR reads the WORDS in an image. A photo, a chart or a drawing also needs something that can SEE, so when
the words alone cannot answer the question a small local vision model (moondream, if installed) describes
the picture and its answer is added to the text the answering model reads. With no vision model installed
the answer says so and gives plain facts about the image instead of guessing.

The vision model is a router "vision" task: the design calls for qwen2.5vl:7b; a small stand-in is used on
a computer without a GPU. Everything runs locally through Ollama.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from typing import Any, Dict, Optional

from backend.app.router.router import demo_model, describe_route, route_to, specialist_model

MAX_SIDE = 896  # larger pictures are shrunk first: the model gains nothing from more pixels and CPU time grows fast
WORDS_ENOUGH = 200  # an image with at least this much readable text is treated as a text document
_VISUAL_WORDS = re.compile(
    r"\b(describe|picture|photo|image shows?|shown|show|look|looks|see|seen|visible|colou?r|colou?rs|chart|graph|"
    r"diagram|figure|drawing|sketch|logo|scene|object|objects|people|person|bar|bars|line|axis|trend|layout|"
    r"handwrit\w*|screenshot|what is in|what is this|what's in)\b", re.I)


def wants_picture_understanding(goal: str, ocr_text: str) -> bool:
    """True when the readable words are not enough for this question."""
    if len(ocr_text.strip()) < 40:
        return True
    return len(ocr_text.strip()) < WORDS_ENOUGH and bool(_VISUAL_WORDS.search(goal))


def image_facts(path: str) -> str:
    """Plain facts about an image (no model): size, orientation, dominant colours."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            small = image.convert("RGB").resize((32, 32))
            colours = small.getcolors(maxcolors=1024) or []
        names = []
        for _count, (r, g, b) in sorted(colours, reverse=True)[:40]:
            name = _colour_name(r, g, b)
            if name not in names:
                names.append(name)
            if len(names) == 3:
                break
        shape = "landscape" if width > height else "portrait" if height > width else "square"
        return f"The image is {width} x {height} pixels ({shape}); its main colours are {', '.join(names) or 'unknown'}."
    except Exception:
        return ""


def _colour_name(r: int, g: int, b: int) -> str:
    high, low = max(r, g, b), min(r, g, b)
    if high < 60:
        return "black"
    if low > 200:
        return "white"
    if high - low < 30:
        return "grey"
    if r >= g and r >= b:
        return "orange" if g > 0.6 * r and b < 0.5 * r else "red" if g < 0.6 * r else "pink" if b > 0.6 * r else "orange"
    if g >= r and g >= b:
        return "green"
    return "blue" if r < 0.7 * b else "purple"


def _shrunk_copy(path: str) -> str:
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        handle, target = tempfile.mkstemp(suffix=".png", prefix="krypto_vision_")
        os.close(handle)
        image.save(target)
    return target


def describe(path: str, goal: str) -> Dict[str, Any]:
    """{text, model, route, seconds, used_model}: what the picture shows, in words a text model can use."""
    started = time.time()
    decision = route_to("vision", "an attached image needs to be looked at, not only read")
    tag = specialist_model("vision")
    route = describe_route(decision, tag or decision.model_tag)
    facts = image_facts(path)
    if not tag:
        route["model"] = "none (no vision model installed)"
        route["substituted"] = True
        text = (f"{facts} No vision model is installed on this computer, so the picture itself could not be "
                "described; only its plain facts and any readable text are available.").strip()
        return {"text": text, "model": None, "route": route, "seconds": 0.0, "used_model": False}

    from backend.app.llm import chat_with_images  # imported here: keeps Ollama out of import time

    question = goal.strip() if _VISUAL_WORDS.search(goal) and len(goal) < 300 else "Describe this image in detail."
    small = _shrunk_copy(path)
    try:
        answer = chat_with_images(tag, question, [small], max_tokens=160)
        if not answer.strip() and question != "Describe this image in detail.":
            answer = chat_with_images(tag, "Describe this image in detail.", [small], max_tokens=160)
    finally:
        try:
            os.unlink(small)
        except OSError:
            pass
    answer = answer.strip()
    text = f"{facts} {answer}".strip() if answer else f"{facts} The vision model returned no description.".strip()
    return {"text": text, "model": tag, "route": route, "seconds": round(time.time() - started, 1), "used_model": bool(answer)}


def available() -> bool:
    return bool(demo_model()) and specialist_model("vision") is not None


_PLAIN_DESCRIBE = re.compile(
    r"^\s*(?:please\s+)?(?:describe|explain|tell me about|what(?:'s| is| does)(?: in| shown in| this| the)?)\b.*"
    r"\b(picture|image|photo|chart|graph|diagram|figure|drawing|screenshot)\b", re.I | re.S)


def plain_description_reply(observations: list, goal: str) -> Optional[str]:
    """For a plain "describe this picture" request: the vision model's own words (no second model to embellish them)."""
    if len(goal) > 160 or not _PLAIN_DESCRIBE.match(goal) or re.search(r"\b(and|then|also|compare|calculate|list|table)\b", goal, re.I):
        return None
    pictures = [o for o in observations if o.get("tool_used") == "describe_image" and not o.get("error")
                and isinstance(o.get("result"), str) and o["result"].strip()]
    if not pictures or "no vision model is installed" in pictures[0]["result"]:
        return None
    lines = []
    for o in pictures:
        name = o["step"].replace("[SYSTEM] look at the picture", "").strip()
        lines.append(f"**{name}**\n\n{o['result'].strip()}\n\n*Described by the local vision model ({o.get('model_tag')}); nothing left this computer.*")
    return "\n\n".join(lines)


_ASKS_FOR_TEXT = re.compile(
    r"^\s*(?:please\s+)?(?:what|which)\s+(?:text|words|writing)\b"  # "what text is written in the image?"
    r"|^\s*(?:please\s+)?what(?:'s|\s+is)\s+written\b"
    r"|^\s*(?:please\s+)?what\s+does\s+(?:it|this|that|the\s+\w+)\s+say\s*[?.!]*\s*$"  # ... but not "say about the budget"
    r"|^\s*(?:please\s+)?(?:read|transcribe|extract|copy)\b.{0,25}\b(?:text|words|writing)\b\s*(?:from\b.{0,40})?[.!?]*\s*$"
    r"|\bocr\b", re.I)
_VERBATIM_MAX_CHARS = 1500


def verbatim_text_reply(observations: list, goal: str) -> Optional[str]:
    """For "what text is in this image": the OCR text itself, exactly. A small model copying it can drop a digit."""
    if len(goal) > 200 or not _ASKS_FOR_TEXT.search(goal) or re.search(r"\b(summar|explain|translate|why|how many|count)\w*", goal, re.I):
        return None
    documents = [o for o in observations if o.get("forced") and o.get("tool_used") == "extract_text"
                 and not o.get("error") and isinstance(o.get("result"), str)]
    if len(documents) != 1:
        return None
    text = documents[0]["result"].split("\n\n[What the picture shows]")[0].strip()
    if not text or len(text) > _VERBATIM_MAX_CHARS:
        return None
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())
    return f"The text reads:\n\n{quoted}\n\n*Read exactly as recognised by OCR on this computer.*"
