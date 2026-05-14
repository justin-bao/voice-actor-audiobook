from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Emotion(StrEnum):
    neutral = "neutral"
    tense = "tense"
    fearful = "fearful"
    angry = "angry"
    tender = "tender"
    grief = "grief"
    wonder = "wonder"
    comic = "comic"
    solemn = "solemn"
    urgent = "urgent"


class Delivery(StrEnum):
    matter_of_fact = "matter-of-fact"
    dramatic = "dramatic"
    intimate = "intimate"
    reflective = "reflective"
    clipped = "clipped"
    lyrical = "lyrical"
    conversational = "conversational"
    suspenseful = "suspenseful"


class ProjectConfig(BaseModel):
    project_id: str
    title: str
    language: str = "zh"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChapterManifest(BaseModel):
    chapter_id: str
    title: str
    source_path: str
    char_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CharacterMemory(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    age: str = ""
    gender: str = ""
    personality: str = ""
    role_in_plot: str = ""
    relationships: dict[str, str] = Field(default_factory=dict)
    voice_notes: str = ""
    evidence: list[str] = Field(default_factory=list)


class StoryMemory(BaseModel):
    title: str
    language: str = "zh"
    plot_summary: str = ""
    current_state: str = ""
    themes: list[str] = Field(default_factory=list)
    pronunciation_notes: dict[str, str] = Field(default_factory=dict)
    characters: dict[str, CharacterMemory] = Field(default_factory=dict)
    chapter_summaries: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChapterCharacterMemory(BaseModel):
    name: str
    role_in_chapter: str = ""
    personality_at_this_point: str = ""
    emotional_state: str = ""
    vocal_quality: str = ""
    key_moments: list[str] = Field(default_factory=list)
    changes: str = ""
    evidence: list[str] = Field(default_factory=list)


class ChapterMemory(BaseModel):
    chapter_id: str
    title: str = ""
    plot_summary: str = ""
    current_state: str = ""
    atmosphere: str = ""
    narrative_arc: str = ""
    themes: list[str] = Field(default_factory=list)
    pronunciation_notes: dict[str, str] = Field(default_factory=dict)
    character_changes: dict[str, ChapterCharacterMemory] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Passage(BaseModel):
    passage_id: str
    chapter_id: str
    index: int
    text: str
    speaker: str = "Narrator"
    addressee: str | None = None
    emotion: Emotion = Emotion.neutral
    delivery: Delivery = Delivery.matter_of_fact
    pace: str = "medium"
    intensity: int = Field(default=3, ge=1, le=5)
    pause_after_ms: int = 350
    pronunciation_hints: dict[str, str] = Field(default_factory=dict)
    audio_tags: list[str] = Field(default_factory=list)
    rationale: str = ""


class Voice(BaseModel):
    voice_id: str
    provider_voice: str
    language: str = "zh"
    gender: str | None = None
    age: str | None = None
    timbre: str = ""
    suitable_for: list[str] = Field(default_factory=list)


class CastAssignment(BaseModel):
    character: str
    voice_id: str
    reason: str


class Cast(BaseModel):
    assignments: dict[str, CastAssignment] = Field(default_factory=dict)
    voices: dict[str, Voice] = Field(default_factory=dict)


class ProjectPaths(BaseModel):
    root: Path
    source: Path
    memory: Path
    annotations: Path
    casts: Path
    scripts: Path
    audio: Path

    model_config = {"arbitrary_types_allowed": True}


JsonDict = dict[str, Any]
