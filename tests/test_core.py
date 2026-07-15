import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from docfish.adapters import HtmlAdapter, MarkdownAdapter, TextAdapter, split_text, supported_for_kind
from docfish.content_packs import ContentPacks
from docfish.database import Database
from docfish.domain import Chunk, Source
from docfish.learning import evidence_status, structured_question, validate_citations
from docfish.sources import create_source, estimate
from docfish.vector_store import SQLiteVectorStore


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "state.sqlite")

    def tearDown(self):
        self.temporary.cleanup()

    def test_html_markdown_and_text_adapters(self):
        html = self.root / "guide.html"
        html.write_text("<html><title>Guide</title><main><h1>Search</h1><p>" + "semantic evidence " * 20 + "</p></main></html>")
        markdown = self.root / "notes.md"
        markdown.write_text("# Notes\n\n" + "grounded learning " * 20)
        text = self.root / "error.txt"
        text.write_text("exact error message " * 20)
        source = Source("docs", "Docs", "html", self.root)
        self.assertEqual(HtmlAdapter().parse(source, html)[0].title, "Guide")
        self.assertEqual(MarkdownAdapter().parse(source, markdown)[0].title, "Notes")
        self.assertTrue(TextAdapter().parse(source, text))

    def test_chunk_overlap_and_question_shot_limit(self):
        chunks = list(split_text("word " * 1000, size=200, overlap=20))
        self.assertGreater(len(chunks), 2)
        prompt = structured_question({"question": "Why?", "examples": ["1", "2", "3", "4"]})
        self.assertIn("Example 3", prompt)
        self.assertNotIn("Example 4", prompt)

    def test_source_validation_and_estimate(self):
        path = self.root / "notes.md"
        path.write_text("notes")
        source = create_source("Notes", "auto", str(path))
        self.assertEqual(source.kind, "markdown")
        self.assertEqual(estimate(source)["files"], 1)
        self.assertTrue(supported_for_kind(path, "markdown"))
        self.assertFalse(supported_for_kind(path, "html"))
        with self.assertRaises(ValueError):
            create_source("Missing", "auto", str(self.root / "missing"))

    def test_jobs_recover_and_indexes_clear_without_source_deletion(self):
        source_file = self.root / "source.txt"
        source_file.write_text("keep me")
        self.database.upsert_source(Source("s", "Source", "text", source_file))
        job = self.database.create_job("s", 4)
        self.database.update_job(job, "indexing", 2, 4)
        self.database._local.connection.close()
        del self.database._local.connection
        reopened = Database(self.database.path)
        self.assertEqual(reopened.latest_job("s")["state"], "interrupted")
        chunk = Chunk("c", "s", "source.txt", "Source", "searchable text", 0)
        reopened.replace_document("s", "source.txt", "hash", 1, 7, "1", "Source", [chunk], [[0.0] * 384])
        reopened.clear_source_index("s")
        self.assertTrue(source_file.exists())
        self.assertEqual(reopened.source_storage()[0]["chunks"], 0)

    def test_lexical_and_embedded_vector_search(self):
        self.database.upsert_source(Source("s", "Source", "text", self.root / "source.txt"))
        chunk = Chunk("alpha", "s", "source.txt", "Source", "semantic retrieval evidence", 0)
        self.database.replace_document("s", "source.txt", "hash", 1, 1, "1", "Source", [chunk], [[1.0, 0.0]])
        self.assertEqual(self.database.lexical_search("s", "semantic")[0]["id"], "alpha")
        vectors = SQLiteVectorStore(self.database)
        vectors.upsert("docfish_s", [("alpha", [1.0, 0.0], chunk.payload()), ("beta", [0.0, 1.0], {"path": "b", "chunk": 0})])
        self.assertEqual(vectors.query("docfish_s", [1.0, 0.0], 1)[0].payload["path"], "source.txt")

    def test_grounding_requires_relevant_evidence_and_valid_citations(self):
        self.assertFalse(evidence_status([])["sufficient"])
        self.assertTrue(evidence_status([{"score": 0.7}])["sufficient"])
        self.assertTrue(validate_citations("Supported [1].", 2)["grounded"])
        self.assertEqual(validate_citations("Invented [3].", 2)["invalid"], [3])

    def test_content_pack_install_verify_and_uninstall(self):
        archive = self.root / "pack.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("docs/index.html", "<h1>Docs</h1>")
        manifest = [{
            "id": "demo", "name": "Demo", "version": "1", "kind": "html", "license": "Test",
            "url": archive.as_uri(), "download_bytes": archive.stat().st_size,
            "installed_bytes_estimate": 20, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "archive": "zip",
        }]
        manifest_path = self.root / "packs.json"
        manifest_path.write_text(json.dumps(manifest))
        packs = ContentPacks(self.database, manifest_path, self.root / "installed")
        installed = packs.install("demo")
        self.assertEqual(installed["state"], "installed")
        self.assertTrue(packs.verify("demo"))
        packs.uninstall("demo")
        self.assertFalse((self.root / "installed" / "demo").exists())

    def test_content_pack_rejects_path_traversal(self):
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.txt", "no")
        with self.assertRaises(ValueError):
            ContentPacks._extract_zip(archive, self.root / "target")


if __name__ == "__main__":
    unittest.main()
