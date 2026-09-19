"""
RAG search: query the Qdrant knowledge base and return cited chunks.
"""

from __future__ import annotations

from typing import List, TypedDict, Union

from backend.app.rag.ingest import COLLECTION_NAME, embed_text, get_client

NOT_FOUND_MESSAGE = "Not found in the knowledge base"

# Cosine similarity threshold below which a result is considered a non-match.
# Cosine scores here range roughly -1..1; anything below this is treated as
# too weak to cite back to the user.
SCORE_THRESHOLD = 0.5


class SearchResult(TypedDict):
    text: str
    doc_name: str
    page: int
    score: float


def search_knowledge(query: str, top_k: int = 3) -> Union[List[SearchResult], str]:
    """
    Search the knowledge base for chunks relevant to `query`.

    Returns:
        A list of SearchResult (best match first), or the string
        NOT_FOUND_MESSAGE if the best match's score is below threshold
        or the collection doesn't exist yet.
    """
    client = get_client()
    if not client.collection_exists(COLLECTION_NAME):
        return NOT_FOUND_MESSAGE

    query_vector = embed_text(query)
    hits = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
    ).points

    if not hits or hits[0].score < SCORE_THRESHOLD:
        return NOT_FOUND_MESSAGE

    results: List[SearchResult] = []
    for hit in hits:
        if hit.score < SCORE_THRESHOLD:
            continue
        results.append(
            SearchResult(
                text=hit.payload["text"],
                doc_name=hit.payload["doc_name"],
                page=hit.payload["page"],
                score=round(hit.score, 4),
            )
        )
    return results
