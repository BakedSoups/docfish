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
        self.batch_sizes = []

    def embed(self, texts, **kwargs):
        self.calls += len(texts)
        self.batch_sizes.append(len(texts))
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
            source_path.write_text("# Guide\n" + "focused evidence " * 16000)
            original = (rag._database, rag._store, rag._embedder, dict(rag._status))
            test_database = None
            try:
                test_database = Database(root / "state.sqlite")
                rag._database = test_database
                rag._store = FakeVectors()
                rag._embedder = FakeEmbedder()
                rag._status = {}
                rag._database.upsert_source(Source("guide", "Guide", "markdown", source_path))
                rag._index("guide")
                first_calls = rag._embedder.calls
                self.assertGreater(len(rag._embedder.batch_sizes), 1)
                self.assertLessEqual(max(rag._embedder.batch_sizes), rag.EMBED_BATCH_SIZE)
                rag._index("guide")
                self.assertEqual(rag._embedder.calls, first_calls)
                source_path.write_text("# Guide\n" + "changed evidence " * 16000)
                rag._index("guide")
                self.assertGreater(rag._embedder.calls, first_calls)
            finally:
                if test_database is not None:
                    test_database.close()
                rag._database, rag._store, rag._embedder, rag._status = original


if __name__ == "__main__":
    unittest.main()
