"""Vector backend implementations.

The indexing layer depends on this adapter instead of Qdrant directly. A small
embedded backend can therefore be added without changing parsing or retrieval.
"""

from typing import Any

from qdrant_client import QdrantClient, models

from .domain import SearchResult


class QdrantVectorStore:
    def __init__(self, url: str):
        self.client = QdrantClient(url=url)

    def collections(self) -> set[str]:
        return {item.name for item in self.client.get_collections().collections}

    def exists(self, name: str) -> bool:
        return self.client.collection_exists(name)

    def recreate(self, name: str, vector_size: int) -> None:
        if self.exists(name):
            self.client.delete_collection(name)
        self.client.create_collection(
            name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )

    def upsert(self, name: str, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        self.client.upsert(
            collection_name=name,
            points=[models.PointStruct(id=id_, vector=vector, payload=payload) for id_, vector, payload in points],
            wait=True,
        )

    def query(self, name: str, vector: list[float], limit: int) -> list[SearchResult]:
        response = self.client.query_points(
            collection_name=name,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        return [SearchResult(score=point.score, payload=point.payload or {}) for point in response.points]

    def neighbors(self, name: str, path: str, position: int, radius: int = 1) -> list[dict[str, Any]]:
        points, _ = self.client.scroll(
            collection_name=name,
            scroll_filter=models.Filter(must=[
                models.FieldCondition(key="path", match=models.MatchValue(value=path)),
                models.FieldCondition(
                    key="chunk",
                    range=models.Range(gte=max(0, position - radius), lte=position + radius),
                ),
            ]),
            limit=radius * 2 + 1,
            with_payload=True,
            with_vectors=False,
        )
        points.sort(key=lambda point: (point.payload or {}).get("chunk", 0))
        return [point.payload or {} for point in points]
