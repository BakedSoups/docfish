"""Persistent local state for sources, indexing, and learning sessions."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain import Source


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

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["include"] = json.loads(value.pop("include_patterns"))
        value["exclude"] = json.loads(value.pop("exclude_patterns"))
        return value
