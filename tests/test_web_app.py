from __future__ import annotations

import tempfile
import unittest
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from audiobook_narrator.models import ChapterMemory, Passage
from audiobook_narrator.web import NarratorWebApp, decoded_path_parts, make_handler, title_to_project_id


class WebAppFileActionsTest(unittest.TestCase):
    def test_create_project_can_derive_id_from_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")

            result = app.create_project({"title": "超禁忌游戏 4"})

            self.assertEqual(result["project_id"], "超禁忌游戏-4")
            self.assertTrue((Path(tmp) / "projects" / "超禁忌游戏-4" / "project.json").exists())

    def test_for_user_scopes_projects_under_user_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            user_app = app.for_user("user-1")

            user_app.create_project({"title": "Book"})

            self.assertTrue((Path(tmp) / "projects" / "user-1" / "book" / "project.json").exists())

    def test_create_project_adds_suffix_for_duplicate_slug_with_different_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")

            first = app.create_project({"title": "Test Book"})
            second = app.create_project({"title": "Test/Book"})

            self.assertEqual(first["project_id"], "test-book")
        self.assertEqual(second["project_id"], "test-book-2")

    def test_bulk_import_preserves_confirmed_order_and_analyzes(self) -> None:
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
                ["010_第十章", "002_第二章"],
            )
            self.assertIn("memory", result)

    def test_project_payload_does_not_auto_select_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})
            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "Hello"})

            payload = app.project_payload("book")

            self.assertIsNone(payload["selected_chapter_id"])
            self.assertEqual(payload["source_text"], "")

    def test_rename_project_updates_config_and_memory_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Old"})

            result = app.rename_project("old", {"title": "New"})

            self.assertEqual(result["config"]["title"], "New")
            self.assertEqual(app.store.load_config("old").title, "New")
            self.assertEqual(app.store.load_memory("old").title, "New")

    def test_reorder_chapters_persists_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})
            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "1"})
            app.save_chapter("book", {"chapter_id": "ch02", "title": "Two", "text": "2"})

            result = app.reorder_chapters("book", {"chapter_ids": ["ch02", "ch01"]})

            self.assertEqual([row["chapter_id"] for row in result["chapters"]], ["ch02", "ch01"])
            self.assertEqual(
                [row["chapter_id"] for row in app.project_payload("book")["chapters"]],
                ["ch02", "ch01"],
            )

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
            paths = store.paths("book")
            (paths.scripts / "ch01.ssml.xml").write_text("<speak />", encoding="utf-8")
            (paths.audio / "ch01.mp3").write_bytes(b"audio")
            (paths.audio / "ch01.parts.json").write_text("[]", encoding="utf-8")
            (paths.audio / "ch01_0001.mp3").write_bytes(b"chunk")
            store.save_chapter_memory(
                "book",
                ChapterMemory(chapter_id="ch01", title="Chapter", plot_summary="Chapter memory"),
            )

            result = app.reset_annotations("book", "ch01")

            self.assertTrue(result["ok"])
            self.assertFalse((paths.annotations / "ch01.jsonl").exists())
            self.assertFalse((paths.source / "ch01.annotated.txt").exists())
            self.assertFalse((paths.scripts / "ch01.ssml.xml").exists())
            self.assertFalse((paths.audio / "ch01.mp3").exists())
            self.assertFalse((paths.audio / "ch01.parts.json").exists())
            self.assertFalse((paths.audio / "ch01_0001.mp3").exists())
            self.assertTrue((paths.source / "ch01.txt").exists())
            self.assertTrue((paths.memory / "chapters" / "ch01.json").exists())

    def test_chapter_status_tracks_analysis_and_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})
            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "Hello"})

            app.update_chapter_status("book", "ch01", analyzed=True, annotated=True)
            chapter = app.load_chapter_manifests("book")[0]
            self.assertTrue(chapter["analyzed"])
            self.assertTrue(chapter["annotated"])

            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "Changed"})
            chapter = app.load_chapter_manifests("book")[0]
            self.assertFalse(chapter["analyzed"])
            self.assertFalse(chapter["annotated"])

    def test_analyze_requires_previous_chapter_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})
            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "Hello"})
            app.save_chapter("book", {"chapter_id": "ch02", "title": "Two", "text": "World"})

            with self.assertRaisesRegex(ValueError, "Analyze the previous chapter first"):
                app.run_step("book", "analyze", "ch02", {"no_openai": True})

            chapters = app.load_chapter_manifests("book")
            self.assertEqual(chapters[1]["pipeline_state"], "paused")
            self.assertIn("Waiting for prior chapter analysis", chapters[1]["pipeline_message"])

            app.run_step("book", "analyze", "ch01", {"no_openai": True})
            result = app.run_step("book", "analyze", "ch02", {"no_openai": True})

            self.assertTrue(result["ok"])

    def test_cancel_pipeline_marks_current_and_subsequent_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})
            app.save_chapter("book", {"chapter_id": "ch01", "title": "One", "text": "Hello"})
            app.save_chapter("book", {"chapter_id": "ch02", "title": "Two", "text": "World"})

            result = app.cancel_pipeline_from("book", "ch01")
            chapters = app.load_chapter_manifests("book")

            self.assertEqual(result["canceled"], ["ch01", "ch02"])
            self.assertEqual([chapter["pipeline_state"] for chapter in chapters], ["canceled", "canceled"])

    def test_delete_chapter_removes_chapter_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "ch01", "title": "Chapter", "text": "Hello"})
            app.save_annotated_text("book", "ch01", {"text": "annotated"})
            store.save_chapter_memory(
                "book",
                ChapterMemory(chapter_id="ch01", title="Chapter", plot_summary="Chapter memory"),
            )

            result = app.delete_chapter("book", "ch01")
            paths = store.paths("book")

            self.assertTrue(result["ok"])
            self.assertEqual(result["remaining_chapter_ids"], [])
            self.assertFalse((paths.source / "ch01.txt").exists())
            self.assertFalse((paths.source / "ch01.manifest.json").exists())
            self.assertFalse((paths.source / "ch01.annotated.txt").exists())
            self.assertFalse((paths.memory / "chapters" / "ch01.json").exists())

    def test_project_payload_includes_selected_chapter_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            store = app.store
            store.create_project("book", "Book")
            app.save_chapter("book", {"chapter_id": "ch01", "title": "Chapter", "text": "Hello"})
            store.save_chapter_memory(
                "book",
                ChapterMemory(chapter_id="ch01", title="Chapter", plot_summary="Chapter memory"),
            )

            payload = app.project_payload("book", "ch01")

            self.assertEqual(payload["chapter_memory"]["plot_summary"], "Chapter memory")

    def test_synthesis_progress_tracks_latest_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})

            self.assertEqual(app.synthesis_progress("book", "ch01")["phase"], "idle")

            app.update_synthesis_progress(
                "book",
                "ch01",
                {"phase": "generating", "total_chunks": 3, "completed_chunks": 1, "current_chunk": 2},
            )

            self.assertEqual(
                app.synthesis_progress("book", "ch01"),
                {"phase": "generating", "total_chunks": 3, "completed_chunks": 1, "current_chunk": 2},
            )

    def test_handler_write_response_swallows_broken_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handler_type = make_handler(NarratorWebApp(Path(tmp) / "projects"))
            handler = object.__new__(handler_type)
            handler.wfile = BytesIO()
            handler.send_response = lambda status: None
            handler.send_header = lambda key, value: None

            def broken_end_headers() -> None:
                raise BrokenPipeError()

            handler.end_headers = broken_end_headers

            self.assertIsNone(handler.write_response(200, b"ok", {"Content-Length": "2"}))

    def test_save_chapter_memory_persists_editable_chapter_understanding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = NarratorWebApp(Path(tmp) / "projects")
            app.create_project({"title": "Book"})

            result = app.save_chapter_memory(
                "book",
                "ch01",
                {
                    "title": "Chapter",
                    "plot_summary": "Chapter-only plot",
                    "current_state": "Chapter end state",
                    "themes": ["pressure"],
                    "character_changes": {
                        "汪淼": {
                            "name": "汪淼",
                            "role_in_chapter": "Asks questions.",
                            "personality_at_this_point": "Persistent.",
                            "changes": "Gets more suspicious.",
                        }
                    },
                },
            )

            self.assertTrue(result["ok"])
            loaded = app.store.load_chapter_memory("book", "ch01")
            self.assertEqual(loaded.plot_summary, "Chapter-only plot")
            self.assertEqual(loaded.character_changes["汪淼"].changes, "Gets more suspicious.")

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
