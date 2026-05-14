from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel

from audiobook_narrator.models import ChapterMemory, ProjectConfig, ProjectPaths, StoryMemory

T = TypeVar("T", bound=BaseModel)


class ProjectStore:
    def __init__(self, base_dir: Path = Path("projects")) -> None:
        self.base_dir = base_dir

    def paths(self, project_id: str) -> ProjectPaths:
        root = self.base_dir / project_id
        return ProjectPaths(
            root=root,
            source=root / "source",
            memory=root / "memory",
            annotations=root / "annotations",
            casts=root / "casts",
            scripts=root / "scripts",
            audio=root / "audio",
        )

    def create_project(self, project_id: str, title: str, language: str = "zh") -> ProjectConfig:
        paths = self.paths(project_id)
        for folder in paths.model_dump().values():
            Path(folder).mkdir(parents=True, exist_ok=True)
        config = ProjectConfig(project_id=project_id, title=title, language=language)
        self.write_json(paths.root / "project.json", config)
        memory = StoryMemory(title=title, language=language)
        self.write_json(paths.memory / "story.json", memory)
        return config

    def load_config(self, project_id: str) -> ProjectConfig:
        return self.read_json(self.paths(project_id).root / "project.json", ProjectConfig)

    def load_memory(self, project_id: str) -> StoryMemory:
        return self.read_json(self.paths(project_id).memory / "story.json", StoryMemory)

    def save_memory(self, project_id: str, memory: StoryMemory) -> None:
        self.write_json(self.paths(project_id).memory / "story.json", memory)

    def load_chapter_memory(self, project_id: str, chapter_id: str) -> ChapterMemory | None:
        path = self.paths(project_id).memory / "chapters" / f"{chapter_id}.json"
        if not path.exists():
            return None
        return self.read_json(path, ChapterMemory)

    def save_chapter_memory(self, project_id: str, chapter_memory: ChapterMemory) -> None:
        self.write_json(
            self.paths(project_id).memory / "chapters" / f"{chapter_memory.chapter_id}.json",
            chapter_memory,
        )

    def list_chapter_memories(self, project_id: str) -> list[ChapterMemory]:
        folder = self.paths(project_id).memory / "chapters"
        if not folder.exists():
            return []
        return [self.read_json(path, ChapterMemory) for path in sorted(folder.glob("*.json"))]

    @staticmethod
    def write_json(path: Path, model_or_dict: BaseModel | dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(model_or_dict, BaseModel):
            payload = model_or_dict.model_dump(mode="json", exclude_none=True)
        else:
            payload = model_or_dict
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def read_json(path: Path, model: type[T]) -> T:
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_jsonl(path: Path, rows: Iterable[BaseModel | dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for row in rows:
            payload = row.model_dump(mode="json", exclude_none=True) if isinstance(row, BaseModel) else row
            lines.append(json.dumps(payload, ensure_ascii=False))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
