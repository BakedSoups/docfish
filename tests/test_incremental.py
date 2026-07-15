import tempfile
import unittest
from pathlib import Path

import numpy

import rag
from docfish.database import Database
from docfish.domain import Source


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [numpy.ones(384, dtype=numpy.float32) for _ in texts]


class FakeVectors:
    def __init__(self):
        self.data = {}

    def collections(self): return set(self.data)
    def exists(self, name): return name in self.data
    def ensure(self, name, size): self.data.setdefault(name, {})
    def upsert(self, name, points): self.data[name].update({point[0]: point for point in points})
    def delete_ids(self, name, ids):
        for id_ in ids: self.data[name].pop(id_, None)


class IncrementalTests(unittest.TestCase):
    def test_only_changed_files_are_reembedded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "guide.md"
            source_path.write_text("# Guide\n" + "focused evidence " * 100)
            original = (rag._database, rag._store, rag._embedder, dict(rag._status))
            try:
                rag._database = Database(root / "state.sqlite")
                rag._store = FakeVectors()
                rag._embedder = FakeEmbedder()
                rag._status = {}
                rag._database.upsert_source(Source("guide", "Guide", "markdown", source_path))
                rag._index("guide")
                first_calls = rag._embedder.calls
                rag._index("guide")
                self.assertEqual(rag._embedder.calls, first_calls)
                source_path.write_text("# Guide\n" + "changed evidence " * 100)
                rag._index("guide")
                self.assertGreater(rag._embedder.calls, first_calls)
            finally:
                rag._database, rag._store, rag._embedder, rag._status = original


if __name__ == "__main__":
    unittest.main()
