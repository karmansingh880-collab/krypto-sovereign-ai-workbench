"""
Pick the part of a long document that goes into the model's prompt.

A small local model can only take a few thousand characters per prompt. For a
longer document we keep either passages spread evenly across it (for
"summarize"-style instructions) or the passages most similar to the
instruction (embedding search with nomic-embed-text), always in document order.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter, OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

import ollama

EMBED_MODEL = "nomic-embed-text"
CHUNK_CHARS = 1200
OVERLAP_CHARS = 150
SUMMARY_RE = re.compile(r"summar|overview|outline|main points|key points|gist|tl;?dr|explain the document", re.I)

# Long documents: embedding every chunk of a 300-page PDF in one request is slow and can time out,
# so a keyword pass shortlists candidates first and only those are embedded, in small batches.
SHORTLIST_ABOVE_CHUNKS = 60
SHORTLIST_SIZE = 60
SPREAD_SAMPLE = 20
EMBED_BATCH = 16

# Whole-document summaries: notes are taken section by section (map), then used as the context (reduce).
LONG_DOC_CHARS = 30_000
SECTION_CHARS = 6_000
MAX_SECTIONS = 6
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "which", "give", "tell", "about", "have",
    "been", "were", "are", "was", "who", "how", "why", "when", "where", "all", "any", "please", "show",
    "document", "file", "pdf", "here", "there", "into", "than", "then", "them", "their", "will", "would",
}


def is_summary_request(instruction: str) -> bool:
    return bool(SUMMARY_RE.search(instruction))


_PRIVATE_USE_RE = re.compile(r"[-]")   # bullet glyphs PDF text extraction leaves behind
_SPACES_RE = re.compile(r"[ \t ​]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def compact_text(text: str) -> str:
    """The same text with stray glyphs, repeated spaces and blank lines removed.

    A CPU reads about 28 tokens a second, so every wasted token costs real time. Nothing that
    carries meaning changes (words, numbers, order), so answers are unaffected.
    """
    text = _PRIVATE_USE_RE.sub("-", text)
    text = _SPACES_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES_RE.sub("\n", text).strip()


def _chunks(text: str) -> List[str]:
    text = text.strip()
    out, start = [], 0
    while start < len(text):
        out.append(text[start : start + CHUNK_CHARS])
        if start + CHUNK_CHARS >= len(text):
            break
        start += CHUNK_CHARS - OVERLAP_CHARS
    return out


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _evenly_spaced(count: int, keep: int) -> List[int]:
    if keep >= count:
        return list(range(count))
    return sorted({round(k * (count - 1) / (keep - 1)) for k in range(keep)}) if keep > 1 else [0]


def _keywords(instruction: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", instruction.lower()) if w not in _STOPWORDS]


def _shortlist(chunks: List[str], instruction: str) -> List[int]:
    """Indices of the chunks worth embedding: best keyword matches plus an even spread as a safety net."""
    words = _keywords(instruction)
    scored = []
    if words:
        for index, chunk in enumerate(chunks):
            counts = Counter(re.findall(r"[a-z0-9][a-z0-9\-]{2,}", chunk.lower()))
            score = sum(1 + math.log(counts[w]) for w in words if counts.get(w))
            if score:
                scored.append((score, index))
    best = [i for _, i in sorted(scored, reverse=True)[:SHORTLIST_SIZE]]
    return sorted(set(best) | set(_evenly_spaced(len(chunks), SPREAD_SAMPLE)) | {0})


def _embed_batches(texts: List[str]) -> List[List[float]]:
    vectors: List[List[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(ollama.embed(model=EMBED_MODEL, input=texts[start : start + EMBED_BATCH]).embeddings)
    return vectors


# Chunk embeddings are remembered for the last few documents, so a follow-up question about the
# same long document does not pay for embedding it again.
_CACHED_DOCS = 4
_embed_cache: "OrderedDict[str, Dict[int, List[float]]]" = OrderedDict()
_cache_lock = threading.Lock()


def _doc_key(text: str) -> str:
    return hashlib.sha1(f"{len(text)}|{text[:3000]}|{text[-3000:]}".encode("utf-8", "ignore")).hexdigest()


def _vectors_for(key: str, chunks: List[str], indexes: List[int]) -> Dict[int, List[float]]:
    with _cache_lock:
        cache = _embed_cache.setdefault(key, {})
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _CACHED_DOCS:
            _embed_cache.popitem(last=False)
        missing = [i for i in indexes if i not in cache]
    if missing:
        fresh = _embed_batches([chunks[i] for i in missing])
        with _cache_lock:
            cache.update(zip(missing, fresh))
    return {i: cache[i] for i in indexes}


def select_context_details(text: str, instruction: str, limit: int = 8000) -> Tuple[str, List[Dict[str, Any]]]:
    """Like select_context, but also says which passages were used (for showing sources).

    Returns (context, sources). `sources` is empty when the whole document fit.
    """
    if len(text) <= limit:
        return text, []

    chunks = _chunks(text)
    keep = max(2, limit // CHUNK_CHARS)

    if SUMMARY_RE.search(instruction):
        chosen = _evenly_spaced(len(chunks), keep)
    else:
        try:
            candidates = list(range(len(chunks)))
            if len(chunks) > SHORTLIST_ABOVE_CHUNKS:
                candidates = _shortlist(chunks, instruction)
            question = ollama.embed(model=EMBED_MODEL, input=[instruction]).embeddings[0]
            vectors = _vectors_for(_doc_key(text), chunks, candidates)
            scores = {i: _cosine(question, v) for i, v in vectors.items()}
            ranked = sorted(candidates, key=lambda i: scores[i], reverse=True)
            chosen = set(ranked[: keep - 1])
            chosen.add(0)  # the opening usually carries the title / aim / context
            chosen = sorted(chosen)
        except Exception:
            chosen = _evenly_spaced(len(chunks), keep)

    parts, previous = [], None
    sources: List[Dict[str, Any]] = []
    step = CHUNK_CHARS - OVERLAP_CHARS
    for index in chosen:
        if previous is not None and index != previous + 1:
            parts.append("[... part of the document omitted ...]")
        parts.append(chunks[index])
        previous = index
        sources.append({
            "start_percent": min(99, int(index * step * 100 / max(1, len(text)))),
            "text": " ".join(chunks[index].split())[:240],
        })
    return "\n".join(parts), sources


def select_context(text: str, instruction: str, limit: int = 8000) -> str:
    """Return `text` unchanged if it fits in `limit` characters, else the best passages."""
    return select_context_details(text, instruction, limit)[0]


def _sections(text: str) -> List[str]:
    """Split on paragraph boundaries into pieces of about SECTION_CHARS."""
    sections, current = [], ""
    for paragraph in re.split(r"\n\s*\n|\n", text):
        if len(current) + len(paragraph) > SECTION_CHARS and current:
            sections.append(current)
            current = ""
        while len(paragraph) > SECTION_CHARS:  # one enormous paragraph
            sections.append(paragraph[:SECTION_CHARS])
            paragraph = paragraph[SECTION_CHARS:]
        current += paragraph + "\n"
    if current.strip():
        sections.append(current)
    return sections


def needs_whole_document_notes(text: str, instruction: str) -> bool:
    return len(text) > LONG_DOC_CHARS and is_summary_request(instruction)


def long_document_notes(text: str, ask: Callable[[str, int], str],
                        progress: Optional[Callable[[int, int], None]] = None) -> Tuple[str, int, int]:
    """Short notes for each of up to MAX_SECTIONS evenly spread sections of a long document.

    Returns (notes, sections_covered, sections_total). `ask(prompt, max_tokens)` is one model call.
    """
    sections = _sections(text)
    total = len(sections)
    chosen = _evenly_spaced(total, MAX_SECTIONS)
    notes = []
    for number, index in enumerate(chosen, 1):
        if progress:
            progress(number, len(chosen))
        prompt = (
            "Below is one part of a long document. Write 3 to 5 short bullet points ('- ') with its "
            "most important facts. Use only this part; no introduction.\n\n"
            f"PART {index + 1} OF {total}:\n{sections[index]}\n"
        )
        try:
            note = ask(prompt, 220).strip()
        except Exception:
            note = ""
        if note:
            notes.append(f"[Part {index + 1} of {total}]\n{note}")
    return "\n\n".join(notes), len(notes), total
