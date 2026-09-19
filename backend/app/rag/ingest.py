"""
RAG ingestion: chunk a document and store its embeddings in Qdrant.

Embeddings come from Ollama's `nomic-embed-text` model. This is a separate,
purpose-built embedding model -- none of Krypto's 4 locked chat/vision
models can serve /api/embed on this Ollama version (recent Ollama versions
only serve embeddings for models built for that purpose).

Qdrant runs as a real server (docker run qdrant/qdrant), addressed via
QDRANT_HOST/QDRANT_PORT env vars (defaulting to localhost:6333).
"""

from __future__ import annotations

import os
import uuid
from typing import List, Optional, TypedDict

import ollama
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

EMBED_MODEL = "nomic-embed-text"
COLLECTION_NAME = "krypto_knowledge_base"
CHUNK_SIZE_CHARS = 500
CHUNK_OVERLAP_CHARS = 50

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))

_client: Optional[QdrantClient] = None


class Chunk(TypedDict):
    text: str
    doc_name: str
    page: int
    chunk_index: int


def get_client() -> QdrantClient:
    """Lazily create a single shared Qdrant client (real server)."""
    global _client
    if _client is None:
        _client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    return _client


def embed_text(text: str) -> List[float]:
    response = ollama.embed(model=EMBED_MODEL, input=text)
    return response.embeddings[0]


def _chunk_page_text(text: str) -> List[str]:
    """Split a page/document's text into overlapping fixed-size chunks."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE_CHARS
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP_CHARS
    return [c for c in chunks if c]


def _read_pages(file_path: str) -> List[str]:
    """Return a list of page texts. PDFs are split per-page; everything
    else (e.g. .txt) is treated as a single page."""
    if file_path.lower().endswith(".pdf"):
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        pages = [page.get_text() for page in doc]
        doc.close()
        return pages

    with open(file_path, "r", encoding="utf-8") as f:
        return [f.read()]


def _ensure_collection(client: QdrantClient, vector_size: int) -> None:
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=qmodels.Distance.COSINE
            ),
        )


def ingest_document(file_path: str) -> int:
    """
    Chunk a document and store its embeddings in Qdrant with metadata
    (doc name, page, chunk index).

    Returns:
        Number of chunks stored.
    """
    doc_name = os.path.basename(file_path)
    pages = _read_pages(file_path)

    client = get_client()
    points = []
    chunk_index = 0

    for page_number, page_text in enumerate(pages, start=1):
        for chunk_text in _chunk_page_text(page_text):
            vector = embed_text(chunk_text)
            if not points:
                _ensure_collection(client, vector_size=len(vector))
            points.append(
                qmodels.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "text": chunk_text,
                        "doc_name": doc_name,
                        "page": page_number,
                        "chunk_index": chunk_index,
                    },
                )
            )
            chunk_index += 1

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)

    return len(points)
