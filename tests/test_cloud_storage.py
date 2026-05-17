from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audiobook_narrator.cloud_storage import (
    artifact_object_path,
    chapter_rows_from_local,
    iter_artifact_files,
    list_storage_paths,
)
from audiobook_narrator.storage import ProjectStore


class FakeBucket:
    def __init__(self) -> None:
        self.entries = {
            "owner/book": [{"name": "project.json", "id": "1"}, {"name": "source", "id": None}],
            "owner/book/source": [{"name": "ch01.txt", "id": "2"}],
        }

    def list(self, prefix: str):
        return self.entries.get(prefix, [])


class CloudStorageHelpersTest(unittest.TestCase):
    def test_artifact_object_path_uses_owner_and_project_prefix(self) -> None:
        path = artifact_object_path("owner", "book", Path("audio/ch01.mp3"))
        self.assertEqual(path, "owner/book/audio/ch01.mp3")

    def test_iter_artifact_files_skips_transient_upload_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source").mkdir()
            (root / "source" / "ch01.txt").write_text("text", encoding="utf-8")
            (root / "upload.tmp.txt").write_text("tmp", encoding="utf-8")

            files = [path.relative_to(root).as_posix() for path in iter_artifact_files(root)]

            self.assertEqual(files, ["source/ch01.txt"])

    def test_chapter_rows_are_derived_from_local_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(Path(tmp) / "projects")
            store.create_project("book", "Book")
            manifest_path = store.paths("book").source / "ch01.manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "chapter_id": "ch01",
                        "title": "One",
                        "order": 2,
                        "char_count": 12,
                        "analyzed": True,
                        "annotated": False,
                        "pipeline_state": "analyzed",
                        "pipeline_message": "done",
                    }
                ),
                encoding="utf-8",
            )

            rows = chapter_rows_from_local(store, "book", "owner")

            self.assertEqual(
                rows,
                [
                    {
                        "owner_id": "owner",
                        "project_id": "book",
                        "chapter_id": "ch01",
                        "title": "One",
                        "order_index": 2,
                        "char_count": 12,
                        "analyzed": True,
                        "annotated": False,
                        "pipeline_state": "analyzed",
                        "pipeline_message": "done",
                    }
                ],
            )

    def test_list_storage_paths_walks_nested_prefixes(self) -> None:
        self.assertEqual(
            list_storage_paths(FakeBucket(), "owner/book"),
            ["owner/book/project.json", "owner/book/source/ch01.txt"],
        )
