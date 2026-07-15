"""First-run checks and generated-storage reporting."""

import os
import urllib.error
import urllib.request
from pathlib import Path


def health(database, vector_store, ollama_url: str, embedding_model: str) -> dict:
    checks = {}
    try:
        with urllib.request.urlopen(ollama_url.rstrip("/") + "/api/tags", timeout=3) as response:
            checks["ollama"] = {"ok": response.status == 200, "url": ollama_url}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        checks["ollama"] = {"ok": False, "url": ollama_url, "error": str(exc)}
    try:
        database.connection().execute("SELECT 1").fetchone()
        checks["database"] = {"ok": os.access(database.path.parent, os.W_OK), "path": str(database.path)}
    except Exception as exc:
        checks["database"] = {"ok": False, "path": str(database.path), "error": str(exc)}
    try:
        collections = sorted(vector_store.collections())
        checks["vectors"] = {"ok": True, "collections": len(collections)}
    except Exception as exc:
        checks["vectors"] = {"ok": False, "error": str(exc)}
    cache = Path.home() / ".cache" / "huggingface"
    checks["embedding"] = {"ok": cache.exists(), "model": embedding_model, "cache": str(cache), "note": "Downloaded on first index if absent"}
    return {"ok": all(item["ok"] for key, item in checks.items() if key != "embedding"), "checks": checks}
