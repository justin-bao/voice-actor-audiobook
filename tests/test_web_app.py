from __future__ import annotations

import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from urllib.parse import quote

from audiobook_narrator.models import Passage
from audiobook_narrator.web import NarratorWebApp, decoded_path_parts, title_to_project_id


class WebAppFileActionsTest(unittest.TestCase):
    def test_create_project_can_derive_id_from_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")

            result = app.create_project({"title": "超禁忌游戏 4"})

            self.assertEqual(result["project_id"], "超禁忌游戏-4")
            self.assertTrue((Path(tmp) / "projects" / "超禁忌游戏-4" / "project.json").exists())

    def test_create_project_adds_suffix_for_duplicate_slug_with_different_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")

            first = app.create_project({"title": "Test Book"})
            second = app.create_project({"title": "Test/Book"})

            self.assertEqual(first["project_id"], "test-book")
        self.assertEqual(second["project_id"], "test-book-2")

    def test_bulk_import_sorts_chapters_naturally_and_analyzes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})

            result = app.bulk_import(
                "book",
                {
                    "analyze": True,
                    "no_openai": True,
                    "files": [
                        {"filename": "010_第十章.txt", "data": b64encode("第十章".encode()).decode()},
                        {"filename": "002_第二章.txt", "data": b64encode("第二章".encode()).decode()},
                    ],
                },
            )

            self.assertEqual(
                [row["chapter_id"] for row in result["manifests"]],
                ["002_第二章", "010_第十章"],
            )
            self.assertIn("memory", result)

    def test_title_to_project_id_has_safe_fallback(self) -> None:
        self.assertEqual(title_to_project_id("///"), "book")

    def test_reset_annotations_removes_annotation_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "ch01", "title": "Chapter", "text": "Hello"})
            app.save_annotations(
                "book",
                "ch01",
                {"annotations": [Passage(passage_id="p1", chapter_id="ch01", index=0, text="Hello").model_dump()]},
            )
            app.save_annotated_text("book", "ch01", {"text": "[[speaker=Narrator]]\nHello\n"})

            result = app.reset_annotations("book", "ch01")
            paths = store.paths("book")

            self.assertTrue(result["ok"])
            self.assertFalse((paths.annotations / "ch01.jsonl").exists())
            self.assertFalse((paths.source / "ch01.annotated.txt").exists())
            self.assertTrue((paths.source / "ch01.txt").exists())

    def test_delete_chapter_removes_chapter_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "ch01", "title": "Chapter", "text": "Hello"})
            app.save_annotated_text("book", "ch01", {"text": "annotated"})

            result = app.delete_chapter("book", "ch01")
            paths = store.paths("book")

            self.assertTrue(result["ok"])
            self.assertEqual(result["remaining_chapter_ids"], [])
            self.assertFalse((paths.source / "ch01.txt").exists())
            self.assertFalse((paths.source / "ch01.manifest.json").exists())
            self.assertFalse((paths.source / "ch01.annotated.txt").exists())

    def test_delete_project_removes_entire_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "ch01", "title": "Chapter", "text": "Hello"})
            paths = store.paths("book")

            result = app.delete_project("book")

            self.assertTrue(result["ok"])
            self.assertFalse(paths.root.exists())

    def test_delete_chapter_route_decodes_non_ascii_chapter_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "004_第一章", "title": "第一章", "text": "Hello"})
            parts = decoded_path_parts(f"/api/projects/book/delete-chapter/{quote('004_第一章')}")
            result = app.delete_chapter(parts[2], parts[4])

            paths = store.paths("book")
            self.assertTrue(result["ok"])
            self.assertFalse((paths.source / "004_第一章.txt").exists())
            self.assertFalse((paths.source / "004_第一章.manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
