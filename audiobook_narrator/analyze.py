from __future__ import annotations

import re
from datetime import datetime, timezone

from audiobook_narrator.models import CharacterMemory, StoryMemory
from audiobook_narrator.providers import LLMProvider
from audiobook_narrator.storage import ProjectStore


CHINESE_NAME = re.compile(r"[\u4e00-\u9fff]{2,4}")


def update_story_memory(store: ProjectStore, project_id: str, provider: LLMProvider) -> StoryMemory:
    paths = store.paths(project_id)
    memory = store.load_memory(project_id)
    for source_path in sorted(paths.source.glob("*.txt")):
        chapter_id = source_path.stem
        text = source_path.read_text(encoding="utf-8")
        if chapter_id not in memory.chapter_summaries:
            memory.chapter_summaries[chapter_id] = summarize_chapter(text, provider)
        for name in discover_character_names(text):
            memory.characters.setdefault(
                name,
                CharacterMemory(
                    name=name,
                    personality="Unknown; update after manual review or LLM analysis.",
                    role_in_plot="Appears in the source text.",
                    voice_notes="Choose a distinct, emotionally flexible Mandarin voice.",
                    evidence=[chapter_id],
                ),
            )
            if chapter_id not in memory.characters[name].evidence:
                memory.characters[name].evidence.append(chapter_id)
    if not memory.plot_summary:
        memory.plot_summary = "Story memory initialized from ingested chapters. Review and enrich as chapters are analyzed."
    memory.current_state = "Memory updated through " + ", ".join(sorted(memory.chapter_summaries))
    memory.updated_at = datetime.now(timezone.utc)
    store.save_memory(project_id, memory)
    return memory


def summarize_chapter(text: str, provider: LLMProvider) -> str:
    if provider.__class__.__name__ != "HeuristicLLMProvider":
        payload = provider.complete_json(
            "You summarize Chinese fiction chapters for audiobook narration memory. Return JSON.",
            "Return {\"summary\":\"...\",\"themes\":[...],\"characters\":[...]} for this chapter:\n"
            + text[:12000],
        )
        return str(payload.get("summary", "")).strip() or heuristic_summary(text)
    return heuristic_summary(text)


def heuristic_summary(text: str) -> str:
    clean = re.sub(r"\s+", "", text)
    return clean[:260] + ("..." if len(clean) > 260 else "")


def discover_character_names(text: str) -> list[str]:
    candidates: dict[str, int] = {}
    for match in re.finditer(
        rf"({CHINESE_NAME.pattern})(?:轻声|低声|大声|冷冷|忽然)?(?:说|问|道|回答)", text
    ):
        name = clean_name(match.group(1))
        if not _looks_like_common_phrase(name):
            candidates[name] = candidates.get(name, 0) + 1
    for match in re.finditer(rf"[”」』]({CHINESE_NAME.pattern})(?:说|问|道|回答)", text):
        name = clean_name(match.group(1))
        if not _looks_like_common_phrase(name):
            candidates[name] = candidates.get(name, 0) + 1
    return [name for name, _ in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[:24]]


def clean_name(name: str) -> str:
    for suffix in ["轻声", "低声", "大声", "冷冷", "忽然"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if len(name) > 3 and name[-2:] in {"门外", "一种"}:
        return ""
    return name


def _looks_like_common_phrase(value: str) -> bool:
    blocked = {
        "",
        "他们",
        "我们",
        "你们",
        "这里",
        "那里",
        "这个",
        "那个",
        "自己",
        "什么",
        "没有",
        "不是",
        "有人",
        "人在",
    }
    return value in blocked or len(value) < 2
