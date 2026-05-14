from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from audiobook_narrator.models import (
    CharacterMemory,
    ChapterCharacterMemory,
    ChapterMemory,
    StoryMemory,
)
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
      "age": "known or inferred broad age such as child, teen, adult, elderly",
      "gender": "known or inferred gender/presentation if useful for voice casting",
      "personality": "stable overall/base personality profile for voice casting",
      "role_in_plot": "aggregate role in the story",
      "relationships": {"other character": "relationship"},
      "voice_notes": "stable ElevenLabs voice casting guidance"
    }
  ],
  "character_changes": [
    {
      "name": "canonical display name",
      "personality_at_this_point": "how they seem during this chapter",
      "changes": "what changes for this character by chapter end",
      "evidence": ["short supporting text detail"]
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
        use_heuristic_characters = provider_is_heuristic or not has_llm_character_rows(analysis)
        chapter_memory = chapter_memory_from_analysis(
            chapter_id,
            source_path.stem,
            text,
            analysis,
            use_heuristic_characters=use_heuristic_characters,
        )
        store.save_chapter_memory(project_id, chapter_memory)
        memory.chapter_summaries[chapter_id] = chapter_memory.plot_summary
        merge_themes(memory, chapter_memory.themes)
        merge_pronunciation_notes(memory, chapter_memory.pronunciation_notes)
        merge_themes(memory, analysis.get("themes", []))
        merge_pronunciation_notes(memory, analysis.get("pronunciation_notes", {}))
        merge_characters(memory, chapter_id, analysis.get("characters", []))
        merge_book_characters_from_chapter(memory, chapter_memory)
        if use_heuristic_characters:
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
            memory.current_state = chapter_memory.current_state
    if not memory.plot_summary or memory.plot_summary == GENERIC_PLOT_SUMMARY or not provider_is_heuristic:
        memory.plot_summary = build_plot_summary(memory)
    if not memory.current_state:
        memory.current_state = "Memory updated through " + ", ".join(sorted(analyzed_chapters))
    memory.updated_at = datetime.now(timezone.utc)
    store.save_memory(project_id, memory)
    return memory


def source_chapter_paths(source_dir: Path) -> list[Path]:
    paths = [path for path in source_dir.glob("*.txt") if not path.name.endswith(".annotated.txt")]

    def sort_key(path: Path) -> tuple[int, str]:
        manifest_path = source_dir / f"{path.stem}.manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                return int(manifest.get("order", 0)), path.name
            except Exception:
                return 0, path.name
        return 0, path.name

    return sorted(paths, key=sort_key)


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


def chapter_memory_from_analysis(
    chapter_id: str,
    title: str,
    text: str,
    analysis: dict,
    *,
    use_heuristic_characters: bool = True,
) -> ChapterMemory:
    summary = str(analysis.get("summary", "")).strip() or heuristic_summary(text)
    current_state = str(analysis.get("current_state", "")).strip()
    chapter_memory = ChapterMemory(
        chapter_id=chapter_id,
        title=title,
        plot_summary=summary,
        current_state=current_state,
    )
    merge_chapter_themes(chapter_memory, analysis.get("themes", []))
    merge_chapter_pronunciation_notes(chapter_memory, analysis.get("pronunciation_notes", {}))
    merge_chapter_character_changes(chapter_memory, analysis.get("character_changes", []))
    if not chapter_memory.character_changes:
        merge_chapter_character_changes(chapter_memory, analysis.get("characters", []))
    if use_heuristic_characters:
        for name in discover_character_names(text):
            if name not in chapter_memory.character_changes:
                chapter_memory.character_changes[name] = ChapterCharacterMemory(
                    name=name,
                    role_in_chapter="Appears in this chapter.",
                    personality_at_this_point=UNKNOWN_PERSONALITY,
                    changes="Needs review.",
                    evidence=[chapter_id],
                )
    return chapter_memory


def has_llm_character_rows(analysis: dict) -> bool:
    return bool(valid_character_rows(analysis.get("characters")) or valid_character_rows(analysis.get("character_changes")))


def valid_character_rows(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        return []
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = clean_name(str(row.get("name", "")).strip())
        if name and not _looks_like_common_phrase(name):
            valid.append(row)
    return valid


def merge_chapter_themes(chapter_memory: ChapterMemory, themes: object) -> None:
    if isinstance(themes, list):
        chapter_memory.themes = [str(theme).strip() for theme in themes if str(theme).strip()]


def merge_chapter_pronunciation_notes(chapter_memory: ChapterMemory, notes: object) -> None:
    if isinstance(notes, dict):
        chapter_memory.pronunciation_notes = {
            str(key).strip(): str(value).strip()
            for key, value in notes.items()
            if str(key).strip() and str(value).strip()
        }


def merge_chapter_character_changes(chapter_memory: ChapterMemory, rows: object) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = clean_name(str(row.get("name", "")).strip())
        if not name or _looks_like_common_phrase(name):
            continue
        chapter_memory.character_changes[name] = ChapterCharacterMemory(
            name=name,
            role_in_chapter=str(row.get("role_in_chapter") or row.get("role_in_plot") or "").strip(),
            personality_at_this_point=str(
                row.get("personality_at_this_point") or row.get("personality") or ""
            ).strip(),
            changes=str(row.get("changes") or "").strip(),
            evidence=[str(item).strip() for item in row.get("evidence", []) if str(item).strip()]
            if isinstance(row.get("evidence", []), list)
            else [],
        )


def merge_book_characters_from_chapter(memory: StoryMemory, chapter_memory: ChapterMemory) -> None:
    for name, change in chapter_memory.character_changes.items():
        merge_character(
            memory,
            chapter_memory.chapter_id,
            {"name": name, "personality": change.personality_at_this_point},
        )


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
    for field in ["age", "gender", "personality", "role_in_plot", "voice_notes"]:
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
