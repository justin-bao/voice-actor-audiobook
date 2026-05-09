from __future__ import annotations

import re

from audiobook_narrator.models import Delivery, Emotion, Passage, StoryMemory
from audiobook_narrator.providers import LLMProvider
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.textsplit import extract_dialogue_text, is_dialogue, split_passages


EMOTION_MARKERS: list[tuple[Emotion, list[str]]] = [
    (Emotion.angry, ["怒", "愤", "吼", "骂", "恨"]),
    (Emotion.fearful, ["怕", "恐", "惊", "颤", "冷汗"]),
    (Emotion.grief, ["泪", "哭", "悲", "痛", "哀"]),
    (Emotion.tender, ["轻声", "温柔", "微笑", "亲切"]),
    (Emotion.urgent, ["快", "立刻", "马上", "喊", "冲"]),
    (Emotion.wonder, ["奇", "震撼", "仰望", "光芒"]),
    (Emotion.tense, ["沉默", "紧张", "盯", "黑暗"]),
]


def annotate_project(store: ProjectStore, project_id: str, provider: LLMProvider) -> dict[str, list[Passage]]:
    paths = store.paths(project_id)
    memory = store.load_memory(project_id)
    annotated: dict[str, list[Passage]] = {}
    for source_path in sorted(paths.source.glob("*.txt")):
        chapter_id = source_path.stem
        text = source_path.read_text(encoding="utf-8")
        passages = annotate_chapter(chapter_id, text, memory, provider)
        store.write_jsonl(paths.annotations / f"{chapter_id}.jsonl", passages)
        annotated[chapter_id] = passages
    return annotated


def annotate_chapter(
    chapter_id: str, text: str, memory: StoryMemory, provider: LLMProvider
) -> list[Passage]:
    chunks = split_passages(text)
    if provider.__class__.__name__ != "HeuristicLLMProvider" and len(text) < 14000:
        llm_rows = try_llm_annotation(chapter_id, chunks, memory, provider)
        if llm_rows:
            return llm_rows
    return [heuristic_passage(chapter_id, index, chunk, memory) for index, chunk in enumerate(chunks)]


def try_llm_annotation(
    chapter_id: str, chunks: list[str], memory: StoryMemory, provider: LLMProvider
) -> list[Passage]:
    payload = provider.complete_json(
        "You are an audiobook director. Return strict JSON with a passages array. "
        "Each passage needs text, speaker, emotion, delivery, pace, intensity, pause_after_ms, rationale.",
        "Story memory:\n"
        + memory.model_dump_json(exclude_none=True)
        + "\n\nAnnotate these chunks:\n"
        + "\n".join(f"{i}: {chunk}" for i, chunk in enumerate(chunks)),
    )
    rows = payload.get("passages", [])
    passages: list[Passage] = []
    for index, row in enumerate(rows):
        try:
            passages.append(
                Passage(
                    passage_id=f"{chapter_id}-{index:04d}",
                    chapter_id=chapter_id,
                    index=index,
                    text=row.get("text") or chunks[index],
                    speaker=row.get("speaker", "Narrator"),
                    emotion=row.get("emotion", "neutral"),
                    delivery=row.get("delivery", "matter-of-fact"),
                    pace=row.get("pace", "medium"),
                    intensity=row.get("intensity", 3),
                    pause_after_ms=row.get("pause_after_ms", 350),
                    rationale=row.get("rationale", ""),
                )
            )
        except Exception:
            return []
    return passages if len(passages) == len(chunks) else []


def heuristic_passage(chapter_id: str, index: int, text: str, memory: StoryMemory) -> Passage:
    speaker = infer_speaker(text, memory)
    emotion = infer_emotion(text)
    delivery = infer_delivery(text, emotion)
    pace = "slow" if emotion in {Emotion.grief, Emotion.solemn, Emotion.tender} else "medium"
    if emotion in {Emotion.urgent, Emotion.angry, Emotion.fearful}:
        pace = "quick"
    return Passage(
        passage_id=f"{chapter_id}-{index:04d}",
        chapter_id=chapter_id,
        index=index,
        text=text,
        speaker=speaker,
        emotion=emotion,
        delivery=delivery,
        pace=pace,
        intensity=4 if emotion in {Emotion.angry, Emotion.fearful, Emotion.urgent} else 3,
        pause_after_ms=650 if text.endswith(("。", "！", "？")) else 300,
        rationale="Heuristic annotation based on punctuation, dialogue markers, and nearby emotion words.",
    )


def infer_speaker(text: str, memory: StoryMemory) -> str:
    if not is_dialogue(text):
        return "Narrator"
    for name in memory.characters:
        if re.search(rf"{re.escape(name)}(轻声|低声|大声|冷冷|忽然)?(说|问|道|喊|叫|回答)", text):
            return name
        if re.search(rf"[”」』]{re.escape(name)}(说|问|道|喊|叫|回答)", text):
            return name
    dialogue = extract_dialogue_text(text) or text
    for name in memory.characters:
        if name in dialogue:
            return name
    return "Unknown Speaker"


def infer_emotion(text: str) -> Emotion:
    for emotion, markers in EMOTION_MARKERS:
        if any(marker in text for marker in markers):
            return emotion
    if "！" in text or "!" in text:
        return Emotion.urgent
    if "？" in text or "?" in text:
        return Emotion.tense
    return Emotion.neutral


def infer_delivery(text: str, emotion: Emotion) -> Delivery:
    if is_dialogue(text):
        if emotion in {Emotion.angry, Emotion.urgent, Emotion.fearful}:
            return Delivery.dramatic
        if emotion in {Emotion.tender, Emotion.grief}:
            return Delivery.intimate
        return Delivery.conversational
    if emotion in {Emotion.tense, Emotion.fearful}:
        return Delivery.suspenseful
    if emotion in {Emotion.wonder, Emotion.grief}:
        return Delivery.lyrical
    return Delivery.matter_of_fact
