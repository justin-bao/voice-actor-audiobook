from __future__ import annotations

import logging
import re

from audiobook_narrator.analyze import source_chapter_paths
from audiobook_narrator.audio_tags import allowed_audio_tags_prompt, audio_tags_for_passage, normalize_audio_tags
from audiobook_narrator.models import ChapterMemory, Delivery, Emotion, Passage, StoryMemory
from audiobook_narrator.providers import LLMProvider
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.textsplit import extract_dialogue_text, is_dialogue, split_passages


logger = logging.getLogger(__name__)

ANNOTATE_SYSTEM_PROMPT = """You are an audiobook director for Mandarin Chinese fiction.
Use ElevenLabs v3 audio tags as the primary performance direction. Only use tags from this allowlist:
__ALLOWED_AUDIO_TAGS__

You will receive two memory inputs:
- Book memory: cumulative facts — character biographies, stable personalities, established relationships, overall plot context.
- Chapter memory: episodic state — how characters feel and behave specifically in this chapter, emotional and mood shifts, narrative atmosphere at this point in the story.

Prioritize chapter memory for moment-to-moment performance direction. Use book memory for stable character voice and identity.

Return strict JSON with a "passages" array. Each passage must preserve the numbered chunk index and contain:
{
  "chunk_index": 0,
  "speaker": "Narrator or character name",
  "audio_tags": ["[tense]", "[whispers]"],
  "pace": "slow|medium|quick",
  "intensity": 1-5,
  "pause_after_ms": integer,
  "rationale": "specific performance note tied to this passage"
}
Do not rewrite the text. Do not invent tags outside the allowlist. Use the exact chunk_index values supplied."""

ANNOTATE_USER_TEMPLATE = """Book memory (cumulative facts, character bios, story context up to this chapter):
{memory_json}

Chapter memory (this chapter's narrative atmosphere, emotional shifts, character states):
{chapter_memory_json}

Annotate these chunks:
{chunks}"""

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
    for source_path in source_chapter_paths(paths.source):
        chapter_id = source_path.stem
        text = source_path.read_text(encoding="utf-8")
        chapter_memory = store.load_chapter_memory(project_id, chapter_id)
        passages = annotate_chapter(chapter_id, text, memory, provider, chapter_memory=chapter_memory)
        store.write_jsonl(paths.annotations / f"{chapter_id}.jsonl", passages)
        annotated[chapter_id] = passages
    return annotated


def annotate_chapter(
    chapter_id: str,
    text: str,
    memory: StoryMemory,
    provider: LLMProvider,
    chapter_memory: ChapterMemory | None = None,
) -> list[Passage]:
    chunks = split_passages(text)
    if provider.__class__.__name__ != "HeuristicLLMProvider":
        llm_rows = try_llm_annotation(chapter_id, chunks, memory, provider, chapter_memory=chapter_memory)
        if llm_rows:
            return llm_rows
    return [heuristic_passage(chapter_id, index, chunk, memory) for index, chunk in enumerate(chunks)]


def try_llm_annotation(
    chapter_id: str,
    chunks: list[str],
    memory: StoryMemory,
    provider: LLMProvider,
    chapter_memory: ChapterMemory | None = None,
) -> list[Passage]:
    if not chunks:
        return []
    chapter_memory_json = chapter_memory.model_dump_json(exclude_none=True) if chapter_memory else "{}"
    payload = provider.complete_json(
        ANNOTATE_SYSTEM_PROMPT.replace("__ALLOWED_AUDIO_TAGS__", allowed_audio_tags_prompt()),
        ANNOTATE_USER_TEMPLATE.format(
            memory_json=memory.model_dump_json(exclude_none=True),
            chapter_memory_json=chapter_memory_json,
            chunks="\n".join(f"{i}: {chunk}" for i, chunk in enumerate(chunks)),
        ),
    )
    rows = payload.get("passages", [])
    if not isinstance(rows, list):
        logger.warning("LLM annotation rejected chapter=%s reason=passages_not_list", chapter_id)
        return []
    by_index: dict[int, dict] = {}
    for fallback_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        index = annotation_row_index(row, fallback_index, len(chunks))
        if index is None:
            logger.warning(
                "LLM annotation row rejected chapter=%s reason=bad_index fallback_index=%s keys=%s",
                chapter_id,
                fallback_index,
                sorted(row.keys()),
            )
            continue
        by_index[index] = row
    passages: list[Passage] = []
    llm_count = 0
    for index, chunk in enumerate(chunks):
        row = by_index.get(index)
        if row is None:
            passages.append(heuristic_passage(chapter_id, index, chunk, memory))
            continue
        try:
            audio_tags = normalize_audio_tags(row.get("audio_tags") or row.get("tags"))
            passages.append(
                passage_from_llm_row(
                    chapter_id=chapter_id,
                    index=index,
                    text=chunk,
                    row=row,
                    audio_tags=audio_tags,
                )
            )
            llm_count += 1
        except Exception as exc:
            logger.warning(
                "LLM annotation row rejected chapter=%s index=%s error=%s", chapter_id, index, exc
            )
            passage = heuristic_passage(chapter_id, index, chunk, memory)
            passage.audio_tags = audio_tags_for_passage(passage)
            passages.append(passage)
    logger.info(
        "Annotation source chapter=%s llm_rows=%s chunks=%s", chapter_id, llm_count, len(chunks)
    )
    return passages


def passage_from_llm_row(
    chapter_id: str, index: int, text: str, row: dict, audio_tags: list[str]
) -> Passage:
    emotion = normalize_emotion(row.get("emotion"))
    delivery = normalize_delivery(row.get("delivery"))
    if not row.get("emotion"):
        emotion = emotion_from_audio_tags(audio_tags)
    if not row.get("delivery"):
        delivery = delivery_from_audio_tags(audio_tags)
    return Passage(
        passage_id=f"{chapter_id}-{index:04d}",
        chapter_id=chapter_id,
        index=index,
        text=text,
        speaker=normalize_speaker(row),
        emotion=emotion,
        delivery=delivery,
        pace=normalize_pace(row.get("pace")),
        intensity=normalize_intensity(row.get("intensity")),
        pause_after_ms=normalize_pause(row.get("pause_after_ms")),
        audio_tags=audio_tags,
        rationale=str(
            row.get("rationale")
            or row.get("performance_note")
            or row.get("note")
            or "AI narration direction based on story context."
        ),
    )


def annotation_row_index(row: dict, fallback_index: int, chunk_count: int) -> int | None:
    raw_index = first_present(row, "chunk_index", "index", "chunk", "chunk_id", "passage_id", "id")
    if raw_index is None:
        return fallback_index if fallback_index < chunk_count else None
    index = parse_annotation_index(raw_index)
    if index is None:
        return fallback_index if fallback_index < chunk_count else None
    if 0 <= index < chunk_count:
        return index
    if 1 <= index <= chunk_count:
        return index - 1
    return fallback_index if fallback_index < chunk_count else None


def parse_annotation_index(value: object) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    matches = re.findall(r"\d+", text)
    if not matches:
        return None
    return int(matches[-1])


def first_present(row: dict, *keys: str) -> object | None:
    for key in keys:
        if key in row and row[key] not in {None, ""}:
            return row[key]
    return None


def normalize_speaker(row: dict) -> str:
    speaker = str(
        first_present(row, "speaker", "speaker_name", "character", "character_name", "voice")
        or "Narrator"
    ).strip()
    return speaker or "Narrator"


def emotion_from_audio_tags(audio_tags: list[str]) -> Emotion:
    tags = {tag.strip("[]").lower() for tag in audio_tags}
    if tags & {"angry", "shouts"}:
        return Emotion.angry
    if tags & {"fearful", "stuttering"}:
        return Emotion.fearful
    if tags & {"crying", "sad"}:
        return Emotion.grief
    if tags & {"softly", "whispers"}:
        return Emotion.tender
    if tags & {"curious"}:
        return Emotion.wonder
    if tags & {"laughs", "mischievously"}:
        return Emotion.comic
    if tags & {"serious"}:
        return Emotion.solemn
    if tags & {"tense"}:
        return Emotion.tense
    if tags & {"cheerfully", "excited", "happily"}:
        return Emotion.urgent
    return Emotion.neutral


def delivery_from_audio_tags(audio_tags: list[str]) -> Delivery:
    tags = {tag.strip("[]").lower() for tag in audio_tags}
    if tags & {"shouts", "dramatically"}:
        return Delivery.dramatic
    if tags & {"softly", "whispers"}:
        return Delivery.intimate
    if tags & {"tense", "fearful", "stuttering"}:
        return Delivery.suspenseful
    if tags & {"serious"}:
        return Delivery.clipped
    if tags & {"curious", "sad", "crying"}:
        return Delivery.reflective
    return Delivery.conversational if audio_tags else Delivery.matter_of_fact


def normalize_emotion(value: object) -> Emotion:
    text = str(value or "neutral").strip().lower().replace("_", "-")
    aliases = {
        "sad": "grief",
        "sadness": "grief",
        "scared": "fearful",
        "fear": "fearful",
        "anxious": "tense",
        "suspense": "tense",
        "calm": "neutral",
    }
    text = aliases.get(text, text)
    return Emotion(text) if text in {item.value for item in Emotion} else Emotion.neutral


def normalize_delivery(value: object) -> Delivery:
    text = str(value or "matter-of-fact").strip().lower().replace("_", "-")
    aliases = {
        "matter of fact": "matter-of-fact",
        "plain": "matter-of-fact",
        "warm": "intimate",
        "suspense": "suspenseful",
    }
    text = aliases.get(text, text)
    return Delivery(text) if text in {item.value for item in Delivery} else Delivery.matter_of_fact


def normalize_pace(value: object) -> str:
    text = str(value or "medium").strip().lower()
    if text in {"slow", "medium", "quick"}:
        return text
    if text in {"fast", "rapid"}:
        return "quick"
    return "medium"


def normalize_intensity(value: object) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def normalize_pause(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 350


def heuristic_passage(chapter_id: str, index: int, text: str, memory: StoryMemory) -> Passage:
    speaker = infer_speaker(text, memory)
    emotion = infer_emotion(text)
    delivery = infer_delivery(text, emotion)
    pace = "slow" if emotion in {Emotion.grief, Emotion.solemn, Emotion.tender} else "medium"
    if emotion in {Emotion.urgent, Emotion.angry, Emotion.fearful}:
        pace = "quick"
    passage = Passage(
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
    passage.audio_tags = audio_tags_for_passage(passage)
    return passage


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
