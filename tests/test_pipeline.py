from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_narrator.analyze import (
    GENERIC_PLOT_SUMMARY,
    UNKNOWN_PERSONALITY,
    source_chapter_paths,
    update_story_memory,
)
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
from audiobook_narrator.cli import extension_for_backend
from audiobook_narrator.evals import evaluate_analysis, evaluate_annotations, evaluate_cast
from audiobook_narrator.models import Passage
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.providers import (
    ElevenLabsTTSProvider,
    HeuristicLLMProvider,
    ScriptOnlyTTSProvider,
    provider_summary,
)
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.synthesize import synthesize_chapter
from audiobook_narrator.textsplit import split_passages


SAMPLE = """叶文洁望着远处的山，沉默了很久。

“你真的相信这一切会结束吗？”汪淼问。

叶文洁轻声说：“有些事情，开始以后就不会按人的愿望停止。”
"""


class StaticLLMProvider:
    provider_name = "static"

    def complete_json(self, system: str, user: str) -> dict:
        return {
            "summary": "叶文洁和汪淼在压抑的谈话中面对无法轻易停止的危机。",
            "current_state": "叶文洁暗示事件已经越过可控边界，汪淼仍在追问真相。",
            "themes": ["失控的开端", "秘密与追问"],
            "pronunciation_notes": {"汪淼": "Wang Miao"},
            "characters": [
                {
                    "name": "叶文洁",
                    "personality": "克制、沉重，像是在压住已经知道的真相。",
                    "role_in_plot": "向汪淼暗示危机无法按人的意愿停止。",
                    "relationships": {"汪淼": "被追问者与倾听者"},
                    "voice_notes": "低声、疲惫、带压抑的重量。",
                },
                {
                    "name": "汪淼",
                    "personality": "困惑但执着，急于确认事情是否还能结束。",
                    "role_in_plot": "追问事件走向，推动叶文洁说出判断。",
                    "voice_notes": "紧张、清晰、带疑问感。",
                },
            ],
        }


class RecordingLLMProvider:
    provider_name = "recording"

    def __init__(self) -> None:
        self.users: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.users.append(user)
        chapter_id = "ch02" if "Chapter id: ch02" in user else "ch01"
        return {
            "summary": f"{chapter_id} summary",
            "current_state": f"{chapter_id} state",
            "themes": [chapter_id],
            "characters": [
                {
                    "name": "叶文洁",
                    "personality": f"{chapter_id} specific",
                    "role_in_plot": f"{chapter_id} role",
                    "voice_notes": f"{chapter_id} voice",
                }
            ],
        }


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

    def test_analyze_maps_llm_output_into_story_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "chapter.txt"
            source.write_text(SAMPLE, encoding="utf-8")
            store = ProjectStore(base / "projects")
            store.create_project("book", "测试书")
            ingest_chapter(store, "book", source, "第一章", "ch01")

            memory = update_story_memory(store, "book", StaticLLMProvider())

            self.assertNotEqual(memory.plot_summary, GENERIC_PLOT_SUMMARY)
            self.assertEqual(
                memory.current_state,
                "叶文洁暗示事件已经越过可控边界，汪淼仍在追问真相。",
            )
            self.assertIn("秘密与追问", memory.themes)
            self.assertEqual(memory.pronunciation_notes["汪淼"], "Wang Miao")
            self.assertNotEqual(memory.characters["叶文洁"].personality, UNKNOWN_PERSONALITY)
            self.assertIn("汪淼", memory.characters)

    def test_analyze_feeds_existing_memory_into_next_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = ProjectStore(base / "projects")
            store.create_project("book", "测试书")
            source_one = base / "001.txt"
            source_two = base / "002.txt"
            source_one.write_text("第一章。叶文洁说。", encoding="utf-8")
            source_two.write_text("第二章。汪淼问。", encoding="utf-8")
            ingest_chapter(store, "book", source_one, "第一章", "ch01")
            ingest_chapter(store, "book", source_two, "第二章", "ch02")
            provider = RecordingLLMProvider()

            memory = update_story_memory(store, "book", provider)

            self.assertEqual(len(provider.users), 2)
            self.assertIn("ch01 summary", provider.users[1])
            self.assertEqual(memory.current_state, "ch02 state")

    def test_analyze_ignores_embedded_annotation_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "ch01.txt").write_text("raw", encoding="utf-8")
            (source / "ch01.annotated.txt").write_text("annotated", encoding="utf-8")

            self.assertEqual([path.name for path in source_chapter_paths(source)], ["ch01.txt"])

    def test_eval_scores_cover_analysis_annotations_and_casting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "chapter.txt"
            source.write_text(SAMPLE, encoding="utf-8")
            store = ProjectStore(base / "projects")
            store.create_project("book", "测试书")
            ingest_chapter(store, "book", source, "第一章", "ch01")
            memory = update_story_memory(store, "book", StaticLLMProvider())
            annotated = annotate_project(store, "book", HeuristicLLMProvider())
            cast = build_cast(store, "book")

            analysis_names = {score["name"] for score in evaluate_analysis(memory)}
            annotation_names = {score["name"] for score in evaluate_annotations(annotated["ch01"])}
            cast_names = {score["name"] for score in evaluate_cast(cast, memory)}

            self.assertIn("analyze_character_specificity", analysis_names)
            self.assertIn("annotate_direction_richness", annotation_names)
            self.assertIn("cast_assignment_coverage", cast_names)

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

    def test_provider_summary_names_heuristic_provider(self) -> None:
        self.assertEqual(provider_summary(HeuristicLLMProvider()), {"provider": "heuristic", "model": None})


if __name__ == "__main__":
    unittest.main()
