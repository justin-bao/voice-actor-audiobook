from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_narrator.analyze import update_story_memory
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
from audiobook_narrator.cli import extension_for_backend
from audiobook_narrator.models import Passage
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.providers import ElevenLabsTTSProvider, HeuristicLLMProvider, ScriptOnlyTTSProvider
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.synthesize import synthesize_chapter
from audiobook_narrator.textsplit import split_passages


SAMPLE = """叶文洁望着远处的山，沉默了很久。

“你真的相信这一切会结束吗？”汪淼问。

叶文洁轻声说：“有些事情，开始以后就不会按人的愿望停止。”
"""


class PipelineTest(unittest.TestCase):
    def test_split_passages_preserves_dialogue_turns(self) -> None:
        passages = split_passages(SAMPLE)
        self.assertEqual(passages[0], "叶文洁望着远处的山，沉默了很久。")
        self.assertEqual(passages[1], "“你真的相信这一切会结束吗？”汪淼问。")
        self.assertIn("叶文洁轻声说", passages[2])

    def test_end_to_end_script_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "chapter.txt"
            source.write_text(SAMPLE, encoding="utf-8")
            store = ProjectStore(base / "projects")
            store.create_project("book", "测试书")
            ingest_chapter(store, "book", source, "第一章", "ch01")
            update_story_memory(store, "book", HeuristicLLMProvider())
            annotated = annotate_project(store, "book", HeuristicLLMProvider())
            cast = build_cast(store, "book")
            ssml_path, script_path = synthesize_chapter(
                store, "book", "ch01", ScriptOnlyTTSProvider(), ".txt"
            )

            self.assertGreaterEqual(len(annotated["ch01"]), 3)
            self.assertIn("汪淼", cast.assignments)
            self.assertTrue(Path(ssml_path).exists())
            self.assertIn("[汪淼", Path(script_path).read_text(encoding="utf-8"))

    def test_elevenlabs_backend_chunks_dialogue_inputs(self) -> None:
        passages = [
            Passage(passage_id="ch01-0000", chapter_id="ch01", index=0, text="旁白。"),
            Passage(
                passage_id="ch01-0001",
                chapter_id="ch01",
                index=1,
                text="“你好。”汪淼说。",
                speaker="汪淼",
            ),
        ]
        with patch.dict(
            "os.environ",
            {
                "ELEVENLABS_API_KEY": "test-key",
                "ELEVENLABS_DEFAULT_VOICE_ID": "voice-default",
                "ELEVENLABS_VOICE_MAP_JSON": '{"汪淼":"voice-wang"}',
            },
            clear=False,
        ):
            provider = ElevenLabsTTSProvider()
            chunks = provider._dialogue_chunks(passages, {"Narrator": "Ting-Ting"})

        self.assertEqual(extension_for_backend("elevenlabs"), ".mp3")
        self.assertEqual(chunks[0]["inputs"][0]["voice_id"], "voice-default")
        self.assertEqual(chunks[0]["inputs"][1]["voice_id"], "voice-wang")


if __name__ == "__main__":
    unittest.main()
