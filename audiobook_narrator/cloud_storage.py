from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from audiobook_narrator.storage import ProjectStore

load_dotenv()

DEFAULT_BUCKET = "audiobook-artifacts"


@dataclass(frozen=True)
class CloudConfig:
    url: str
    service_role_key: str
    owner_id: str
    bucket: str = DEFAULT_BUCKET

    @classmethod
    def from_env(cls) -> "CloudConfig":
        missing = [
            key
            for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_OWNER_ID")
            if not os.getenv(key)
        ]
        if missing:
            raise RuntimeError(f"Missing Supabase cloud settings: {', '.join(missing)}")
        return cls(
            url=os.environ["SUPABASE_URL"],
            service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            owner_id=os.environ["SUPABASE_OWNER_ID"],
        )


class SupabaseProjectSync:
    def __init__(self, client: Any, config: CloudConfig) -> None:
        self.client = client
        self.config = config

    @classmethod
    def from_env(cls) -> "SupabaseProjectSync":
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                'Supabase support is not installed. Run: python3 -m pip install -e ".[supabase]"'
            ) from exc
        config = CloudConfig.from_env()
        return cls(create_client(config.url, config.service_role_key), config)

    def push_project(self, store: ProjectStore, project_id: str) -> dict[str, int | str]:
        config = store.load_config(project_id)
        chapter_rows = chapter_rows_from_local(store, project_id, self.config.owner_id)
        self.client.table("audiobook_projects").upsert(
            {
                "owner_id": self.config.owner_id,
                "project_id": config.project_id,
                "title": config.title,
                "language": config.language,
                "narration_mode": config.narration_mode,
                "created_at": config.created_at.isoformat(),
                "updated_at": config.updated_at.isoformat(),
            },
            on_conflict="owner_id,project_id",
        ).execute()
        if chapter_rows:
            self.client.table("audiobook_chapters").upsert(
                chapter_rows,
                on_conflict="owner_id,project_id,chapter_id",
            ).execute()
        local_chapter_ids = {row["chapter_id"] for row in chapter_rows}
        remote_chapter_ids = {
            row["chapter_id"]
            for row in (
                self.client.table("audiobook_chapters")
                .select("chapter_id")
                .eq("owner_id", self.config.owner_id)
                .eq("project_id", project_id)
                .execute()
                .data
                or []
            )
        }
        for chapter_id in sorted(remote_chapter_ids - local_chapter_ids):
            (
                self.client.table("audiobook_chapters")
                .delete()
                .eq("owner_id", self.config.owner_id)
                .eq("project_id", project_id)
                .eq("chapter_id", chapter_id)
                .execute()
            )

        artifacts = 0
        bucket = self.client.storage.from_(self.config.bucket)
        local_remote_paths: set[str] = set()
        for local_path in iter_artifact_files(store.paths(project_id).root):
            remote_path = artifact_object_path(
                self.config.owner_id,
                project_id,
                local_path.relative_to(store.paths(project_id).root),
            )
            local_remote_paths.add(remote_path)
            bucket.upload(
                remote_path,
                local_path.read_bytes(),
                {
                    "content-type": mimetypes.guess_type(local_path.name)[0] or "application/octet-stream",
                    "upsert": "true",
                },
            )
            artifacts += 1
        remote_paths = set(list_storage_paths(bucket, f"{self.config.owner_id}/{project_id}"))
        stale_paths = sorted(remote_paths - local_remote_paths)
        if stale_paths:
            bucket.remove(stale_paths)
        return {"project_id": project_id, "chapters": len(chapter_rows), "artifacts": artifacts}

    def pull_project(self, store: ProjectStore, project_id: str) -> dict[str, int | str]:
        project = (
            self.client.table("audiobook_projects")
            .select("*")
            .eq("owner_id", self.config.owner_id)
            .eq("project_id", project_id)
            .single()
            .execute()
            .data
        )
        if not project:
            raise FileNotFoundError(f"Project not found in Supabase: {project_id}")

        root = store.paths(project_id).root
        root.mkdir(parents=True, exist_ok=True)
        bucket = self.client.storage.from_(self.config.bucket)
        remote_paths = list_storage_paths(bucket, f"{self.config.owner_id}/{project_id}")
        artifacts = 0
        for remote_path in remote_paths:
            relative_path = Path(remote_path).relative_to(self.config.owner_id, project_id)
            local_path = root / relative_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(bucket.download(remote_path))
            artifacts += 1

        chapters = (
            self.client.table("audiobook_chapters")
            .select("chapter_id")
            .eq("owner_id", self.config.owner_id)
            .eq("project_id", project_id)
            .execute()
            .data
            or []
        )
        return {"project_id": project_id, "chapters": len(chapters), "artifacts": artifacts}


def chapter_rows_from_local(store: ProjectStore, project_id: str, owner_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_dir = store.paths(project_id).source
    for manifest_path in sorted(source_dir.glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "owner_id": owner_id,
                "project_id": project_id,
                "chapter_id": manifest["chapter_id"],
                "title": manifest["title"],
                "order_index": int(manifest.get("order", 0)),
                "char_count": int(manifest.get("char_count", 0)),
                "analyzed": bool(manifest.get("analyzed", False)),
                "annotated": bool(manifest.get("annotated", False)),
                "pipeline_state": manifest.get("pipeline_state"),
                "pipeline_message": manifest.get("pipeline_message"),
            }
        )
    return rows


def iter_artifact_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "upload.tmp.txt":
            yield path


def artifact_object_path(owner_id: str, project_id: str, relative_path: Path) -> str:
    return "/".join([owner_id, project_id, *relative_path.parts])


def list_storage_paths(bucket: Any, prefix: str) -> list[str]:
    results: list[str] = []
    pending = [prefix]
    while pending:
        current = pending.pop()
        for entry in bucket.list(current) or []:
            name = entry["name"]
            child = f"{current}/{name}"
            if entry.get("id"):
                results.append(child)
            else:
                pending.append(child)
    return sorted(results)
