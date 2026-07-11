"""Local HTML documentation catalog and embedded-Qdrant RAG index."""

import hashlib
import re
import threading
from pathlib import Path

from bs4 import BeautifulSoup
from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models


ROOT = Path(__file__).parent / "Documentation" / "HTML_docs"
DB = Path(__file__).parent / "Documentation" / "qdrant"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DOCS = {
    "godot": ("Godot", ROOT / "godot-docs-html-stable"),
    "pandas": ("Pandas", ROOT / "pandas"),
    "go": ("Go", ROOT / "Go.docset/Contents/Resources/Documents"),
    "javascript": ("JavaScript / MDN", ROOT / "JavaScript.docset/Contents/Resources/Documents"),
    "numpy": ("NumPy", ROOT / "NumPy.docset/Contents/Resources/Documents"),
    "react": ("React", ROOT / "React.docset/Contents/Resources/Documents"),
    "python": ("Python", ROOT / "python/python-3.14-docs-html"),
    "git": ("Git", ROOT / "git"),
}

_client = None
_embedder = None
_lock = threading.Lock()
_status = {}


def client():
    global _client
    if _client is None:
        DB.mkdir(parents=True, exist_ok=True)
        _client = QdrantClient(path=str(DB))
    return _client


def embedder():
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _embedder


def collection(key):
    return f"angler_{key}"


def docs_catalog():
    existing = {c.name for c in client().get_collections().collections}
    result = []
    for key, (name, path) in DOCS.items():
        if not path.exists():
            continue
        state = _status.get(key, {})
        result.append({
            "id": key, "name": name,
            "indexed": collection(key) in existing and state.get("state") != "indexing",
            "state": state.get("state", "ready" if collection(key) in existing else "not_indexed"),
            "progress": state.get("progress", 0), "pages": state.get("pages", 0),
            "home": _home_page(path),
        })
    return result


def _home_page(path):
    direct = path / "index.html"
    if direct.exists():
        return "index.html"
    match = next(path.rglob("index.html"), None)
    return match.relative_to(path).as_posix() if match else ""


def safe_doc_path(key, relative=""):
    if key not in DOCS:
        raise KeyError(key)
    root = DOCS[key][1].resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Invalid documentation path")
    return target


def list_pages(key, query="", limit=80):
    root = safe_doc_path(key)
    words = query.lower().split()
    pages = []
    for path in root.rglob("*.html"):
        rel = path.relative_to(root).as_posix()
        if words and not all(word in rel.lower() for word in words):
            continue
        pages.append({"path": rel, "title": path.stem.replace("_", " ").replace("-", " ").title()})
        if len(pages) >= limit:
            break
    return pages


def start_index(key):
    if key not in DOCS:
        raise KeyError(key)
    if _status.get(key, {}).get("state") == "indexing":
        return
    threading.Thread(target=_index, args=(key,), daemon=True).start()


def _clean_page(path):
    try:
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip()
        return title, text
    except Exception:
        return path.stem, ""


def _chunks(text, size=1400, overlap=180):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            split = text.rfind(" ", start + size // 2, end)
            end = split if split > start else end
        yield text[start:end]
        start = max(end - overlap, start + 1)


def _index(key):
    with _lock:
        root = DOCS[key][1]
        pages = list(root.rglob("*.html"))
        _status[key] = {"state": "indexing", "progress": 0, "pages": len(pages)}
        try:
            c = client()
            name = collection(key)
            if c.collection_exists(name):
                c.delete_collection(name)
            c.create_collection(name, vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE))
            batch_text, batch_meta = [], []
            for number, path in enumerate(pages, 1):
                title, text = _clean_page(path)
                if len(text) >= 80:
                    rel = path.relative_to(root).as_posix()
                    for chunk_no, chunk in enumerate(_chunks(text)):
                        batch_text.append(chunk)
                        batch_meta.append((rel, title, chunk_no))
                        if len(batch_text) >= 48:
                            _upsert(c, name, batch_text, batch_meta)
                            batch_text, batch_meta = [], []
                if number % 25 == 0:
                    _status[key]["progress"] = round(number * 100 / len(pages))
            if batch_text:
                _upsert(c, name, batch_text, batch_meta)
            _status[key] = {"state": "ready", "progress": 100, "pages": len(pages)}
        except Exception as exc:
            _status[key] = {"state": "error", "progress": 0, "pages": len(pages), "error": str(exc)}


def _upsert(c, name, texts, metadata):
    vectors = list(embedder().embed(texts))
    points = []
    for text, meta, vector in zip(texts, metadata, vectors):
        path, title, chunk_no = meta
        identity = hashlib.sha1(f"{path}:{chunk_no}".encode()).hexdigest()[:32]
        points.append(models.PointStruct(id=identity, vector=vector.tolist(), payload={
            "path": path, "title": title, "chunk": chunk_no, "text": text,
        }))
    c.upsert(collection_name=name, points=points, wait=True)


def search(key, query, limit=5):
    name = collection(key)
    if not client().collection_exists(name):
        raise RuntimeError("This documentation set has not been indexed yet")
    vector = list(embedder().embed([query]))[0].tolist()
    response = client().query_points(collection_name=name, query=vector, limit=limit, with_payload=True)
    return [{"score": round(point.score, 4), **point.payload} for point in response.points]
