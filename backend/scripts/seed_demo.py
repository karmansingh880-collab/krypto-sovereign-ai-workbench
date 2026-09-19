"""
Load the demo SOP into the Qdrant knowledge base (safe to run repeatedly).

Run from the repo root:
    .venv\\Scripts\\python -m backend.scripts.seed_demo
"""

from qdrant_client.http import models as qmodels

from backend.app.core.config import BACKEND_DIR
from backend.app.rag.ingest import COLLECTION_NAME, get_client, ingest_document

DEMO_DOCS = [BACKEND_DIR / "test_data" / "SOP-CORR-014.txt"]


def already_ingested(doc_name: str) -> bool:
    client = get_client()
    if not client.collection_exists(COLLECTION_NAME):
        return False
    count = client.count(
        COLLECTION_NAME,
        count_filter=qmodels.Filter(
            must=[qmodels.FieldCondition(key="doc_name", match=qmodels.MatchValue(value=doc_name))]
        ),
    )
    return count.count > 0


def main() -> None:
    for path in DEMO_DOCS:
        if already_ingested(path.name):
            print(f"{path.name}: already in knowledge base, skipping")
            continue
        chunks = ingest_document(str(path))
        print(f"{path.name}: ingested {chunks} chunks")


if __name__ == "__main__":
    main()
