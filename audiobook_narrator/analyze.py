from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from audiobook_narrator.models import CharacterMemory, StoryMemory
from audiobook_narrator.providers import LLMProvider
from audiobook_narrator.storage import ProjectStore


CHINESE_NAME = re.compile(r"[\u4e00-\u9fff]{2,4}")
UNKNOWN_PERSONALITY = "Unknown; update after manual review or LLM analysis."
GENERIC_PLOT_SUMMARY = (
    "Story memory initialized from ingested chapters. Review and enrich as chapters are analyzed."
)

ANALYZE_SYSTEM_PROMPT = """You maintain structured memory for Mandarin Chinese audiobook narration.
Extract only evidence-supported story facts from the chapter. Return strict JSON with:
{
  "summary": "chapter plot summary focused on causality and stakes",
  "current_state": "where the story/scene stands at chapter end",
  "themes": ["short theme labels"],
  "pronunciation_notes": {"term": "how to read it or why it matters"},
  "characters": [
    {
      "name": "canonical display name",
      "aliases": ["other names"],
      "personality": "specific traits shown in this chapter",
      "role_in_plot": "what this character does or wants",
      "relationships": {"other character": "relationship"},
      "voice_notes": "narration/casting guidance for this point in the book"
    }
  ]
}
Do not use placeholders like Unknown when the chapter gives evidence."""

ANALYZE_USER_TEMPLATE = """Project title: {title}
Chapter id: {chapter_id}

Existing memory:
{memory_json}

Chapter text:
{text}"""


def update_story_memory(store: ProjectStore, project_id: str, provider: LLMProvider) -> StoryMemory:
    paths = store.paths(project_id)
    memory = store.load_memory(project_id)
    provider_is_heuristic = provider.__class__.__name__ == "HeuristicLLMProvider"
    analyzed_chapters: list[str] = []
    for source_path in source_chapter_paths(paths.source):
        chapter_id = source_path.stem
        text = source_path.read_text(encoding="utf-8")
        analyzed_chapters.append(chapter_id)
        if provider_is_heuristic and chapter_id in memory.chapter_summaries:
            analysis = {"summary": memory.chapter_summaries[chapter_id]}
        else:
            analysis = analyze_chapter(chapter_id, text, memory, provider)
        summary = str(analysis.get("summary", "")).strip() or heuristic_summary(text)
        memory.chapter_summaries[chapter_id] = summary
        merge_themes(memory, analysis.get("themes", []))
        merge_pronunciation_notes(memory, analysis.get("pronunciation_notes", {}))
        merge_characters(memory, chapter_id, analysis.get("characters", []))
        for name in discover_character_names(text):
            merge_character(
                memory,
                chapter_id,
                {
                    "name": name,
                    "personality": UNKNOWN_PERSONALITY,
                    "role_in_plot": "Appears in the source text.",
                    "voice_notes": "Choose a distinct, emotionally flexible Mandarin voice.",
                },
            )
        if analysis.get("current_state"):
            memory.current_state = str(analysis["current_state"]).strip()
    if not memory.plot_summary or memory.plot_summary == GENERIC_PLOT_SUMMARY or not provider_is_heuristic:
        memory.plot_summary = build_plot_summary(memory)
    if not memory.current_state:
        memory.current_state = "Memory updated through " + ", ".join(sorted(analyzed_chapters))
    memory.updated_at = datetime.now(timezone.utc)
    store.save_memory(project_id, memory)
    return memory


def source_chapter_paths(source_dir: Path) -> list[Path]:
    return sorted(
        path for path in source_dir.glob("*.txt") if not path.name.endswith(".annotated.txt")
    )


def analyze_chapter(chapter_id: str, text: str, memory: StoryMemory, provider: LLMProvider) -> dict:
    if provider.__class__.__name__ != "HeuristicLLMProvider":
        payload = provider.complete_json(
            ANALYZE_SYSTEM_PROMPT,
            ANALYZE_USER_TEMPLATE.format(
                title=memory.title,
                chapter_id=chapter_id,
                memory_json=memory.model_dump_json(exclude_none=True),
                text=text[:12000],
            ),
        )
        payload.setdefault("summary", heuristic_summary(text))
        return payload
    return {"summary": heuristic_summary(text), "characters": heuristic_character_rows(text)}


def summarize_chapter(text: str, provider: LLMProvider) -> str:
    if provider.__class__.__name__ != "HeuristicLLMProvider":
        payload = provider.complete_json(
            "You summarize Chinese fiction chapters for audiobook narration memory. Return JSON.",
            "Return {\"summary\":\"...\",\"themes\":[...],\"characters\":[...]} for this chapter:\n"
            + text[:12000],
        )
        return str(payload.get("summary", "")).strip() or heuristic_summary(text)
    return heuristic_summary(text)


def build_plot_summary(memory: StoryMemory) -> str:
    summaries = [summary for _, summary in sorted(memory.chapter_summaries.items()) if summary]
    if not summaries:
        return GENERIC_PLOT_SUMMARY
    joined = " ".join(summaries)
    return joined[:1200] + ("..." if len(joined) > 1200 else "")


def merge_themes(memory: StoryMemory, themes: object) -> None:
    if not isinstance(themes, list):
        return
    seen = set(memory.themes)
    for theme in themes:
        value = str(theme).strip()
        if value and value not in seen:
            memory.themes.append(value)
            seen.add(value)


def merge_pronunciation_notes(memory: StoryMemory, notes: object) -> None:
    if not isinstance(notes, dict):
        return
    for key, value in notes.items():
        term = str(key).strip()
        note = str(value).strip()
        if term and note:
            memory.pronunciation_notes[term] = note


def merge_characters(memory: StoryMemory, chapter_id: str, characters: object) -> None:
    if not isinstance(characters, list):
        return
    for row in characters:
        if isinstance(row, dict):
            merge_character(memory, chapter_id, row)


def merge_character(memory: StoryMemory, chapter_id: str, row: dict) -> None:
    name = clean_name(str(row.get("name", "")).strip())
    if not name or _looks_like_common_phrase(name):
        return
    existing = memory.characters.get(name)
    if existing is None:
        existing = CharacterMemory(name=name)
        memory.characters[name] = existing
    aliases = row.get("aliases", [])
    if isinstance(aliases, list):
        for alias in aliases:
            value = str(alias).strip()
            if value and value not in existing.aliases:
                existing.aliases.append(value)
    for field in ["personality", "role_in_plot", "voice_notes"]:
        value = str(row.get(field, "")).strip()
        current = getattr(existing, field)
        if value and (not current or current == UNKNOWN_PERSONALITY or provider_placeholder(current)):
            setattr(existing, field, value)
    relationships = row.get("relationships", {})
    if isinstance(relationships, dict):
        for key, value in relationships.items():
            if str(key).strip() and str(value).strip():
                existing.relationships[str(key).strip()] = str(value).strip()
    if chapter_id not in existing.evidence:
        existing.evidence.append(chapter_id)


def provider_placeholder(value: str) -> bool:
    lowered = value.lower()
    return "unknown" in lowered or "manual review" in lowered


def heuristic_character_rows(text: str) -> list[dict]:
    return [
        {
            "name": name,
            "personality": UNKNOWN_PERSONALITY,
            "role_in_plot": "Appears in the source text.",
            "voice_notes": "Choose a distinct, emotionally flexible Mandarin voice.",
        }
        for name in discover_character_names(text)
    ]


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
