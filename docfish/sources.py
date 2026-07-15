"""Source validation, discovery, and storage estimates."""

import fnmatch
import hashlib
import re
from pathlib import Path

from .adapters import supported, supported_for_kind
from .domain import Source


DEFAULT_HTML_EXCLUDES = ["404.html", "genindex*.html", "py-modindex.html", "search.html"]


def source_id(name: str, path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "source"
    digest = hashlib.sha1(str(path).encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


def create_source(name: str, kind: str, raw_path: str, include=None, exclude=None) -> Source:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ValueError("Source path does not exist")
    if not name.strip():
        raise ValueError("Source name is required")
    detected = detect_kind(path)
    if kind in ("", "auto", None):
        kind = detected
    if kind not in {"html", "pdf", "markdown", "text"}:
        raise ValueError("Unsupported source type")
    if path.is_file() and not supported(path):
        raise ValueError("Unsupported file type")
    excludes = list(exclude or [])
    if kind == "html":
        excludes = list(dict.fromkeys([*excludes, *DEFAULT_HTML_EXCLUDES]))
    return Source(
        source_id(name, path), name.strip(), kind, path,
        list(include or []), excludes,
    )


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"} or path.is_dir():
        return "html"
    return "text"


def files_for(source: Source):
    candidates = [source.path] if source.path.is_file() else source.path.rglob("*")
    for path in candidates:
        if not path.is_file() or not supported_for_kind(path, source.kind):
            continue
        relative = path.name if source.path.is_file() else path.relative_to(source.path).as_posix()
        if source.include and not any(fnmatch.fnmatch(relative, pattern) for pattern in source.include):
            continue
        if source.exclude and any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in source.exclude):
            continue
        yield path


def estimate(source: Source) -> dict:
    count = 0
    source_bytes = 0
    for path in files_for(source):
        count += 1
        source_bytes += path.stat().st_size
    # Text vectors and metadata vary, but this is a useful conservative preview.
    estimated_index_bytes = max(0, round(source_bytes * 0.65))
    return {"files": count, "source_bytes": source_bytes, "estimated_index_bytes": estimated_index_bytes}
