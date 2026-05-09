from __future__ import annotations

import re
from pathlib import Path

from audiobook_narrator.models import ChapterManifest
from audiobook_narrator.storage import ProjectStore


def chapter_id_from_title(title: str, existing_count: int) -> str:
    ascii_slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return ascii_slug[:40] or f"ch{existing_count + 1:02d}"


def load_input_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".epub":
        return _load_epub(path)
    raise ValueError(f"Unsupported input type: {path.suffix}. Use .txt, .md, or .epub.")


def _load_epub(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ImportError as exc:
        raise RuntimeError("EPUB support requires: python3 -m pip install -e '.[epub]'") from exc

    book = epub.read_epub(str(path))
    chunks: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        text = soup.get_text("\n")
        if text.strip():
            chunks.append(text)
    return "\n\n".join(chunks)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def ingest_chapter(
    store: ProjectStore,
    project_id: str,
    input_path: Path,
    chapter_title: str,
    chapter_id: str | None = None,
) -> ChapterManifest:
    paths = store.paths(project_id)
    existing = list(paths.source.glob("*.txt"))
    chapter_id = chapter_id or chapter_id_from_title(chapter_title, len(existing))
    text = normalize_text(load_input_text(input_path))
    source_path = paths.source / f"{chapter_id}.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(text, encoding="utf-8")
    manifest = ChapterManifest(
        chapter_id=chapter_id,
        title=chapter_title,
        source_path=str(source_path),
        char_count=len(text),
    )
    store.write_json(paths.source / f"{chapter_id}.manifest.json", manifest)
    return manifest

