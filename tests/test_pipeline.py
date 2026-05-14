from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiobook_narrator.audio_tags import normalize_audio_tags
from audiobook_narrator.analyze import (
    GENERIC_PLOT_SUMMARY,
    UNKNOWN_PERSONALITY,
    chapter_memory_from_analysis,
    source_chapter_paths,
    update_story_memory,
)
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.annotate import try_llm_annotation
from audiobook_narrator.cast import build_cast, cast_from_memory
from audiobook_narrator.cli import extension_for_backend
from audiobook_narrator.evals import evaluate_analysis, evaluate_annotations, evaluate_cast
from audiobook_narrator.models import Cast, CastAssignment, CharacterMemory, Passage, StoryMemory, Voice
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.providers import (
    ElevenLabsTTSProvider,
    HeuristicLLMProvider,
    ScriptOnlyTTSProvider,
    provider_summary,
    tls_context,
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


class PartialAnnotationProvider:
    provider_name = "partial-annotation"

    def complete_json(self, system: str, user: str) -> dict:
        return {
            "passages": [
                {
                    "chunk_index": 0,
                    "speaker": "Narrator",
                    "emotion": "anxious",
                    "delivery": "suspense",
                    "audio_tags": ["[suspense]", "[not a real tag]", "whispering"],
                    "pace": "fast",
                    "intensity": 6,
                    "pause_after_ms": "500",
                    "rationale": "Opening narration should feel tense.",
                }
            ]
        }


class IdAnnotationProvider:
    provider_name = "id-annotation"

    def complete_json(self, system: str, user: str) -> dict:
        return {
            "passages": [
                {
                    "passage_id": "ch01-0000",
                    "speaker_name": "Narrator",
                    "audio_tags": ["[tense]"],
                    "pace": "slow",
                    "intensity": 2,
                    "pause_after_ms": 700,
                    "performance_note": "Hold the opening tension.",
                },
                {
                    "chunk_id": "chunk_0001",
                    "character_name": "汪淼",
                    "audio_tags": ["[whispers]"],
                    "pace": "medium",
                    "intensity": 3,
                    "pause_after_ms": 450,
                },
            ]
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
            self.assertEqual(set(memory.characters), {"叶文洁", "汪淼"})
            self.assertIn("汪淼", memory.characters)
            chapter_memory = store.load_chapter_memory("book", "ch01")
            self.assertIsNotNone(chapter_memory)
            self.assertEqual(
                chapter_memory.plot_summary,
                "叶文洁和汪淼在压抑的谈话中面对无法轻易停止的危机。",
            )
            self.assertEqual(chapter_memory.current_state, memory.current_state)
            self.assertIn("秘密与追问", chapter_memory.themes)
            self.assertIn("叶文洁", chapter_memory.character_changes)
            self.assertIn(
                "克制",
                chapter_memory.character_changes["叶文洁"].personality_at_this_point,
            )
            self.assertEqual(set(chapter_memory.character_changes), {"叶文洁", "汪淼"})

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
            self.assertEqual(store.load_chapter_memory("book", "ch01").plot_summary, "ch01 summary")
            self.assertEqual(store.load_chapter_memory("book", "ch02").plot_summary, "ch02 summary")

    def test_chapter_memory_tracks_character_changes_separately(self) -> None:
        analysis = {
            "summary": "汪淼在追问中意识到叶文洁掌握更深的秘密。",
            "current_state": "汪淼的怀疑加深，叶文洁保持克制。",
            "themes": ["真相逼近"],
            "character_changes": [
                {
                    "name": "汪淼",
                    "role_in_chapter": "追问叶文洁。",
                    "personality_at_this_point": "焦灼、执着。",
                    "changes": "从试探转为确信存在隐情。",
                    "evidence": ["你真的相信这一切会结束吗？"],
                }
            ],
        }

        chapter_memory = chapter_memory_from_analysis("ch01", "第一章", SAMPLE, analysis)

        self.assertEqual(chapter_memory.plot_summary, analysis["summary"])
        self.assertEqual(chapter_memory.current_state, analysis["current_state"])
        self.assertIn("真相逼近", chapter_memory.themes)
        self.assertEqual(
            chapter_memory.character_changes["汪淼"].changes,
            "从试探转为确信存在隐情。",
        )

    def test_analyze_ignores_embedded_annotation_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "ch01.txt").write_text("raw", encoding="utf-8")
            (source / "ch01.annotated.txt").write_text("annotated", encoding="utf-8")

            self.assertEqual([path.name for path in source_chapter_paths(source)], ["ch01.txt"])

    def test_analyze_uses_manifest_order_for_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "ch01.txt").write_text("one", encoding="utf-8")
            (source / "ch01.manifest.json").write_text('{"chapter_id":"ch01","order":1}', encoding="utf-8")
            (source / "ch02.txt").write_text("two", encoding="utf-8")
            (source / "ch02.manifest.json").write_text('{"chapter_id":"ch02","order":0}', encoding="utf-8")

            self.assertEqual([path.name for path in source_chapter_paths(source)], ["ch02.txt", "ch01.txt"])

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

    def test_cast_uses_elevenlabs_voices_and_preserves_existing_choices(self) -> None:
        memory = StoryMemory(title="测试书")
        memory.characters["汪淼"] = CharacterMemory(
            name="汪淼",
            age="adult",
            gender="male",
            personality="执着、紧张、理性。",
            voice_notes="紧张但清晰的成年男性声音。",
        )
        memory.characters["叶文洁"] = CharacterMemory(
            name="叶文洁",
            age="adult",
            gender="female",
            personality="克制、沉重。",
            voice_notes="低声、疲惫。",
        )
        existing = Cast(
            assignments={
                "汪淼": CastAssignment(character="汪淼", voice_id="saved_wang", reason="Manual pick")
            },
            voices={
                "saved_wang": Voice(
                    voice_id="saved_wang",
                    provider_voice="voice-existing",
                    language="zh",
                )
            },
        )
        voices = [
            {"voice_id": "voice-male", "name": "Male Mandarin", "labels": {"gender": "male", "age": "adult"}},
            {"voice_id": "voice-female", "name": "Female Mandarin", "labels": {"gender": "female", "age": "adult"}},
        ]

        cast = cast_from_memory(memory, existing=existing, elevenlabs_voices=voices)

        self.assertEqual(cast.assignments["汪淼"].voice_id, "saved_wang")
        self.assertEqual(cast.voices["saved_wang"].provider_voice, "voice-existing")
        self.assertEqual(cast.voices[cast.assignments["叶文洁"].voice_id].provider_voice, "voice-female")

    def test_annotation_uses_partial_llm_rows_without_total_fallback(self) -> None:
        story_memory = StoryMemory(title="测试书")
        chunks = ["旁白很紧张。", "“真的吗？”汪淼问。"]

        passages = try_llm_annotation("ch01", chunks, story_memory, PartialAnnotationProvider())

        self.assertEqual(len(passages), 2)
        self.assertEqual(passages[0].emotion.value, "tense")
        self.assertEqual(passages[0].delivery.value, "suspenseful")
        self.assertEqual(passages[0].pace, "quick")
        self.assertEqual(passages[0].intensity, 5)
        self.assertEqual(passages[0].audio_tags, ["[tense]", "[whispers]"])
        self.assertEqual(passages[0].rationale, "Opening narration should feel tense.")
        self.assertIn("Heuristic annotation", passages[1].rationale)

    def test_annotation_accepts_llm_passage_ids_and_speaker_aliases(self) -> None:
        story_memory = StoryMemory(title="测试书")
        chunks = ["旁白很紧张。", "“真的吗？”汪淼问。"]

        passages = try_llm_annotation("ch01", chunks, story_memory, IdAnnotationProvider())

        self.assertEqual(len(passages), 2)
        self.assertEqual(passages[0].audio_tags, ["[tense]"])
        self.assertEqual(passages[0].rationale, "Hold the opening tension.")
        self.assertEqual(passages[1].speaker, "汪淼")
        self.assertNotIn("Heuristic annotation", passages[1].rationale)

    def test_elevenlabs_audio_tags_are_normalized_to_allowlist(self) -> None:
        self.assertEqual(
            normalize_audio_tags(["[suspense]", "[not a tag]", "whispering", "urgent"]),
            ["[tense]", "[whispers]", "[shouts]"],
        )

    def test_elevenlabs_backend_chunks_dialogue_inputs(self) -> None:
        passages = [
            Passage(passage_id="ch01-0000", chapter_id="ch01", index=0, text="旁白。"),
            Passage(
                passage_id="ch01-0001",
                chapter_id="ch01",
                index=1,
                text="“你好。”汪淼说。",
                speaker="汪淼",
                audio_tags=["[whispers]", "[fake]"],
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
        self.assertEqual(chunks[0]["inputs"][1]["text"], "[whispers] “你好。”汪淼说。")
        self.assertEqual(chunks[0]["manifest"][1]["audio_tags"], ["[whispers]"])

    def test_provider_summary_names_heuristic_provider(self) -> None:
        self.assertEqual(provider_summary(HeuristicLLMProvider()), {"provider": "heuristic", "model": None})

    def test_tls_context_uses_a_ca_store(self) -> None:
        context = tls_context()
        self.assertGreater(len(context.get_ca_certs()), 0)


if __name__ == "__main__":
    unittest.main()
