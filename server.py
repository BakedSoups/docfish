#!/usr/bin/env python3
"""Tiny dependency-free web server and Ollama API proxy."""

import json
import mimetypes
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).parent / "static"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/models":
            self.proxy("/api/tags")
        elif parsed.path == "/api/docs":
            import rag
            self.json_response({"docs": rag.docs_catalog()})
        elif parsed.path == "/api/sources":
            import rag
            self.json_response({"sources": rag.docs_catalog()})
        elif parsed.path == "/api/notes":
            import rag
            self.json_response({"notes": rag.database().list_notes()})
        elif parsed.path == "/api/content-packs":
            import rag
            self.json_response({"packs": rag.content_packs().list()})
        elif parsed.path == "/api/health":
            import rag
            from docfish.diagnostics import health
            self.json_response(health(rag.database(), rag.store(), OLLAMA, rag.EMBED_MODEL))
        elif parsed.path == "/api/storage":
            import rag
            self.json_response({"sources": rag.database().source_storage()})
        elif parsed.path == "/api/settings/export":
            import rag
            self.json_response({"version": 1, "sources": rag.export_sources()})
        elif parsed.path == "/api/notes/export":
            import rag
            from docfish.learning import note_markdown
            args = urllib.parse.parse_qs(parsed.query)
            try:
                note = rag.database().get_note(int(args.get("id", ["0"])[0]))
            except ValueError:
                note = None
            if not note:
                self.send_error(404)
                return
            if args.get("format", ["markdown"])[0] == "json":
                self.json_response(note)
            else:
                payload = note_markdown(note).encode()
                self.send_response(200); self.send_header("Content-Type", "text/markdown; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
        elif parsed.path.startswith("/api/sources/"):
            import rag
            key = urllib.parse.unquote(parsed.path.removeprefix("/api/sources/").split("/", 1)[0])
            try:
                self.json_response({"source": rag.source_details(key)})
            except KeyError:
                self.json_response({"error": "Unknown source"}, 404)
        elif parsed.path == "/api/docs/pages":
            import rag
            args = urllib.parse.parse_qs(parsed.query)
            try:
                pages = rag.list_pages(args.get("doc", [""])[0], args.get("q", [""])[0])
                self.json_response({"pages": pages})
            except (KeyError, ValueError) as exc:
                self.json_response({"error": str(exc)}, 400)
        elif parsed.path == "/api/docs/cover":
            import rag
            args = urllib.parse.parse_qs(parsed.query)
            try:
                path = rag.cover_path(args.get("doc", [""])[0])
                if path is None:
                    self.send_error(404)
                    return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (KeyError, OSError, subprocess.SubprocessError):
                self.send_error(404)
        elif parsed.path.startswith("/docs/"):
            self.serve_doc(parsed.path)
        elif parsed.path == "/anglerfish_idle.gif":
            asset = Path(__file__).parent / "anglerfish_idle.gif"
            if not asset.exists():
                self.send_error(404)
                return
            data = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()

    def end_headers(self):
        if self.path.startswith(("/app.js", "/styles.css", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_POST(self):
        if self.path == "/api/chat":
            self.proxy("/api/chat", self.rfile.read(int(self.headers.get("Content-Length", 0))))
        elif self.path == "/api/docs/index":
            import rag
            try:
                body = self.read_json()
                rag.start_index(body.get("doc", ""))
                self.json_response({"ok": True}, 202)
            except KeyError as exc:
                self.json_response({"error": f"Unknown documentation set: {exc}"}, 400)
        elif self.path == "/api/docs/index-all":
            import rag
            queued = rag.start_all()
            self.json_response({"ok": True, "queued": queued}, 202)
        elif self.path == "/api/sources":
            import rag
            try:
                self.json_response({"source": rag.add_source(self.read_json())}, 201)
            except (ValueError, OSError) as exc:
                self.json_response({"error": str(exc)}, 400)
        elif self.path.startswith("/api/sources/") and self.path.endswith("/index") and not self.path.endswith("/remove-index"):
            import rag
            key = urllib.parse.unquote(self.path.removeprefix("/api/sources/").removesuffix("/index").strip("/"))
            try:
                rag.start_index(key)
                self.json_response({"ok": True}, 202)
            except KeyError:
                self.json_response({"error": "Unknown source"}, 404)
        elif self.path.startswith("/api/sources/") and self.path.endswith("/cancel"):
            import rag
            key = urllib.parse.unquote(self.path.removeprefix("/api/sources/").removesuffix("/cancel").strip("/"))
            try:
                self.json_response({"ok": rag.cancel_index(key)}, 202)
            except KeyError:
                self.json_response({"error": "Unknown source"}, 404)
        elif self.path == "/api/rag/search":
            import rag
            from docfish.learning import evidence_status
            try:
                body = self.read_json()
                results = rag.search(body.get("doc", ""), body.get("query", ""))
                self.json_response({"results": results, "evidence": evidence_status(results)})
            except (KeyError, RuntimeError, ValueError) as exc:
                self.json_response({"error": str(exc)}, 400)
        elif self.path == "/api/questions/craft":
            from docfish.learning import crafting_prompt
            try:
                body = self.read_json()
                prompt = crafting_prompt(body, body.get("mode", "improve"))
                result = self.ollama_chat(body.get("model", ""), prompt)
                self.json_response({"proposal": result})
            except (ValueError, urllib.error.URLError, TimeoutError) as exc:
                self.json_response({"error": str(exc)}, 502)
        elif self.path == "/api/questions/validate":
            from docfish.learning import validate_citations
            body = self.read_json()
            self.json_response(validate_citations(body.get("answer", ""), int(body.get("source_count", 0))))
        elif self.path == "/api/notes":
            import rag
            body = self.read_json()
            note = rag.database().save_note(
                body.get("question", ""), body.get("crafted_prompt", ""),
                body.get("answer", ""), body.get("citations", []), body.get("correction", ""),
            )
            self.json_response({"note": note}, 201)
        elif self.path == "/api/learning/action":
            from docfish.learning import learning_prompt
            try:
                body = self.read_json()
                prompt = learning_prompt(body.get("mode", "explain"), body.get("question", ""), body.get("answer", ""), body.get("citations", []))
                self.json_response({"result": self.ollama_chat(body.get("model", ""), prompt)})
            except (ValueError, urllib.error.URLError, TimeoutError) as exc:
                self.json_response({"error": str(exc)}, 502)
        elif self.path.startswith("/api/content-packs/") and self.path.endswith("/install"):
            import rag
            pack_id = urllib.parse.unquote(self.path.removeprefix("/api/content-packs/").removesuffix("/install").strip("/"))
            try:
                self.json_response({"pack": rag.content_packs().install(pack_id)}, 201)
            except (KeyError, OSError, ValueError, urllib.error.URLError) as exc:
                self.json_response({"error": str(exc)}, 400)
        elif self.path.startswith("/api/content-packs/") and self.path.endswith("/verify"):
            import rag
            pack_id = urllib.parse.unquote(self.path.removeprefix("/api/content-packs/").removesuffix("/verify").strip("/"))
            try:
                self.json_response({"valid": rag.content_packs().verify(pack_id)})
            except KeyError:
                self.json_response({"error": "Unknown content pack"}, 404)
        elif self.path.startswith("/api/sources/") and self.path.endswith("/remove-index"):
            import rag
            key = urllib.parse.unquote(self.path.removeprefix("/api/sources/").removesuffix("/remove-index").strip("/"))
            try:
                rag.remove_index(key)
                self.json_response({"ok": True})
            except KeyError:
                self.json_response({"error": "Unknown source"}, 404)
        elif self.path == "/api/cleanup":
            import rag
            self.json_response({"removed": rag.cleanup_stale_indexes()})
        elif self.path == "/api/settings/import":
            import rag
            try:
                self.json_response({"imported": rag.import_sources(self.read_json().get("sources", []))})
            except (ValueError, OSError) as exc:
                self.json_response({"error": str(exc)}, 400)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/content-packs/"):
            import rag
            pack_id = urllib.parse.unquote(self.path.removeprefix("/api/content-packs/").strip("/"))
            try:
                rag.content_packs().uninstall(pack_id)
                self.json_response({"ok": True})
            except KeyError:
                self.json_response({"error": "Unknown content pack"}, 404)
        elif self.path.startswith("/api/sources/"):
            import rag
            key = urllib.parse.unquote(self.path.removeprefix("/api/sources/").strip("/"))
            try:
                rag.remove_source(key)
                self.json_response({"ok": True})
            except KeyError:
                self.json_response({"error": "Unknown source"}, 404)
        else:
            self.send_error(404)

    def read_json(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def json_response(self, value, status=200):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def serve_doc(self, url_path):
        import rag
        parts = url_path.split("/", 3)
        if len(parts) < 3:
            self.send_error(404)
            return
        key = urllib.parse.unquote(parts[2])
        relative = urllib.parse.unquote(parts[3]) if len(parts) > 3 else ""
        try:
            path = rag.safe_doc_path(key, relative)
            if path.is_dir():
                path = path / "index.html"
            data = path.read_bytes()
        except (KeyError, ValueError, OSError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def proxy(self, path, body=None):
        try:
            req = urllib.request.Request(
                OLLAMA + path,
                data=body,
                headers={"Content-Type": "application/json"} if body is not None else {},
                method="POST" if body is not None else "GET",
            )
            with urllib.request.urlopen(req, timeout=300) as response:
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while chunk := response.read(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (urllib.error.URLError, TimeoutError) as exc:
            message = getattr(exc, "reason", exc)
            payload = json.dumps({"error": f"Could not reach Ollama at {OLLAMA}: {message}"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def ollama_chat(self, model, prompt):
        if not model:
            raise ValueError("Select a local model first")
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "You are Docfish, a concise learning assistant for programmers. Follow the requested learning task and stay grounded in the supplied material."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }).encode()
        request = urllib.request.Request(
            OLLAMA + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            value = json.load(response)
        return value.get("message", {}).get("content", "").strip()

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Ollama UI: http://127.0.0.1:{port}")
    print(f"Ollama API: {OLLAMA}")
    if os.environ.get("AUTO_INDEX", "0").lower() in ("1", "true", "yes"):
        import rag
        print(f"Queued documentation indexes: {', '.join(rag.start_all()) or 'all complete'}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
