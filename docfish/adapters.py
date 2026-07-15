"""Document adapters that normalize supported files into cited chunks."""

import hashlib
import re
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .domain import Chunk, Source


PARSER_VERSION = "1"


def split_text(text: str, size: int = 1400, overlap: int = 180):
    text = re.sub(r"[ \t]+", " ", text).strip()
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            split = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            end = split if split > start else end
        value = text[start:end].strip()
        if value:
            yield value
        start = max(end - overlap, start + 1)


def chunk_id(source_id: str, path: str, position: int) -> str:
    return hashlib.sha1(f"{source_id}:{path}:{position}".encode()).hexdigest()[:32]


class HtmlAdapter:
    suffixes = {".html", ".htm"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    def parse(self, source: Source, path: Path) -> list[Chunk]:
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text("\n", strip=True)
        return make_chunks(source, path, title, text)


class PdfAdapter:
    suffixes = {".pdf"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, source: Source, path: Path) -> list[Chunk]:
        result = []
        relative = relative_path(source, path)
        for page, pdf_page in enumerate(PdfReader(path).pages, 1):
            title = f"{path.stem} — page {page}"
            for position, text in enumerate(split_text(pdf_page.extract_text() or "")):
                result.append(Chunk(
                    chunk_id(source.id, f"{relative}#page={page}", position), source.id,
                    f"{relative}#page={page}", title, text, position, page=page,
                ))
        return result


class MarkdownAdapter:
    suffixes = {".md", ".markdown"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    def parse(self, source: Source, path: Path) -> list[Chunk]:
        raw = path.read_text(encoding="utf-8", errors="replace")
        title_match = re.search(r"^#\s+(.+)$", raw, re.M)
        title = title_match.group(1).strip() if title_match else path.stem
        text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", raw, flags=re.S)
        text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
        return make_chunks(source, path, title, text)


class TextAdapter:
    suffixes = {".txt", ".rst", ".log", ".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".h", ".cpp"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    def parse(self, source: Source, path: Path) -> list[Chunk]:
        return make_chunks(source, path, path.stem, path.read_text(encoding="utf-8", errors="replace"))


ADAPTERS = (HtmlAdapter(), PdfAdapter(), MarkdownAdapter(), TextAdapter())


def adapter_for(path: Path):
    return next((adapter for adapter in ADAPTERS if adapter.supports(path)), None)


def supported(path: Path) -> bool:
    return adapter_for(path) is not None


def relative_path(source: Source, path: Path) -> str:
    if source.path.is_file():
        return path.name
    return path.relative_to(source.path).as_posix()


def make_chunks(source: Source, path: Path, title: str, text: str) -> list[Chunk]:
    relative = relative_path(source, path)
    return [
        Chunk(chunk_id(source.id, relative, position), source.id, relative, title, value, position)
        for position, value in enumerate(split_text(text)) if len(value) >= 40
    ]

