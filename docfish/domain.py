"""Shared domain types for sources, parsed chunks, and retrieval results."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class Source:
    id: str
    name: str
    kind: str
    path: Path
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    home: str = ""
    state: str = "not_indexed"


@dataclass(slots=True)
class Chunk:
    id: str
    source_id: str
    document_path: str
    title: str
    text: str
    position: int
    anchor: str = ""
    page: int | None = None

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.document_path,
            "title": self.title,
            "chunk": self.position,
            "text": self.text,
        }
        if self.anchor:
            value["anchor"] = self.anchor
        if self.page is not None:
            value["page"] = self.page
        return value


@dataclass(slots=True)
class SearchResult:
    score: float
    payload: dict[str, Any]


class SourceParser(Protocol):
    def supports(self, path: Path) -> bool: ...

    def parse(self, source: Source, path: Path) -> list[Chunk]: ...


class VectorStore(Protocol):
    def collections(self) -> set[str]: ...

    def exists(self, name: str) -> bool: ...

    def recreate(self, name: str, vector_size: int) -> None: ...

    def delete_collection(self, name: str) -> None: ...

    def ensure(self, name: str, vector_size: int) -> None: ...

    def upsert(self, name: str, points: list[tuple[str, list[float], dict[str, Any]]]) -> None: ...

    def delete_ids(self, name: str, ids: list[str]) -> None: ...

    def query(self, name: str, vector: list[float], limit: int) -> list[SearchResult]: ...

    def neighbors(self, name: str, path: str, position: int, radius: int = 1) -> list[dict[str, Any]]: ...
