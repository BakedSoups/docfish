"""Explicit, checksum-verified optional content packs."""

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from .domain import Source


class ContentPacks:
    def __init__(self, database, manifest_path: Path, install_root: Path):
        self.database = database
        self.manifest_path = manifest_path
        self.install_root = install_root

    def list(self) -> list[dict]:
        manifests = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else []
        installed = {row["id"]: dict(row) for row in self.database.connection().execute("SELECT * FROM content_packs")}
        return [{**item, "state": installed.get(item["id"], {}).get("state", "available")} for item in manifests]

    def get(self, pack_id: str) -> dict:
        pack = next((item for item in self.list() if item["id"] == pack_id), None)
        if not pack:
            raise KeyError(pack_id)
        return pack

    def install(self, pack_id: str) -> dict:
        pack = self.get(pack_id)
        target = self.install_root / pack_id
        temporary = self.install_root / f".{pack_id}.download"
        staging = self.install_root / f".{pack_id}.staging"
        self.install_root.mkdir(parents=True, exist_ok=True)
        self._set_state(pack, "downloading", target)
        try:
            urllib.request.urlretrieve(pack["url"], temporary)
            self._verify_file(temporary, pack)
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            if pack.get("archive") == "zip":
                self._extract_zip(temporary, staging)
            else:
                raise ValueError("Unsupported content pack archive")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
            metadata = {"manifest": pack, "tree_sha256": self._tree_digest(target)}
            (target / ".docfish-pack.json").write_text(json.dumps(metadata, indent=2))
            source_path = self._content_root(target)
            self.database.upsert_source(Source(pack_id, pack["name"], pack["kind"], source_path))
            self._set_state(pack, "installed", target)
            return {**pack, "state": "installed", "path": str(source_path)}
        except Exception:
            self._set_state(pack, "error", target)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging)

    def verify(self, pack_id: str) -> bool:
        pack = self.get(pack_id)
        row = self.database.connection().execute("SELECT installed_path FROM content_packs WHERE id=?", (pack_id,)).fetchone()
        if not row:
            return False
        target = Path(row[0])
        try:
            metadata = json.loads((target / ".docfish-pack.json").read_text())
        except (OSError, ValueError):
            return False
        return metadata.get("manifest", {}).get("sha256") == pack["sha256"] and metadata.get("tree_sha256") == self._tree_digest(target)

    def uninstall(self, pack_id: str) -> None:
        pack = self.get(pack_id)
        row = self.database.connection().execute("SELECT installed_path FROM content_packs WHERE id=?", (pack_id,)).fetchone()
        if row:
            path = Path(row[0]).resolve()
            root = self.install_root.resolve()
            if path != root and root in path.parents and path.exists():
                shutil.rmtree(path)
        self.database.remove_source(pack_id)
        with self.database.transaction() as db:
            db.execute("DELETE FROM content_packs WHERE id=?", (pack_id,))

    def _set_state(self, pack: dict, state: str, target: Path) -> None:
        with self.database.transaction() as db:
            db.execute("""
                INSERT INTO content_packs(id, manifest, state, installed_path, installed_at)
                VALUES(?, ?, ?, ?, CASE WHEN ?='installed' THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT(id) DO UPDATE SET manifest=excluded.manifest, state=excluded.state,
                    installed_path=excluded.installed_path,
                    installed_at=CASE WHEN excluded.state='installed' THEN CURRENT_TIMESTAMP ELSE content_packs.installed_at END
            """, (pack["id"], json.dumps(pack), state, str(target), state))

    @staticmethod
    def _verify_file(path: Path, pack: dict) -> None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != pack["sha256"]:
            raise ValueError("Content pack checksum did not match its manifest")
        if pack.get("download_bytes") and path.stat().st_size != pack["download_bytes"]:
            raise ValueError("Content pack size did not match its manifest")

    @staticmethod
    def _extract_zip(archive: Path, target: Path) -> None:
        with zipfile.ZipFile(archive) as bundle:
            root = target.resolve()
            for member in bundle.infolist():
                destination = (target / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise ValueError("Unsafe path in content pack")
            bundle.extractall(target)

    @staticmethod
    def _content_root(target: Path) -> Path:
        children = [path for path in target.iterdir() if path.is_dir()]
        return children[0] if len(children) == 1 else target

    @staticmethod
    def _tree_digest(target: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in target.rglob("*") if item.is_file() and item.name != ".docfish-pack.json"):
            digest.update(path.relative_to(target).as_posix().encode())
            with path.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
        return digest.hexdigest()
