"""Print RAG indexing status from inside the Docfish container."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / "Documentation" / "docfish.sqlite"


def status_rows(database_path: Path) -> list[dict]:
    if not database_path.exists():
        raise FileNotFoundError(f"Docfish database not found: {database_path}")
    database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute("""
            SELECT s.id, s.name, s.state, s.progress, s.error,
                   COALESCE((SELECT COUNT(*) FROM documents d WHERE d.source_id=s.id), 0) AS documents,
                   COALESCE((SELECT COUNT(*) FROM chunks c WHERE c.source_id=s.id), 0) AS chunks,
                   COALESCE((SELECT processed FROM index_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1), 0) AS processed,
                   COALESCE((SELECT total FROM index_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1), 0) AS total
            FROM sources s
            ORDER BY CASE s.state WHEN 'indexing' THEN 0 WHEN 'queued' THEN 1 WHEN 'error' THEN 2 WHEN 'ready' THEN 3 ELSE 4 END,
                     s.name COLLATE NOCASE
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        database.close()


def render_table(rows: list[dict]) -> str:
    headers = ("SOURCE", "STATE", "PROGRESS", "FILES", "DOCS", "CHUNKS", "ERROR")
    values = []
    for row in rows:
        progress = f"{row['progress']}%" if row["state"] in {"queued", "indexing", "ready"} else "—"
        files = f"{row['processed']}/{row['total']}" if row["total"] else "—"
        values.append((row["name"], row["state"], progress, files, str(row["documents"]), str(row["chunks"]), row["error"] or ""))
    widths = [len(header) for header in headers]
    for value in values:
        widths = [max(width, len(str(cell))) for width, cell in zip(widths, value)]
    line = "  ".join(header.ljust(width) for header, width in zip(headers, widths))
    separator = "  ".join("-" * width for width in widths)
    body = ["  ".join(str(cell).ljust(width) for cell, width in zip(value, widths)) for value in values]
    return "\n".join([line, separator, *body])


def main() -> None:
    parser = argparse.ArgumentParser(description="Show Docfish RAG indexing status")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DOCFISH_DB", DEFAULT_DATABASE)))
    args = parser.parse_args()
    try:
        rows = status_rows(args.database)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"status unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(rows, indent=2) if args.json else render_table(rows))


if __name__ == "__main__":
    main()
