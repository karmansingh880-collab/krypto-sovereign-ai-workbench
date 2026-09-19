"""
Vision tool: ask qwen2.5vl:7b a question about an image via Ollama.
"""

from __future__ import annotations

import ollama

from backend.app.router.router import resolve_model_tag, specialist_model

VISION_MODEL_TAG = "qwen2.5vl:7b"


def describe_image(file_path: str, question: str = "Describe this image.") -> str:
    """
    Send an image to the vision model along with a question and return
    its text answer.

    Args:
        file_path: path to the image file (png/jpg/etc).
        question: what to ask about the image.
    """
    response = ollama.chat(
        model=specialist_model("vision") or resolve_model_tag(VISION_MODEL_TAG),
        messages=[
            {
                "role": "user",
                "content": question,
                "images": [file_path],
            }
        ],
    )
    return response["message"]["content"].strip()
