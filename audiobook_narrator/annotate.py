from __future__ import annotations

import logging
import re

from audiobook_narrator.audio_tags import allowed_audio_tags_prompt, audio_tags_for_passage, extract_inline_tags, normalize_audio_tags
from audiobook_narrator.models import ChapterMemory, Delivery, Emotion, Passage, StoryMemory
from audiobook_narrator.providers import LLMProvider
from audiobook_narrator.storage import ProjectStore, list_source_chapter_paths
from audiobook_narrator.textsplit import extract_dialogue_text, is_dialogue, split_passages


logger = logging.getLogger(__name__)

ANNOTATE_SYSTEM_PROMPT = """You are an audiobook director for Mandarin Chinese fiction.
Use ElevenLabs v3 audio tags as the primary performance direction. Only use tags from this allowlist:
__ALLOWED_AUDIO_TAGS__

You will receive three memory inputs:
- Book memory: cumulative facts — character biographies, stable personalities, established relationships, overall plot context.
- Previous chapter state: the emotional and narrative handoff — where characters were left and the atmosphere at the end of the prior chapter.
- This chapter's episodic memory: atmosphere, emotional arcs, vocal quality per character, and key dramatic beats specific to this chapter.

Use book memory for stable character voice identity. Use previous chapter state to set the opening tone and continuity. Use this chapter's episodic memory for moment-to-moment performance direction.

Embed audio tags INLINE in the text at the exact position where the performance direction takes effect. This lets emotion and delivery change mid-passage. Examples:
  "她先是沉默，然后[whispers] 低声说：'我知道了。'"
  "[tense] 她盯着门口，[fearful] 听到脚步声越来越近。"
  "[angry] '你凭什么！'他吼道，[sad] 但眼眶已经红了。"

PRONUNCIATION: For any proper noun, name, or term that appears in pronunciation_notes, embed the pinyin guide in parentheses at its FIRST occurrence in each passage. This gives the TTS engine a phonetic anchor at the exact position. Format: 汪淼(Wāng Miǎo). Do not add pinyin for common everyday words; only for terms explicitly listed in pronunciation_notes.

Return strict JSON with a "passages" array. Each passage must preserve the numbered chunk index and contain:
{
  "chunk_index": 0,
  "speaker": "Narrator or character name",
  "text": "original text with [tag] markers and pronunciation guides embedded",
  "pace": "slow|medium|quick",
  "intensity": 1-5,
  "pause_after_ms": integer,
  "rationale": "specific performance note tied to this passage"
}
Only insert tags from the allowlist and pinyin guides. Do not otherwise alter the text. Use the exact chunk_index values supplied."""

ANNOTATE_SYSTEM_PROMPT_SINGLE_NARRATOR = """You are an audiobook director for Mandarin Chinese fiction. A SINGLE narrator voice performs the entire book — there is no voice switching between characters.
Use ElevenLabs v3 audio tags as the primary performance direction. Only use tags from this allowlist:
__ALLOWED_AUDIO_TAGS__

You will receive three memory inputs:
- Book memory: cumulative facts — character biographies, stable personalities, established relationships, overall plot context.
- Previous chapter state: the emotional and narrative handoff — where characters were left and the atmosphere at the end of the prior chapter.
- This chapter's episodic memory: atmosphere, emotional arcs, vocal quality per character, and key dramatic beats specific to this chapter.

The narrator differentiates characters through subtle shifts in delivery, pace, register, and audio tags — NOT by switching voices. For dialogue:
- Commanding/authoritative characters: slightly lower register, measured pace, [serious] or [authoritative] tone
- Young or curious characters: lighter, quicker delivery, [curious] or [excited]
- Emotional moments: lean into [crying], [whispers], [tense], [fearful] etc.
- Narrator prose: clear, even, neutral delivery

Embed audio tags INLINE in the text at the exact position where the performance direction takes effect. Examples:
  "她先是沉默，然后[whispers] 低声说：'我知道了。'"
  "[tense] 她盯着门口，[fearful] 听到脚步声越来越近。"
  "[angry] '你凭什么！'他吼道，[sad] 但眼眶已经红了。"

PRONUNCIATION: For any proper noun, name, or term that appears in pronunciation_notes, embed the pinyin guide in parentheses at its FIRST occurrence in each passage. Format: 汪淼(Wāng Miǎo). Do not add pinyin for common everyday words; only for terms explicitly listed in pronunciation_notes.

Return strict JSON with a "passages" array. Each passage must preserve the numbered chunk index. Set speaker to "Narrator" for all passages — the single narrator voice performs everything.
{
  "chunk_index": 0,
  "speaker": "Narrator",
  "text": "original text with [tag] markers and pronunciation guides embedded",
  "pace": "slow|medium|quick",
  "intensity": 1-5,
  "pause_after_ms": integer,
  "rationale": "specific delivery note — which character register shift or emotional beat to convey"
}
Only insert tags from the allowlist and pinyin guides. Do not otherwise alter the text. Use the exact chunk_index values supplied."""

ANNOTATE_USER_TEMPLATE = """Book memory (cumulative facts, character bios, story context):
{memory_json}

Previous chapter state (emotional and narrative handoff):
{prev_chapter_memory_json}

This chapter's episodic memory (atmosphere, emotional shifts, character states, key beats):
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


def annotate_project(
    store: ProjectStore,
    project_id: str,
    provider: LLMProvider,
    narration_mode: str = "multi_voice",
) -> dict[str, list[Passage]]:
    paths = store.paths(project_id)
    memory = store.load_memory(project_id)
    annotated: dict[str, list[Passage]] = {}
    prev_chapter_memory: ChapterMemory | None = None
    for source_path in list_source_chapter_paths(paths.source):
        chapter_id = source_path.stem
        text = source_path.read_text(encoding="utf-8")
        chapter_memory = store.load_chapter_memory(project_id, chapter_id)
        passages = annotate_chapter(
            chapter_id, text, memory, provider,
            chapter_memory=chapter_memory,
            prev_chapter_memory=prev_chapter_memory,
            narration_mode=narration_mode,
        )
        store.write_jsonl(paths.annotations / f"{chapter_id}.jsonl", passages)
        annotated[chapter_id] = passages
        prev_chapter_memory = chapter_memory
    return annotated


def annotate_chapter(
    chapter_id: str,
    text: str,
    memory: StoryMemory,
    provider: LLMProvider,
    chapter_memory: ChapterMemory | None = None,
    prev_chapter_memory: ChapterMemory | None = None,
    narration_mode: str = "multi_voice",
) -> list[Passage]:
    chunks = split_passages(text)
    if provider.__class__.__name__ != "HeuristicLLMProvider":
        llm_rows = try_llm_annotation(
            chapter_id, chunks, memory, provider,
            chapter_memory=chapter_memory,
            prev_chapter_memory=prev_chapter_memory,
            narration_mode=narration_mode,
        )
        if llm_rows:
            return llm_rows
    return [heuristic_passage(chapter_id, index, chunk, memory) for index, chunk in enumerate(chunks)]


def try_llm_annotation(
    chapter_id: str,
    chunks: list[str],
    memory: StoryMemory,
    provider: LLMProvider,
    chapter_memory: ChapterMemory | None = None,
    prev_chapter_memory: ChapterMemory | None = None,
    narration_mode: str = "multi_voice",
) -> list[Passage]:
    if not chunks:
        return []
    chapter_memory_json = chapter_memory.model_dump_json(exclude_none=True) if chapter_memory else "{}"
    prev_chapter_memory_json = prev_chapter_memory.model_dump_json(exclude_none=True) if prev_chapter_memory else "{}"
    base_prompt = (
        ANNOTATE_SYSTEM_PROMPT_SINGLE_NARRATOR
        if narration_mode == "single_narrator"
        else ANNOTATE_SYSTEM_PROMPT
    )
    payload = provider.complete_json(
        base_prompt.replace("__ALLOWED_AUDIO_TAGS__", allowed_audio_tags_prompt()),
        ANNOTATE_USER_TEMPLATE.format(
            memory_json=memory.model_dump_json(exclude_none=True),
            prev_chapter_memory_json=prev_chapter_memory_json,
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
    # Prefer tagged text returned by the LLM; fall back to original chunk text
    row_text = str(row.get("text") or "").strip()
    tagged_text = row_text if row_text else text
    # Derive audio_tags from inline tags embedded in the text, or from the explicit list
    inline_tags = extract_inline_tags(tagged_text)
    resolved_tags = inline_tags if inline_tags else audio_tags
    emotion = normalize_emotion(row.get("emotion"))
    delivery = normalize_delivery(row.get("delivery"))
    if not row.get("emotion"):
        emotion = emotion_from_audio_tags(resolved_tags)
    if not row.get("delivery"):
        delivery = delivery_from_audio_tags(resolved_tags)
    return Passage(
        passage_id=f"{chapter_id}-{index:04d}",
        chapter_id=chapter_id,
        index=index,
        text=tagged_text,
        speaker=normalize_speaker(row),
        emotion=emotion,
        delivery=delivery,
        pace=normalize_pace(row.get("pace")),
        intensity=normalize_intensity(row.get("intensity")),
        pause_after_ms=normalize_pause(row.get("pause_after_ms")),
        audio_tags=resolved_tags,
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
