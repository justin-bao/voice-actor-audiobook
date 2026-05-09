from __future__ import annotations

import base64
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from audiobook_narrator.ingest import normalize_text


def import_document_text(filename: str, data_url_or_base64: str) -> str:
    raw = decode_upload(data_url_or_base64)
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return normalize_text(raw.decode("utf-8-sig"))
    if suffix == ".docx":
        return normalize_text(extract_docx_text(raw))
    if suffix == ".pdf":
        return normalize_text(extract_pdf_text(raw))
    if suffix == ".epub":
        return normalize_text(extract_epub_text(raw))
    raise ValueError("Unsupported file type. Use .txt, .md, .docx, .pdf, or .epub.")


def decode_upload(data_url_or_base64: str) -> bytes:
    payload = data_url_or_base64
    if "," in payload and payload.startswith("data:"):
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload)


def extract_docx_text(raw: bytes) -> str:
    with zipfile.ZipFile(BytesIO(raw)) as docx:
        xml = docx.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        if "".join(texts).strip():
            paragraphs.append("".join(texts))
    return "\n\n".join(paragraphs)


def extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF import requires: python3 -m pip install -e '.[pdf]'") from exc

    reader = PdfReader(BytesIO(raw))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip())


def extract_epub_text(raw: bytes) -> str:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ImportError as exc:
        raise RuntimeError("EPUB import requires: python3 -m pip install -e '.[epub]'") from exc

    tmp_path = Path("web_imports") / "upload.epub"
    tmp_path.parent.mkdir(exist_ok=True)
    tmp_path.write_bytes(raw)
    try:
        book = epub.read_epub(str(tmp_path))
        chunks = []
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_body_content(), "html.parser")
            text = soup.get_text("\n")
            if text.strip():
                chunks.append(text)
        return "\n\n".join(chunks)
    finally:
        tmp_path.unlink(missing_ok=True)


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    return re.sub(r"[_-]+", " ", stem) or "Imported Chapter"
