"""Vector backend implementations.

The indexing layer depends on this adapter instead of Qdrant directly. A small
embedded backend can therefore be added without changing parsing or retrieval.
"""

import json
import math
from array import array
from typing import Any

from qdrant_client import QdrantClient, models

from .domain import SearchResult


class SQLiteVectorStore:
    """Small-library vector search with no service or Docker dependency."""

    def __init__(self, database):
        self.database = database

    def collections(self) -> set[str]:
        rows = self.database.connection().execute(
            "SELECT DISTINCT collection_name FROM vector_points"
        ).fetchall()
        return {row[0] for row in rows}

    def exists(self, name: str) -> bool:
        row = self.database.connection().execute(
            "SELECT 1 FROM vector_points WHERE collection_name=? LIMIT 1", (name,)
        ).fetchone()
        return row is not None

    def recreate(self, name: str, vector_size: int) -> None:
        with self.database.transaction() as db:
            db.execute("DELETE FROM vector_points WHERE collection_name=?", (name,))

    def delete_collection(self, name: str) -> None:
        self.recreate(name, 0)

    def ensure(self, name: str, vector_size: int) -> None:
        return None

    def upsert(self, name: str, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        with self.database.transaction() as db:
            db.executemany("""
                INSERT INTO vector_points(collection_name, id, vector, payload) VALUES(?, ?, ?, ?)
                ON CONFLICT(collection_name, id) DO UPDATE SET vector=excluded.vector, payload=excluded.payload
            """, [
                (name, id_, array("f", vector).tobytes(), json.dumps(payload))
                for id_, vector, payload in points
            ])

    def delete_ids(self, name: str, ids: list[str]) -> None:
        if not ids:
            return
        with self.database.transaction() as db:
            db.executemany(
                "DELETE FROM vector_points WHERE collection_name=? AND id=?",
                [(name, id_) for id_ in ids],
            )

    def query(self, name: str, vector: list[float], limit: int) -> list[SearchResult]:
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        rows = self.database.connection().execute(
            "SELECT vector, payload FROM vector_points WHERE collection_name=?", (name,)
        ).fetchall()
        results = []
        for row in rows:
            candidate = array("f")
            candidate.frombytes(row["vector"])
            candidate_norm = math.sqrt(sum(value * value for value in candidate)) or 1.0
            score = sum(left * right for left, right in zip(vector, candidate)) / (norm * candidate_norm)
            results.append(SearchResult(score, json.loads(row["payload"])))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    def neighbors(self, name: str, path: str, position: int, radius: int = 1) -> list[dict[str, Any]]:
        rows = self.database.connection().execute(
            "SELECT payload FROM vector_points WHERE collection_name=?", (name,)
        ).fetchall()
        payloads = [json.loads(row[0]) for row in rows]
        return sorted(
            [item for item in payloads if item.get("path") == path and abs(item.get("chunk", 0) - position) <= radius],
            key=lambda item: item.get("chunk", 0),
        )


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

    def delete_collection(self, name: str) -> None:
        if self.exists(name):
            self.client.delete_collection(name)

    def ensure(self, name: str, vector_size: int) -> None:
        if not self.exists(name):
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

    def delete_ids(self, name: str, ids: list[str]) -> None:
        if ids:
            self.client.delete(
                collection_name=name,
                points_selector=models.PointIdsList(points=ids),
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
