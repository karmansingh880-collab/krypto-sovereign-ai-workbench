"""
Manual smoke test for the RAG ingest/search pipeline.

Run with:
    ./venv/bin/python -m backend.app.rag.test_rag
"""

import tempfile
from pathlib import Path

from backend.app.rag.ingest import ingest_document
from backend.app.rag.search import search_knowledge

SAMPLE_TEXT = """
Krypto is an AI-assisted inspection platform built for MRPL (Mangalore
Refinery and Petrochemicals Limited) under Smart India Hackathon 2026,
problem statement 26117. The platform helps inspectors analyze equipment
photos, scanned inspection reports, and handwritten thickness logs.

One of its core tools is the corrosion rate calculator, which computes
remaining safe life for a pipe or vessel based on thickness readings taken
at two different points in time. This calculation is done with plain
Python math, not a language model, to guarantee correctness.

The knowledge base search feature lets inspectors ask natural-language
questions about past inspection standards and get back cited answers with
the source document and page number.
"""


def main():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(SAMPLE_TEXT)
        sample_path = f.name

    try:
        print(f"Ingesting sample file: {sample_path}")
        num_chunks = ingest_document(sample_path)
        print(f"Stored {num_chunks} chunks in Qdrant.\n")

        matching_query = "What does the corrosion calculator use to compute remaining life?"
        print(f"Query (should match): {matching_query!r}")
        results = search_knowledge(matching_query)
        if isinstance(results, str):
            print(f"  -> {results}")
        else:
            for r in results:
                print(f"  -> [{r['doc_name']} p.{r['page']}] score={r['score']}")
                print(f"     {r['text'][:150]!r}")
        print()

        non_matching_query = "What is the capital of France?"
        print(f"Query (should NOT match): {non_matching_query!r}")
        results = search_knowledge(non_matching_query)
        if isinstance(results, str):
            print(f"  -> {results}")
        else:
            for r in results:
                print(f"  -> [{r['doc_name']} p.{r['page']}] score={r['score']}")
    finally:
        Path(sample_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
