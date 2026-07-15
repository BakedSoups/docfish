"""Local HTML documentation catalog and embedded-Qdrant RAG index."""

import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from bs4 import BeautifulSoup
from fastembed import TextEmbedding
from pypdf import PdfReader
from docfish.vector_store import QdrantVectorStore


ROOT = Path(__file__).parent / "Documentation" / "HTML_docs"
PDF_ROOT = Path(__file__).parent / "Documentation" / "PDF_docss"
DB = Path(__file__).parent / "Documentation" / "qdrant"
MANIFEST = DB / "indexed.json"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
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
_pdf_cache = {}

COVER_ASSETS = {
    "godot": ROOT / "godot-docs-html-stable/_static/docs_logo.svg",
    "pandas": ROOT / "pandas/_static/pandas.svg",
    "go": ROOT / "Go.docset/Contents/Resources/Documents/go.dev/images/go-logo-blue.svg",
    "javascript": ROOT / "JavaScript.docset/Contents/Resources/Documents/developer.mozilla.org/favicon.svg",
    "numpy": ROOT / "NumPy.docset/Contents/Resources/Documents/doc/_static/numpylogo.svg",
    "python": ROOT / "python/python-3.14-docs-html/_static/og-image.png",
}

_store = None
_embedder = None
_lock = threading.Lock()
_status = {}
_all_worker_running = False


def store():
    global _store
    if _store is None:
        _store = QdrantVectorStore(QDRANT_URL)
    return _store


def embedder():
    global _embedder
    if _embedder is None:
        # Leave enough CPU available for Ollama and the web UI while indexing.
        _embedder = TextEmbedding(model_name=EMBED_MODEL, threads=4)
    return _embedder


def collection(key):
    return f"angler_{key}"


def all_docs():
    result = dict(DOCS)
    if PDF_ROOT.exists():
        for path in sorted(PDF_ROOT.glob("*.pdf")):
            digest = hashlib.sha1(path.name.encode()).hexdigest()[:10]
            title = re.split(r"\s+-\s+Martin[- ]Kleppmann", path.stem, flags=re.I)[0].strip()
            if title.islower():
                title = title.title()
            result[f"pdf-{digest}"] = (title or path.stem, path)
    return result


def docs_catalog():
    existing = store().collections()
    completed = _completed()
    result = []
    for key, (name, path) in all_docs().items():
        if not path.exists():
            continue
        state = _status.get(key, {})
        result.append({
            "id": key, "name": name,
            "indexed": collection(key) in existing and key in completed and state.get("state") not in ("indexing", "queued"),
            "state": state.get("state", "ready" if collection(key) in existing and key in completed else "not_indexed"),
            "progress": state.get("progress", 0), "pages": state.get("pages", 0),
            "home": _home_page(path),
            "type": "pdf" if path.is_file() and path.suffix.lower() == ".pdf" else "html",
        })
    return result


def _home_page(path):
    if path.is_file():
        return ""
    direct = path / "index.html"
    if direct.exists():
        return "index.html"
    match = next(path.rglob("index.html"), None)
    return match.relative_to(path).as_posix() if match else ""


def safe_doc_path(key, relative=""):
    docs = all_docs()
    if key not in docs:
        raise KeyError(key)
    root = docs[key][1].resolve()
    if root.is_file():
        if relative not in ("", root.name):
            raise ValueError("Invalid documentation path")
        return root
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Invalid documentation path")
    return target


def cover_path(key):
    docs = all_docs()
    if key not in docs:
        raise KeyError(key)
    root = docs[key][1]
    if root.is_file() and root.suffix.lower() == ".pdf":
        output_dir = Path(__file__).parent / "Documentation" / "covers"
        output_dir.mkdir(parents=True, exist_ok=True)
        cover = output_dir / f"{key}.png"
        if not cover.exists() or cover.stat().st_mtime_ns < root.stat().st_mtime_ns:
            prefix = cover.with_suffix("")
            subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-scale-to-x", "480", "-scale-to-y", "-1", "-png", str(root), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return cover
    asset = COVER_ASSETS.get(key)
    return asset if asset and asset.exists() else None


def list_pages(key, query="", limit=80):
    root = safe_doc_path(key)
    if root.is_file() and root.suffix.lower() == ".pdf":
        pages = _pdf_pages(root)
        words = query.lower().split()
        results = []
        for number, text in enumerate(pages, 1):
            if words and not all(word in text.lower() for word in words):
                continue
            snippet = re.sub(r"\s+", " ", text).strip()[:150]
            results.append({"path": f"#page={number}", "title": f"Page {number}", "snippet": snippet})
            if len(results) >= limit:
                break
        return results
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
    if key not in all_docs():
        raise KeyError(key)
    if _status.get(key, {}).get("state") == "indexing":
        return
    _status[key] = {"state": "queued", "progress": 0, "pages": 0}
    threading.Thread(target=_index, args=(key,), daemon=True).start()


def start_all():
    global _all_worker_running
    order = [key for key in ("git", "go", "python", "react", "numpy", "pandas") if key in all_docs()]
    order += [key for key in all_docs() if key.startswith("pdf-")]
    order += [key for key in ("godot", "javascript") if key in all_docs()]
    completed = _completed()
    existing = store().collections()
    pending = [key for key in order if key not in completed or collection(key) not in existing]
    if _all_worker_running:
        return pending
    for key in pending:
        _status[key] = {"state": "queued", "progress": 0, "pages": 0}
    def run():
        global _all_worker_running
        try:
            for key in pending:
                _index(key)
        finally:
            _all_worker_running = False
    _all_worker_running = True
    threading.Thread(target=run, daemon=True).start()
    return pending


def _clean_page(path):
    try:
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
        main = soup.find("main") or soup.find("article") or soup.body or soup
        sections, anchor, parts, seen_blocks = [], "", [], set()
        for element in main.find_all(["h1", "h2", "h3", "p", "pre", "dt", "dd"]):
            text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
            fingerprint = hashlib.sha1(text.encode()).digest() if text else b""
            if not text or fingerprint in seen_blocks:
                continue
            seen_blocks.add(fingerprint)
            if element.name in ("h1", "h2", "h3"):
                if parts:
                    sections.append((anchor, " ".join(parts)[:280_000]))
                parent_with_id = element.find_parent(id=True)
                anchor = element.get("id", "") or (parent_with_id.get("id", "") if parent_with_id else "")
                parts = [text]
            else:
                parts.append(text)
        if parts:
            sections.append((anchor, " ".join(parts)[:280_000]))
        if not sections:
            sections = [("", re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip())]
        return title, sections
    except Exception:
        return path.stem, []


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
        root = all_docs()[key][1]
        if root.is_file() and root.suffix.lower() == ".pdf":
            _index_pdf(key, root)
            return
        excluded = {"404.html", "genindex.html", "py-modindex.html", "search.html"}
        pages = [path for path in root.rglob("*.html") if path.name.lower() not in excluded]
        _status[key] = {"state": "indexing", "progress": 0, "pages": len(pages)}
        try:
            c = store()
            name = collection(key)
            c.recreate(name, 384)
            batch_text, batch_meta = [], []
            for number, path in enumerate(pages, 1):
                title, sections = _clean_page(path)
                rel = path.relative_to(root).as_posix()
                chunk_no = 0
                for anchor, text in sections:
                    if len(text) < 80:
                        continue
                    source_path = f"{rel}#{anchor}" if anchor else rel
                    for chunk in _chunks(text):
                        batch_text.append(chunk)
                        batch_meta.append((source_path, title, chunk_no))
                        chunk_no += 1
                        if chunk_no >= 500:
                            break
                        if len(batch_text) >= 48:
                            _upsert(c, name, batch_text, batch_meta)
                            batch_text, batch_meta = [], []
                    if chunk_no >= 500:
                        break
                if number % 25 == 0:
                    _status[key]["progress"] = round(number * 100 / len(pages))
            if batch_text:
                _upsert(c, name, batch_text, batch_meta)
            _status[key] = {"state": "ready", "progress": 100, "pages": len(pages)}
            _mark_complete(key)
        except Exception as exc:
            _status[key] = {"state": "error", "progress": 0, "pages": len(pages), "error": str(exc)}


def _upsert(c, name, texts, metadata):
    vectors = list(embedder().embed(texts))
    points = []
    for text, meta, vector in zip(texts, metadata, vectors):
        path, title, chunk_no, *page_value = meta
        identity = hashlib.sha1(f"{path}:{chunk_no}".encode()).hexdigest()[:32]
        points.append((identity, vector.tolist(), {
            "path": path, "title": title, "chunk": chunk_no, "text": text,
            **({"page": page_value[0]} if page_value else {}),
        }))
    c.upsert(name, points)


def search(key, query, limit=6):
    name = collection(key)
    if not store().exists(name) or key not in _completed():
        raise RuntimeError("This documentation set has not been indexed yet")
    vector = list(embedder().embed([query]))[0].tolist()
    response = store().query(name, vector, max(24, limit * 4))
    terms = {word for word in re.findall(r"[a-z0-9_]{2,}", query.lower()) if word not in {"the", "and", "for", "with", "how", "what", "why"}}
    candidates = []
    for point in response:
        payload = point.payload
        words = set(re.findall(r"[a-z0-9_]{2,}", payload.get("text", "").lower()))
        lexical = len(terms & words) / max(1, len(terms))
        candidates.append((point.score * .72 + lexical * .28, payload))
    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen, seen = [], []
    for score, payload in candidates:
        identity = (payload.get("path"), payload.get("chunk", 0))
        if any(path == identity[0] and abs(chunk - identity[1]) <= 1 for path, chunk in seen):
            continue
        seen.append(identity)
        expanded = _neighbor_text(name, payload)
        chosen.append({"score": round(score, 4), **payload, "text": expanded})
        if len(chosen) >= limit:
            break
    return chosen


def _neighbor_text(name, payload):
    path, chunk = payload.get("path"), payload.get("chunk", 0)
    if not path:
        return payload.get("text", "")
    points = store().neighbors(name, path, chunk)
    if not points:
        return payload.get("text", "")
    return " ".join(point.get("text", "") for point in points)


def _pdf_pages(path):
    cache_key = (str(path), path.stat().st_mtime_ns)
    if cache_key not in _pdf_cache:
        reader = PdfReader(path)
        _pdf_cache.clear()
        _pdf_cache[cache_key] = [(page.extract_text() or "") for page in reader.pages]
    return _pdf_cache[cache_key]


def _index_pdf(key, path):
    pages = _pdf_pages(path)
    _status[key] = {"state": "indexing", "progress": 0, "pages": len(pages)}
    try:
        c = store()
        name = collection(key)
        c.recreate(name, 384)
        batch_text, batch_meta = [], []
        for page_no, text in enumerate(pages, 1):
            clean = re.sub(r"\s+", " ", text).strip()
            for chunk_no, chunk in enumerate(_chunks(clean)):
                if len(chunk) < 80:
                    continue
                batch_text.append(chunk)
                batch_meta.append((f"#page={page_no}", f"{path.stem} — page {page_no}", chunk_no, page_no))
                if len(batch_text) >= 48:
                    _upsert(c, name, batch_text, batch_meta)
                    batch_text, batch_meta = [], []
            if page_no % 10 == 0:
                _status[key]["progress"] = round(page_no * 100 / len(pages))
        if batch_text:
            _upsert(c, name, batch_text, batch_meta)
        _status[key] = {"state": "ready", "progress": 100, "pages": len(pages)}
        _mark_complete(key)
    except Exception as exc:
        _status[key] = {"state": "error", "progress": 0, "pages": len(pages), "error": str(exc)}


def _completed():
    try:
        return set(json.loads(MANIFEST.read_text()))
    except (OSError, ValueError):
        return set()


def _mark_complete(key):
    completed = _completed()
    completed.add(key)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(completed)))
    temporary.replace(MANIFEST)
