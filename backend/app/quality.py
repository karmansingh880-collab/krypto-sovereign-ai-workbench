"""
Answer checking: do the specific facts in an answer actually appear in the document it came from?

A small local model sometimes invents a number, a code, or a "quote". This does a plain text
check -- no model involved -- and returns the facts it could NOT find, so the app can say
"double-check these" instead of presenting them as certain.
"""

from __future__ import annotations

import re
from typing import List

MAX_REPORTED = 8

# 1,200 / 4.2 / 0.14 / 1500 -- but not list numbers or small counts like "5 bullet points"
_NUMBER_RE = re.compile(r"(?<![\w.\-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{3,})(?![\w])")
# codes such as P-103, ORCHID-7731-DELTA, KILO_44 (must contain a digit or be upper-case)
_CODE_RE = re.compile(r"\b[A-Za-z0-9]{1,}(?:[-_][A-Za-z0-9]+)+\b")
_QUOTE_RE = re.compile(r"[\"“”]([^\"“”\n]{12,160})[\"“”]")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def unverified_facts(answer: str, source: str, question: str = "") -> List[str]:
    """Facts stated in `answer` that are not found in `source` (facts also in `question` are ignored)."""
    if not answer or not source:
        return []

    candidates: List[str] = []
    for match in _NUMBER_RE.finditer(answer):
        candidates.append(match.group(1))
    for match in _CODE_RE.finditer(answer):
        token = match.group(0)
        if re.search(r"\d", token) or token.isupper():
            candidates.append(token)
    for match in _QUOTE_RE.finditer(answer):
        candidates.append(match.group(1))
    if not candidates:
        return []

    plain_source = source.lower().replace(",", "")
    squashed_source = _squash(source)
    asked = question.lower().replace(",", "")

    missing: List[str] = []
    for fact in candidates:
        key = fact.lower().replace(",", "")
        if key in asked:
            continue
        found = key in plain_source or _squash(fact) in squashed_source
        if not found and fact not in missing:
            missing.append(fact)
        if len(missing) >= MAX_REPORTED:
            break
    return missing
