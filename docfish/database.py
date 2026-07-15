"""Persistent local state for sources, indexing, and learning sessions."""

import json
import sqlite3
import threading
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain import Source
from .domain import Chunk


SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._initialize()

    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _initialize(self) -> None:
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('html', 'pdf', 'markdown', 'text')),
                    path TEXT NOT NULL UNIQUE,
                    include_patterns TEXT NOT NULL DEFAULT '[]',
                    exclude_patterns TEXT NOT NULL DEFAULT '[]',
                    home TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'not_indexed',
                    progress INTEGER NOT NULL DEFAULT 0,
                    pages INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    index_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    parser_version TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, path)
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    document_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    anchor TEXT NOT NULL DEFAULT '',
                    page INTEGER,
                    vector BLOB,
                    UNIQUE(source_id, document_path, position)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    source_id UNINDEXED,
                    title,
                    text,
                    tokenize='unicode61 tokenchars ''_'''
                );

                CREATE TABLE IF NOT EXISTS index_jobs (
                    id INTEGER PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS learning_notes (
                    id INTEGER PRIMARY KEY,
                    question TEXT NOT NULL,
                    crafted_prompt TEXT NOT NULL DEFAULT '',
                    answer TEXT NOT NULL DEFAULT '',
                    citations TEXT NOT NULL DEFAULT '[]',
                    correction TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS content_packs (
                    id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'available',
                    installed_path TEXT NOT NULL DEFAULT '',
                    installed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS vector_points (
                    collection_name TEXT NOT NULL,
                    id TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(collection_name, id)
                );
            """)
            db.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            # A process died before updating these jobs. They are safe to retry.
            db.execute("UPDATE index_jobs SET state='interrupted', updated_at=CURRENT_TIMESTAMP WHERE state IN ('queued', 'indexing')")
            db.execute("UPDATE sources SET state='interrupted' WHERE state IN ('queued', 'indexing')")

    def upsert_source(self, source: Source) -> None:
        with self.transaction() as db:
            db.execute("""
                INSERT INTO sources(id, name, kind, path, include_patterns, exclude_patterns, home, state)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, kind=excluded.kind, path=excluded.path,
                    include_patterns=excluded.include_patterns,
                    exclude_patterns=excluded.exclude_patterns, home=excluded.home,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                source.id, source.name, source.kind, str(source.path),
                json.dumps(source.include), json.dumps(source.exclude), source.home, source.state,
            ))

    def list_sources(self) -> list[dict]:
        rows = self.connection().execute("SELECT * FROM sources ORDER BY name COLLATE NOCASE").fetchall()
        return [self._source_row(row) for row in rows]

    def get_source(self, source_id: str) -> dict | None:
        row = self.connection().execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return self._source_row(row) if row else None

    def remove_source(self, source_id: str) -> bool:
        with self.transaction() as db:
            cursor = db.execute("DELETE FROM sources WHERE id=?", (source_id,))
            db.execute("DELETE FROM chunks_fts WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM vector_points WHERE collection_name IN (?, ?)", (f"angler_{source_id}", f"docfish_{source_id}"))
            return cursor.rowcount > 0

    def set_source_state(self, source_id: str, state: str, progress: int = 0, pages: int = 0, error: str = "") -> None:
        with self.transaction() as db:
            db.execute("""
                UPDATE sources SET state=?, progress=?, pages=?, error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (state, progress, pages, error, source_id))

    def create_job(self, source_id: str, total: int = 0) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO index_jobs(source_id, state, total) VALUES(?, 'queued', ?)",
                (source_id, total),
            )
            db.execute(
                "UPDATE sources SET state='queued', progress=0, error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (source_id,),
            )
            return int(cursor.lastrowid)

    def update_job(self, job_id: int, state: str, processed: int = 0, total: int = 0, error: str = "") -> None:
        progress = round(processed * 100 / total) if total else 0
        with self.transaction() as db:
            db.execute("""
                UPDATE index_jobs SET state=?, progress=?, processed=?, total=?, error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (state, progress, processed, total, error, job_id))
            row = db.execute("SELECT source_id FROM index_jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                db.execute("""
                    UPDATE sources SET state=?, progress=?, pages=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
                """, (state, progress, total, error, row["source_id"]))

    def latest_job(self, source_id: str) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM index_jobs WHERE source_id=? ORDER BY id DESC LIMIT 1", (source_id,)
        ).fetchone()
        return dict(row) if row else None

    def request_cancel(self, source_id: str) -> bool:
        with self.transaction() as db:
            cursor = db.execute("""
                UPDATE index_jobs SET cancel_requested=1, updated_at=CURRENT_TIMESTAMP
                WHERE id=(SELECT id FROM index_jobs WHERE source_id=? AND state IN ('queued','indexing') ORDER BY id DESC LIMIT 1)
            """, (source_id,))
            return cursor.rowcount > 0

    def is_cancel_requested(self, job_id: int) -> bool:
        row = self.connection().execute("SELECT cancel_requested FROM index_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row[0])

    def document_manifest(self, source_id: str) -> dict[str, dict]:
        rows = self.connection().execute(
            "SELECT * FROM documents WHERE source_id=?", (source_id,)
        ).fetchall()
        return {row["path"]: dict(row) for row in rows}

    def chunk_ids(self, source_id: str, document_path: str) -> set[str]:
        rows = self.connection().execute(
            "SELECT id FROM chunks WHERE source_id=? AND document_path=?",
            (source_id, document_path),
        ).fetchall()
        return {row[0] for row in rows}

    def replace_document(
        self, source_id: str, document_path: str, content_hash: str,
        modified_ns: int, size_bytes: int, parser_version: str,
        title: str, chunks: list[Chunk], vectors: list[list[float]],
    ) -> set[str]:
        old_ids = self.chunk_ids(source_id, document_path)
        new_ids = {chunk.id for chunk in chunks}
        with self.transaction() as db:
            db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=? AND document_path=?)", (source_id, document_path))
            db.execute("DELETE FROM chunks WHERE source_id=? AND document_path=?", (source_id, document_path))
            for chunk, vector in zip(chunks, vectors):
                blob = array("f", vector).tobytes()
                db.execute("""
                    INSERT INTO chunks(id, source_id, document_path, title, text, position, anchor, page, vector)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (chunk.id, source_id, document_path, chunk.title, chunk.text, chunk.position, chunk.anchor, chunk.page, blob))
                db.execute(
                    "INSERT INTO chunks_fts(chunk_id, source_id, title, text) VALUES(?, ?, ?, ?)",
                    (chunk.id, source_id, chunk.title, chunk.text),
                )
            db.execute("""
                INSERT INTO documents(source_id, path, content_hash, modified_ns, size_bytes, parser_version, title)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    content_hash=excluded.content_hash, modified_ns=excluded.modified_ns,
                    size_bytes=excluded.size_bytes, parser_version=excluded.parser_version,
                    title=excluded.title, indexed_at=CURRENT_TIMESTAMP
            """, (source_id, document_path, content_hash, modified_ns, size_bytes, parser_version, title))
        return old_ids - new_ids

    def remove_document(self, source_id: str, document_path: str) -> set[str]:
        old_ids = self.chunk_ids(source_id, document_path)
        with self.transaction() as db:
            db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=? AND document_path=?)", (source_id, document_path))
            db.execute("DELETE FROM documents WHERE source_id=? AND path=?", (source_id, document_path))
            db.execute("DELETE FROM chunks WHERE source_id=? AND document_path=?", (source_id, document_path))
        return old_ids

    def clear_source_index(self, source_id: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM chunks_fts WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM documents WHERE source_id=?", (source_id,))
            db.execute("UPDATE sources SET state='not_indexed', progress=0, pages=0, index_bytes=0, error='', updated_at=CURRENT_TIMESTAMP WHERE id=?", (source_id,))

    def source_storage(self) -> list[dict]:
        rows = self.connection().execute("""
            SELECT s.id, s.name,
                   COALESCE((SELECT SUM(LENGTH(text) + LENGTH(vector)) FROM chunks WHERE source_id=s.id), 0) AS generated_bytes,
                   (SELECT COUNT(*) FROM documents WHERE source_id=s.id) AS documents,
                   (SELECT COUNT(*) FROM chunks WHERE source_id=s.id) AS chunks
            FROM sources s ORDER BY generated_bytes DESC
        """).fetchall()
        return [dict(row) for row in rows]

    def lexical_search(self, source_id: str, query: str, limit: int = 20) -> list[dict]:
        terms = [term for term in query.replace('"', ' ').split() if term]
        if not terms:
            return []
        expression = " OR ".join(f'"{term}"' for term in terms[:12])
        rows = self.connection().execute("""
            SELECT c.*, bm25(chunks_fts) AS rank
            FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.chunk_id
            WHERE chunks_fts MATCH ? AND chunks_fts.source_id=?
            ORDER BY rank LIMIT ?
        """, (expression, source_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def save_note(self, question: str, crafted_prompt: str, answer: str, citations: list[dict], correction: str = "") -> dict:
        with self.transaction() as db:
            cursor = db.execute("""
                INSERT INTO learning_notes(question, crafted_prompt, answer, citations, correction)
                VALUES(?, ?, ?, ?, ?)
            """, (question, crafted_prompt, answer, json.dumps(citations), correction))
            note_id = int(cursor.lastrowid)
        return self.get_note(note_id)

    def list_notes(self) -> list[dict]:
        rows = self.connection().execute(
            "SELECT * FROM learning_notes ORDER BY id DESC"
        ).fetchall()
        return [self._note_row(row) for row in rows]

    def get_note(self, note_id: int) -> dict | None:
        row = self.connection().execute("SELECT * FROM learning_notes WHERE id=?", (note_id,)).fetchone()
        return self._note_row(row) if row else None

    @staticmethod
    def _note_row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["citations"] = json.loads(value["citations"])
        return value

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["include"] = json.loads(value.pop("include_patterns"))
        value["exclude"] = json.loads(value.pop("exclude_patterns"))
        return value
