"""
Work out from the instruction which files the user wants back.

Plain rules on purpose: a small local model is unreliable at deciding this, and
a wrong guess silently changes what the user gets. Only an explicit request for
a file or an image switches a run onto the file-producing path; ordinary
questions about a document keep going to the normal agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

_IMAGE_WORD = r"(?:images?|pictures?|pics?|graphs?|charts?|figures?|diagrams?|plots?|photos?|illustrations?|screenshots?|infographics?|visuali[sz]ations?)"
_GET_VERB = r"(?:give|show|download|extract|get|find|fetch|display|send|save|export|see|share|provide|list|attach|pull|need|want|share)"
_MAKE_VERB = r"(?:create|make|generate|draw|plot|design|build|produce|render|prepare|write|draft)"

DOCX_RE = re.compile(
    r"\bdocx?\b"
    r"|\bword\s+(?:file|document|doc|format|report|version)\b"
    r"|\b(?:in|as|to|into)\s+(?:a\s+|an\s+)?(?:ms\s+|microsoft\s+)?word\b"
    r"|\b(?:in|as|to|into)\s+docs\b",
    re.I,
)
PDF_RE = re.compile(
    r"\b(?:as|in|into|to)\s+(?:a\s+)?pdf\b"
    r"|\bpdf\s+(?:file|format|report|version|copy|document|output)\b",
    re.I,
)
# "create a word file and a pdf of this summary" -- a make/give verb followed by "pdf".
_PDF_AFTER_VERB_RE = re.compile(
    r"\b(?:" + _MAKE_VERB + r"|give|export|save|convert|download|send)\b([^.?!\n]{0,60}?)\bpdf\b", re.I
)
# Words that mean the PDF is the INPUT ("summarize this pdf", "a report from the uploaded pdf").
_INPUT_WORDS_RE = re.compile(r"\b(?:this|that|these|attached|uploaded|my|given|provided|input|the|of|from)\s*$", re.I)
_INPUT_ANYWHERE_RE = re.compile(r"\b(?:this|that|attached|uploaded|my|given|provided|input)\b", re.I)


def _pdf_output_requested(goal: str) -> bool:
    if PDF_RE.search(goal):
        return True
    for match in _PDF_AFTER_VERB_RE.finditer(goal):
        between = match.group(1)
        if _INPUT_ANYWHERE_RE.search(between) or _INPUT_WORDS_RE.search(between):
            continue
        return True
    return False
IMAGE_REQUEST_RE = re.compile(
    r"\b(?:" + _GET_VERB + "|" + _MAKE_VERB + r")\b[^.?!\n]{0,40}?\b" + _IMAGE_WORD + r"\b",
    re.I,
)
MAKE_IMAGE_RE = re.compile(r"\b" + _MAKE_VERB + r"\b[^.?!\n]{0,40}?\b" + _IMAGE_WORD + r"\b", re.I)


@dataclass
class Intent:
    docx: bool = False
    pdf: bool = False
    image: Optional[str] = None      # None, "extract" (take it from the document) or "generate"
    corrosion: bool = False          # the approval-note workflow, handled by the normal agent

    @property
    def wants_files(self) -> bool:
        return self.docx or self.pdf or self.image is not None


def detect(goal: str, formats: Iterable[str] = (), has_file: bool = False) -> Intent:
    """`formats` are the explicit choices from the UI (docx / pdf / image)."""
    chosen = {f.strip().lower() for f in formats}
    intent = Intent(corrosion="approval note" in goal.lower())

    intent.docx = "docx" in chosen or bool(DOCX_RE.search(goal))
    intent.pdf = "pdf" in chosen or _pdf_output_requested(goal)

    if "image" in chosen or IMAGE_REQUEST_RE.search(goal):
        # "create/make a chart" builds a new picture; "give/download the graph" takes
        # the one that is already in the attached document (when there is one).
        wants_new = bool(MAKE_IMAGE_RE.search(goal)) or not has_file
        intent.image = "generate" if wants_new else "extract"
    return intent
