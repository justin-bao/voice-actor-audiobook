from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from audiobook_narrator.analyze import update_story_memory
from audiobook_narrator.auth import SupabaseAuthService
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
from audiobook_narrator.captions import export_captioned_video
from audiobook_narrator.document_import import import_document_text, title_from_filename
from audiobook_narrator.evals import evaluate_analysis, evaluate_annotations, evaluate_cast
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.models import Cast, Passage, StoryMemory
from audiobook_narrator.providers import (
    ElevenLabsTTSProvider,
    flush_langfuse,
    get_llm_provider,
    get_tts_provider,
    langfuse_observation,
    provider_summary,
    score_langfuse_current_trace,
    update_langfuse_observation,
)
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.synthesize import synthesize_chapter


STATIC_DIR = Path(__file__).resolve().parent / "frontend"
logger = logging.getLogger(__name__)


def decoded_path_parts(path: str) -> list[str]:
    return [unquote(part) for part in urlparse(path).path.strip("/").split("/") if part]


def title_to_project_id(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    normalized = re.sub(r"\s+", "-", normalized)
    slug = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-.")
    return slug[:80] or "book"


def natural_sort_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


class NarratorWebApp:
    def __init__(self, projects_dir: Path, user_id: str | None = None) -> None:
        self.projects_dir = projects_dir
        self.user_id = user_id
        self.store = ProjectStore(projects_dir if user_id is None else projects_dir / user_id)
        self._synthesis_progress: dict[tuple[str, str], dict] = {}
        self._progress_lock = threading.Lock()
        self._canceled_chapters: set[tuple[str, str]] = set()
        self._user_apps: dict[str, "NarratorWebApp"] = {}

    def for_user(self, user_id: str) -> "NarratorWebApp":
        if self.user_id == user_id:
            return self
        if user_id not in self._user_apps:
            self._user_apps[user_id] = NarratorWebApp(self.projects_dir, user_id=user_id)
        return self._user_apps[user_id]

    def list_projects(self) -> dict:
        projects = []
        if self.store.base_dir.exists():
            for project_file in sorted(self.store.base_dir.glob("*/project.json")):
                config = json.loads(project_file.read_text(encoding="utf-8"))
                projects.append(config)
        return {"projects": projects}

    def create_project(self, body: dict) -> dict:
        title = body["title"].strip()
        if not title:
            raise ValueError("Book title is required.")
        requested_id = body.get("project_id", "").strip()
        base_project_id = self.safe_project_id(requested_id) if requested_id else title_to_project_id(title)
        project_id = self.unique_project_id(base_project_id, title)
        language = body.get("language", "zh")
        config_path = self.store.paths(project_id).root / "project.json"
        if config_path.exists():
            return self.store.load_config(project_id).model_dump(mode="json")
        config = self.store.create_project(project_id, title, language)
        return config.model_dump(mode="json")

    def rename_project(self, project_id: str, body: dict) -> dict:
        project_id = self.safe_project_id(project_id)
        title = body.get("title", "").strip()
        if not title:
            raise ValueError("Book title is required.")
        config = self.store.load_config(project_id)
        config.title = title
        self.store.write_json(self.store.paths(project_id).root / "project.json", config)
        memory_path = self.store.paths(project_id).memory / "story.json"
        if memory_path.exists():
            memory = self.store.load_memory(project_id)
            memory.title = title
            self.store.save_memory(project_id, memory)
        return {"ok": True, "config": config.model_dump(mode="json")}

    def delete_project(self, project_id: str) -> dict:
        project_id = self.safe_project_id(project_id)
        root = self.store.paths(project_id).root
        if root.exists():
            shutil.rmtree(root)
        return {"ok": True, "project_id": project_id}

    def project_payload(self, project_id: str, chapter_id: str | None = None) -> dict:
        paths = self.store.paths(project_id)
        config = self.store.load_config(project_id)
        chapters = self.load_chapter_manifests(project_id)
        selected = chapter_id
        source_text = ""
        annotations = []
        annotated_text = ""
        chapter_memory = None
        audio_manifest = None
        audio_url = None
        if selected:
            source_path = paths.source / f"{selected}.txt"
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
            annotations = self.store.read_jsonl(paths.annotations / f"{selected}.jsonl")
            annotated_path = paths.source / f"{selected}.annotated.txt"
            if annotated_path.exists():
                annotated_text = annotated_path.read_text(encoding="utf-8")
            loaded_chapter_memory = self.store.load_chapter_memory(project_id, selected)
            if loaded_chapter_memory:
                chapter_memory = loaded_chapter_memory.model_dump(mode="json")
            manifest_path = paths.audio / f"{selected}.parts.json"
            audio_path = paths.audio / f"{selected}.mp3"
            if manifest_path.exists() and audio_path.exists():
                audio_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mtime = int(audio_path.stat().st_mtime)
                audio_url = f"/api/projects/{project_id}/audio/{selected}?v={mtime}"
        memory_path = paths.memory / "story.json"
        cast_path = paths.casts / "voices.json"
        return {
            "config": config.model_dump(mode="json"),
            "chapters": chapters,
            "selected_chapter_id": selected,
            "source_text": source_text,
            "memory": json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else None,
            "chapter_memory": chapter_memory,
            "annotations": annotations,
            "annotated_text": annotated_text,
            "cast": json.loads(cast_path.read_text(encoding="utf-8")) if cast_path.exists() else None,
            "audio_manifest": audio_manifest,
            "audio_url": audio_url,
        }

    def save_chapter(self, project_id: str, body: dict) -> dict:
        paths = self.store.paths(project_id)
        chapter_id = self.safe_chapter_id(body["chapter_id"].strip())
        title = body.get("title", chapter_id).strip() or chapter_id
        source_path = paths.source / f"{chapter_id}.txt"
        previous_text = source_path.read_text(encoding="utf-8") if source_path.exists() else None
        next_text = body.get("text", "")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(next_text, encoding="utf-8")
        manifest_path = paths.source / f"{chapter_id}.manifest.json"
        existing_manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        )
        manifest = {
            "chapter_id": chapter_id,
            "title": title,
            "source_path": str(source_path),
            "char_count": len(next_text),
            "order": existing_manifest.get("order", self.next_chapter_order(project_id)),
            "analyzed": existing_manifest.get("analyzed", False),
            "annotated": existing_manifest.get("annotated", False),
        }
        if previous_text != next_text:
            manifest["analyzed"] = False
            manifest["annotated"] = False
        self.store.write_json(manifest_path, manifest)
        return {"ok": True, "manifest": manifest}

    def import_chapter(self, project_id: str, body: dict) -> dict:
        filename = body["filename"]
        text = import_document_text(filename, body["data"])
        chapter_id = self.safe_chapter_id(body.get("chapter_id") or Path(filename).stem)
        title = body.get("title") or title_from_filename(filename)
        tmp = self.store.paths(project_id).root / "upload.tmp.txt"
        tmp.write_text(text, encoding="utf-8")
        try:
            manifest = ingest_chapter(self.store, project_id, tmp, title, chapter_id)
        finally:
            tmp.unlink(missing_ok=True)
        manifest_payload = manifest.model_dump(mode="json")
        manifest_payload["order"] = self.next_chapter_order(project_id, exclude_chapter_id=chapter_id)
        self.store.write_json(
            self.store.paths(project_id).source / f"{chapter_id}.manifest.json",
            manifest_payload,
        )
        return {"ok": True, "manifest": manifest_payload, "text": text}

    def bulk_import(self, project_id: str, body: dict) -> dict:
        files = body.get("files", [])
        if not files:
            raise ValueError("No files were provided for bulk import.")
        manifests = []
        for row in files:
            payload = self.import_chapter(project_id, row)
            manifests.append(payload["manifest"])
        result: dict[str, object] = {"ok": True, "manifests": manifests}
        if body.get("analyze", True):
            provider = get_llm_provider(not body.get("no_openai"))
            logger.info(
                "Web step=bulk-import-analyze project=%s chapters=%s llm=%s",
                project_id,
                len(manifests),
                provider_summary(provider),
            )
            with langfuse_observation(
                "audiobook-bulk-import-analyze",
                input={"project_id": project_id, "chapters": [row["chapter_id"] for row in manifests]},
                metadata={"step": "bulk-import-analyze", "llm": provider_summary(provider)},
            ) as observation:
                memory = update_story_memory(self.store, project_id, provider)
                for manifest in manifests:
                    self.update_chapter_status(
                        project_id,
                        manifest["chapter_id"],
                        analyzed=True,
                        pipeline_state="analyzed",
                        pipeline_message="Analysis complete.",
                    )
                update_langfuse_observation(
                    observation,
                    output={
                        "chapters": len(memory.chapter_summaries),
                        "characters": len(memory.characters),
                    },
                )
                score_langfuse_current_trace(evaluate_analysis(memory))
            flush_langfuse()
            result["memory"] = memory.model_dump(mode="json")
            result["llm"] = provider_summary(provider)
        return result

    def update_config(self, project_id: str, body: dict) -> dict:
        project_id = self.safe_project_id(project_id)
        config = self.store.load_config(project_id)
        if "narration_mode" in body:
            mode = str(body["narration_mode"]).strip()
            if mode not in {"multi_voice", "single_narrator"}:
                raise ValueError(f"Invalid narration_mode: {mode!r}")
            config.narration_mode = mode
        self.store.write_json(self.store.paths(project_id).root / "project.json", config)
        return {"ok": True, "config": config.model_dump(mode="json")}

    def save_memory(self, project_id: str, body: dict) -> dict:
        memory = StoryMemory.model_validate(body)
        self.store.save_memory(project_id, memory)
        return {"ok": True, "memory": memory.model_dump(mode="json")}

    def save_chapter_memory(self, project_id: str, chapter_id: str, body: dict) -> dict:
        from audiobook_narrator.models import ChapterMemory

        payload = {**body, "chapter_id": self.safe_chapter_id(chapter_id)}
        chapter_memory = ChapterMemory.model_validate(payload)
        self.store.save_chapter_memory(project_id, chapter_memory)
        return {"ok": True, "chapter_memory": chapter_memory.model_dump(mode="json")}

    def reorder_chapters(self, project_id: str, body: dict) -> dict:
        paths = self.store.paths(project_id)
        chapter_ids = [self.safe_chapter_id(str(chapter_id)) for chapter_id in body.get("chapter_ids", [])]
        seen = set()
        ordered_ids = []
        for chapter_id in chapter_ids:
            if chapter_id not in seen:
                ordered_ids.append(chapter_id)
                seen.add(chapter_id)
        manifests_by_id = {row["chapter_id"]: row for row in self.load_chapter_manifests(project_id)}
        for chapter_id in manifests_by_id:
            if chapter_id not in seen:
                ordered_ids.append(chapter_id)
        for order, chapter_id in enumerate(ordered_ids):
            manifest = manifests_by_id.get(chapter_id)
            if not manifest:
                continue
            manifest["order"] = order
            self.store.write_json(paths.source / f"{chapter_id}.manifest.json", manifest)
        return {"ok": True, "chapters": self.load_chapter_manifests(project_id)}

    def save_annotations(self, project_id: str, chapter_id: str, body: dict) -> dict:
        chapter_id = self.safe_chapter_id(chapter_id)
        passages = [Passage.model_validate(row) for row in body.get("annotations", [])]
        self.store.write_jsonl(self.store.paths(project_id).annotations / f"{chapter_id}.jsonl", passages)
        return {"ok": True, "count": len(passages)}

    def save_annotated_text(self, project_id: str, chapter_id: str, body: dict) -> dict:
        chapter_id = self.safe_chapter_id(chapter_id)
        annotated_text = body.get("text", "")
        path = self.store.paths(project_id).source / f"{chapter_id}.annotated.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(annotated_text, encoding="utf-8")
        return {"ok": True, "path": str(path)}

    def reset_annotations(self, project_id: str, chapter_id: str) -> dict:
        chapter_id = self.safe_chapter_id(chapter_id)
        paths = self.store.paths(project_id)
        removed = []
        candidates = [
            paths.annotations / f"{chapter_id}.jsonl",
            paths.source / f"{chapter_id}.annotated.txt",
            paths.scripts / f"{chapter_id}.ssml.xml",
            paths.audio / f"{chapter_id}.txt",
            paths.audio / f"{chapter_id}.mp3",
            paths.audio / f"{chapter_id}.aiff",
            paths.audio / f"{chapter_id}.parts.json",
        ]
        candidates.extend(paths.audio.glob(f"{chapter_id}_*.mp3"))
        candidates.extend(paths.audio.glob(f"{chapter_id}_*.aiff"))
        candidates.extend(paths.audio.glob(f"{chapter_id}_parts*"))
        for path in candidates:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(str(path))
        self.update_chapter_status(project_id, chapter_id, annotated=False)
        return {"ok": True, "removed": removed}

    def delete_chapter(self, project_id: str, chapter_id: str) -> dict:
        chapter_id = self.safe_chapter_id(chapter_id)
        paths = self.store.paths(project_id)
        removed = []
        candidates = [
            paths.source / f"{chapter_id}.txt",
            paths.source / f"{chapter_id}.manifest.json",
            paths.source / f"{chapter_id}.annotated.txt",
            paths.annotations / f"{chapter_id}.jsonl",
            paths.scripts / f"{chapter_id}.ssml.xml",
            paths.audio / f"{chapter_id}.txt",
            paths.audio / f"{chapter_id}.mp3",
            paths.audio / f"{chapter_id}.aiff",
            paths.audio / f"{chapter_id}.parts.json",
            paths.memory / "chapters" / f"{chapter_id}.json",
        ]
        candidates.extend(paths.audio.glob(f"{chapter_id}_*.mp3"))
        candidates.extend(paths.audio.glob(f"{chapter_id}_*.aiff"))
        candidates.extend(paths.audio.glob(f"{chapter_id}_parts*"))
        for path in candidates:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(str(path))
        remaining = sorted(
            manifest.stem.replace(".manifest", "") for manifest in paths.source.glob("*.manifest.json")
        )
        return {"ok": True, "removed": removed, "remaining_chapter_ids": remaining}

    def save_cast(self, project_id: str, body: dict) -> dict:
        cast = Cast.model_validate(body)
        self.store.write_json(self.store.paths(project_id).casts / "voices.json", cast)
        return {"ok": True, "cast": cast.model_dump(mode="json")}

    def run_step(self, project_id: str, step: str, chapter_id: str | None, body: dict) -> dict:
        config = self.store.load_config(project_id)
        narration_mode = config.narration_mode
        if step == "analyze":
            if not chapter_id:
                raise ValueError("chapter_id is required for analyze.")
            safe_chapter_id = self.safe_chapter_id(chapter_id)
            self.require_previous_chapter_analysis(project_id, safe_chapter_id)
            self.update_chapter_status(
                project_id,
                safe_chapter_id,
                pipeline_state="analyzing",
                pipeline_message="Analyzing chapter context.",
            )
            provider = get_llm_provider(not body.get("no_openai"))
            logger.info("Web step=analyze project=%s llm=%s mode=%s", project_id, provider_summary(provider), narration_mode)
            try:
                with langfuse_observation(
                    "audiobook-analyze",
                    input={"project_id": project_id},
                    metadata={"step": "analyze", "llm": provider_summary(provider), "narration_mode": narration_mode},
                ) as observation:
                    memory = update_story_memory(
                        self.store,
                        project_id,
                        provider,
                        narration_mode=narration_mode,
                        chapter_ids={safe_chapter_id},
                    )
                    cast = build_cast(self.store, project_id, self.safe_elevenlabs_voices(), provider=provider, narration_mode=narration_mode)
                    self.raise_if_chapter_canceled(project_id, safe_chapter_id)
                    self.update_chapter_status(
                        project_id,
                        safe_chapter_id,
                        analyzed=True,
                        pipeline_state="analyzed",
                        pipeline_message="Analysis complete.",
                    )
                    update_langfuse_observation(
                        observation,
                        output={
                            "chapters": len(memory.chapter_summaries),
                            "characters": len(memory.characters),
                        },
                    )
                    score_langfuse_current_trace(evaluate_analysis(memory))
            except Exception as exc:
                if (project_id, safe_chapter_id) in self._canceled_chapters:
                    raise
                self.update_chapter_status(
                    project_id,
                    safe_chapter_id,
                    pipeline_state="error",
                    pipeline_message=str(exc),
                )
                self.mark_chapters_paused_after(
                    project_id,
                    safe_chapter_id,
                    f"Paused because {safe_chapter_id} failed: {exc}",
                )
                raise
            flush_langfuse()
            return {"ok": True, "memory": memory.model_dump(mode="json"), "cast": cast.model_dump(mode="json"), "llm": provider_summary(provider)}
        if step == "annotate":
            if not chapter_id:
                raise ValueError("chapter_id is required for annotate.")
            safe_chapter_id = self.safe_chapter_id(chapter_id)
            self.update_chapter_status(
                project_id,
                safe_chapter_id,
                pipeline_state="annotating",
                pipeline_message="Annotating chapter.",
            )
            provider = get_llm_provider(not body.get("no_openai"))
            logger.info("Web step=annotate project=%s llm=%s mode=%s", project_id, provider_summary(provider), narration_mode)
            try:
                with langfuse_observation(
                    "audiobook-annotate",
                    input={"project_id": project_id},
                    metadata={"step": "annotate", "llm": provider_summary(provider), "narration_mode": narration_mode},
                ) as observation:
                    annotated = annotate_project(
                        self.store,
                        project_id,
                        provider,
                        narration_mode=narration_mode,
                        chapter_ids={safe_chapter_id},
                    )
                    update_langfuse_observation(
                        observation,
                        output={"chapters": {key: len(value) for key, value in annotated.items()}},
                    )
                    passages = [passage for rows in annotated.values() for passage in rows]
                    score_langfuse_current_trace(evaluate_annotations(passages))
                    self.raise_if_chapter_canceled(project_id, safe_chapter_id)
                    self.update_chapter_status(
                        project_id,
                        safe_chapter_id,
                        annotated=True,
                        pipeline_state="complete",
                        pipeline_message="Analysis and annotation complete.",
                    )
            except Exception as exc:
                if (project_id, safe_chapter_id) in self._canceled_chapters:
                    raise
                self.update_chapter_status(
                    project_id,
                    safe_chapter_id,
                    pipeline_state="error",
                    pipeline_message=str(exc),
                )
                self.mark_chapters_paused_after(
                    project_id,
                    safe_chapter_id,
                    f"Paused because {safe_chapter_id} failed: {exc}",
                )
                raise
            flush_langfuse()
            return {
                "ok": True,
                "chapters": {key: len(value) for key, value in annotated.items()},
                "llm": provider_summary(provider),
            }
        if step == "cast":
            provider = get_llm_provider(not body.get("no_openai"))
            cast = build_cast(self.store, project_id, self.safe_elevenlabs_voices(), provider=provider, narration_mode=narration_mode)
            with langfuse_observation(
                "audiobook-cast",
                input={"project_id": project_id},
                metadata={"step": "cast"},
            ) as observation:
                memory = self.store.load_memory(project_id)
                update_langfuse_observation(
                    observation,
                    output={
                        "assignments": len(cast.assignments),
                        "voices": len(cast.voices),
                    },
                )
                score_langfuse_current_trace(evaluate_cast(cast, memory))
            flush_langfuse()
            return {"ok": True, "cast": cast.model_dump(mode="json")}
        if step == "synthesize":
            if not chapter_id:
                raise ValueError("chapter_id is required for synthesis.")
            backend = body.get("backend", "script")
            extension = ".mp3" if backend in {"elevenlabs", "openai"} else ".aiff" if backend == "macos_say" else ".txt"
            with langfuse_observation(
                "audiobook-synthesize",
                input={"project_id": project_id, "chapter_id": chapter_id},
                metadata={"step": "synthesize", "backend": backend},
            ) as observation:
                self.update_synthesis_progress(
                    project_id,
                    chapter_id,
                    {"phase": "starting", "total_chunks": 0, "completed_chunks": 0, "current_chunk": 0},
                )
                ssml, audio = synthesize_chapter(
                    self.store,
                    project_id,
                    chapter_id,
                    get_tts_provider(backend),
                    extension,
                    lambda patch: self.update_synthesis_progress(project_id, chapter_id, patch),
                )
                update_langfuse_observation(observation, output={"ssml": ssml, "output": audio})
            flush_langfuse()
            return {"ok": True, "ssml": ssml, "output": audio}
        if step == "captioned_video":
            if not chapter_id:
                raise ValueError("chapter_id is required for captioned video export.")
            output = export_captioned_video(self.store, project_id, chapter_id)
            return {"ok": True, **output}
        raise ValueError(f"Unknown step: {step}")

    def regenerate_chunk(self, project_id: str, chapter_id: str, chunk_index: int, backend: str) -> dict:
        if backend != "elevenlabs":
            raise ValueError("Block regeneration requires the elevenlabs backend.")
        paths = self.store.paths(project_id)
        manifest_path = paths.audio / f"{chapter_id}.parts.json"
        if not manifest_path.exists():
            raise ValueError(f"No audio manifest for chapter {chapter_id}. Run full synthesis first.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if chunk_index < 0 or chunk_index >= len(manifest):
            raise ValueError(f"Chunk {chunk_index} is out of range (0–{len(manifest) - 1}).")
        chunk = manifest[chunk_index]
        passage_indices = [p["index"] for p in chunk.get("passages", [])]
        all_annotations = self.store.read_jsonl(paths.annotations / f"{chapter_id}.jsonl")
        passages = [
            Passage.model_validate(all_annotations[i])
            for i in passage_indices
            if i < len(all_annotations)
        ]
        cast = self.store.read_json(paths.casts / "voices.json", Cast)
        provider = ElevenLabsTTSProvider()
        provider.regenerate_chunk_audio(passages, speaker_voice_map(cast), Path(chunk["path"]))
        combined_path = paths.audio / f"{chapter_id}.mp3"
        with open(combined_path, "wb") as combined:
            for c in manifest:
                chunk_path = Path(c["path"])
                if chunk_path.exists():
                    combined.write(chunk_path.read_bytes())
        return {"ok": True, "chunk_index": chunk_index}

    def download_book_audio(self, project_id: str, chapter_ids: list[str] | None = None) -> tuple[bytes, str]:
        project_id = self.safe_project_id(project_id)
        paths = self.store.paths(project_id)
        config = self.store.load_config(project_id)
        chapters = self.load_chapter_manifests(project_id)
        if chapter_ids is not None:
            allowed = set(chapter_ids)
            chapters = [c for c in chapters if c.get("chapter_id") in allowed]
        chunks: list[bytes] = []
        for chapter in chapters:
            chapter_id = chapter.get("chapter_id", "")
            mp3 = paths.audio / f"{chapter_id}.mp3"
            if mp3.exists():
                chunks.append(mp3.read_bytes())
        if not chunks:
            raise ValueError("No generated audio found for the selected chapters.")
        safe_title = re.sub(r"[^\w\s-]", "", config.title).strip().replace(" ", "_") or project_id
        filename = f"{safe_title}.mp3"
        return b"".join(chunks), filename

    def safe_elevenlabs_voices(self) -> list[dict]:
        try:
            return ElevenLabsTTSProvider().list_voices()
        except Exception as exc:
            logger.warning("ElevenLabs voices unavailable for auto-casting: %s", exc)
            return []

    def synthesis_progress(self, project_id: str, chapter_id: str | None) -> dict:
        if not chapter_id:
            raise ValueError("chapter is required.")
        key = (self.safe_project_id(project_id), self.safe_chapter_id(chapter_id))
        with self._progress_lock:
            progress = dict(self._synthesis_progress.get(key, {}))
        return progress or {
            "phase": "idle",
            "total_chunks": 0,
            "completed_chunks": 0,
            "current_chunk": 0,
        }

    def update_synthesis_progress(self, project_id: str, chapter_id: str, patch: dict) -> None:
        key = (self.safe_project_id(project_id), self.safe_chapter_id(chapter_id))
        with self._progress_lock:
            current = self._synthesis_progress.get(
                key,
                {"phase": "starting", "total_chunks": 0, "completed_chunks": 0, "current_chunk": 0},
            )
            self._synthesis_progress[key] = current | patch

    def load_chapter_manifests(self, project_id: str) -> list[dict]:
        paths = self.store.paths(project_id)
        rows = []
        for index, manifest_path in enumerate(sorted(paths.source.glob("*.manifest.json"))):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("order", index)
            chapter_id = manifest.get("chapter_id", "")
            manifest["has_memory"] = (paths.memory / "chapters" / f"{chapter_id}.json").exists()
            manifest["has_annotations"] = (paths.annotations / f"{chapter_id}.jsonl").exists()
            manifest["has_audio"] = (paths.audio / f"{chapter_id}.mp3").exists()
            manifest.setdefault("analyzed", manifest["has_memory"])
            manifest.setdefault("annotated", manifest["has_annotations"])
            manifest.setdefault(
                "pipeline_state",
                "complete" if manifest["analyzed"] and manifest["annotated"] else "pending",
            )
            manifest.setdefault("pipeline_message", "")
            rows.append(manifest)
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("order", 0)),
                natural_sort_key(str(row.get("chapter_id", ""))),
            ),
        )

    def require_previous_chapter_analysis(self, project_id: str, chapter_id: str) -> None:
        chapters = self.load_chapter_manifests(project_id)
        for index, chapter in enumerate(chapters):
            if chapter.get("chapter_id") != chapter_id:
                continue
            if index == 0:
                return
            previous = chapters[index - 1]
            if previous.get("analyzed"):
                return
            self.update_chapter_status(
                project_id,
                chapter_id,
                pipeline_state="paused",
                pipeline_message=f"Waiting for prior chapter analysis: {previous.get('title') or previous['chapter_id']}",
            )
            self.mark_chapters_paused_after(
                project_id,
                chapter_id,
                f"Waiting for prior chapter analysis: {previous.get('title') or previous['chapter_id']}",
            )
            raise ValueError(
                "Analyze the previous chapter first so this chapter has the required context: "
                f"{previous.get('title') or previous['chapter_id']}"
            )

    def mark_chapters_paused_after(self, project_id: str, chapter_id: str, message: str) -> None:
        found = False
        for chapter in self.load_chapter_manifests(project_id):
            if chapter.get("chapter_id") == chapter_id:
                found = True
                continue
            if found and not chapter.get("analyzed"):
                self.update_chapter_status(
                    project_id,
                    chapter["chapter_id"],
                    pipeline_state="paused",
                    pipeline_message=message,
                )

    def cancel_pipeline_from(self, project_id: str, chapter_id: str) -> dict:
        found = False
        canceled = []
        for chapter in self.load_chapter_manifests(project_id):
            if chapter.get("chapter_id") == chapter_id:
                found = True
            if found and not (chapter.get("analyzed") and chapter.get("annotated")):
                self._canceled_chapters.add((project_id, chapter["chapter_id"]))
                self.update_chapter_status(
                    project_id,
                    chapter["chapter_id"],
                    pipeline_state="canceled",
                    pipeline_message=f"Canceled because {chapter_id} was canceled.",
                )
                canceled.append(chapter["chapter_id"])
        return {"ok": True, "canceled": canceled}

    def raise_if_chapter_canceled(self, project_id: str, chapter_id: str) -> None:
        if (project_id, chapter_id) in self._canceled_chapters:
            raise RuntimeError(f"Chapter analysis canceled: {chapter_id}")

    def update_chapter_status(self, project_id: str, chapter_id: str, **updates: bool) -> None:
        manifest_path = self.store.paths(project_id).source / f"{chapter_id}.manifest.json"
        if not manifest_path.exists():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(updates)
        self.store.write_json(manifest_path, manifest)

    def next_chapter_order(self, project_id: str, exclude_chapter_id: str | None = None) -> int:
        orders = [
            int(row.get("order", index))
            for index, row in enumerate(self.load_chapter_manifests(project_id))
            if row.get("chapter_id") != exclude_chapter_id
        ]
        return (max(orders) + 1) if orders else 0

    @staticmethod
    def safe_chapter_id(chapter_id: str) -> str:
        if not chapter_id or "/" in chapter_id or "\\" in chapter_id or ".." in chapter_id:
            raise ValueError("Invalid chapter_id.")
        return chapter_id

    @staticmethod
    def safe_project_id(project_id: str) -> str:
        if not project_id or "/" in project_id or "\\" in project_id or ".." in project_id:
            raise ValueError("Invalid project_id.")
        return project_id

    def unique_project_id(self, base_project_id: str, title: str) -> str:
        base_project_id = self.safe_project_id(base_project_id)
        project_id = base_project_id
        suffix = 2
        while True:
            config_path = self.store.paths(project_id).root / "project.json"
            if not config_path.exists():
                return project_id
            config = self.store.load_config(project_id)
            if config.title == title:
                return project_id
            project_id = f"{base_project_id}-{suffix}"
            suffix += 1


def make_handler(app: NarratorWebApp, auth_service: SupabaseAuthService | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/config":
                    if not auth_service:
                        raise RuntimeError("Supabase auth is not configured.")
                    return self.send_json(auth_service.public_config())
                active_app = self.active_app(parsed.path)
                if parsed.path == "/api/projects":
                    return self.send_json(active_app.list_projects())
                if parsed.path.startswith("/api/projects/"):
                    parts = decoded_path_parts(parsed.path)
                    if len(parts) == 5 and parts[3] == "audio":
                        return self.serve_audio_file(active_app, parts[2], parts[4])
                    if len(parts) == 4 and parts[3] == "download":
                        return self.serve_book_download(active_app, parts[2])
                    if len(parts) == 4 and parts[3] == "synthesis-progress":
                        chapter_id = parse_qs(parsed.query).get("chapter", [None])[0]
                        return self.send_json(active_app.synthesis_progress(parts[2], chapter_id))
                    project_id = unquote(parsed.path.split("/")[3])
                    chapter_id = parse_qs(parsed.query).get("chapter", [None])[0]
                    return self.send_json(active_app.project_payload(project_id, chapter_id))
                if parsed.path == "/api/elevenlabs/voices":
                    return self.send_json(active_app.elevenlabs_voices())
                return self.send_static(parsed.path)
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as exc:
                return self.send_error_json(exc)

        def do_POST(self) -> None:
            try:
                body = self.read_json()
                parts = decoded_path_parts(self.path)
                active_app = self.active_app(self.path)
                if parts == ["api", "projects"]:
                    return self.send_json(active_app.create_project(body))
                if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                    project_id = parts[2]
                    if len(parts) == 4 and parts[3] == "chapters":
                        return self.send_json(active_app.save_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "rename":
                        return self.send_json(active_app.rename_project(project_id, body))
                    if len(parts) == 4 and parts[3] == "chapters-reorder":
                        return self.send_json(active_app.reorder_chapters(project_id, body))
                    if len(parts) == 4 and parts[3] == "import":
                        return self.send_json(active_app.import_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "bulk-import":
                        return self.send_json(active_app.bulk_import(project_id, body))
                    if len(parts) == 4 and parts[3] == "config":
                        return self.send_json(active_app.update_config(project_id, body))
                    if len(parts) == 4 and parts[3] == "memory":
                        return self.send_json(active_app.save_memory(project_id, body))
                    if len(parts) == 5 and parts[3] == "chapter-memory":
                        return self.send_json(active_app.save_chapter_memory(project_id, parts[4], body))
                    if len(parts) == 5 and parts[3] == "annotations":
                        return self.send_json(active_app.save_annotations(project_id, parts[4], body))
                    if len(parts) == 5 and parts[3] == "annotated-text":
                        return self.send_json(active_app.save_annotated_text(project_id, parts[4], body))
                    if len(parts) == 5 and parts[3] == "reset-annotations":
                        return self.send_json(active_app.reset_annotations(project_id, parts[4]))
                    if len(parts) == 5 and parts[3] == "delete-chapter":
                        return self.send_json(active_app.delete_chapter(project_id, parts[4]))
                    if len(parts) == 5 and parts[3] == "cancel-pipeline":
                        return self.send_json(active_app.cancel_pipeline_from(project_id, parts[4]))
                    if len(parts) == 4 and parts[3] == "delete-project":
                        return self.send_json(active_app.delete_project(project_id))
                    if len(parts) == 4 and parts[3] == "cast":
                        return self.send_json(active_app.save_cast(project_id, body))
                    if len(parts) == 4 and parts[3] == "run":
                        return self.send_json(active_app.run_step(project_id, body["step"], body.get("chapter_id"), body))
                    if len(parts) == 6 and parts[3] == "audio" and parts[5] == "regenerate-chunk":
                        return self.send_json(active_app.regenerate_chunk(
                            project_id, parts[4],
                            int(body.get("chunk_index", 0)),
                            body.get("backend", "elevenlabs"),
                        ))
                raise ValueError("Unknown API route.")
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as exc:
                return self.send_error_json(exc)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.write_response(
                status,
                data,
                {
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(data)),
                },
            )

        def send_error_json(self, exc: Exception) -> None:
            try:
                status = 401 if isinstance(exc, PermissionError) else 500
                self.send_json({"ok": False, "error": str(exc)}, status)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def active_app(self, request_path: str) -> NarratorWebApp:
            if not request_path.startswith("/api/"):
                return app
            if not auth_service:
                return app
            user_id = auth_service.user_id_from_authorization(self.headers.get("Authorization"))
            return app.for_user(user_id)

        def send_static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            path = (STATIC_DIR / relative).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists():
                self.write_response(404, b"", {})
                return
            data = path.read_bytes()
            self.write_response(
                200,
                data,
                {
                    "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "Cache-Control": "no-store",
                    "Content-Length": str(len(data)),
                },
            )

        def serve_book_download(self, active_app: NarratorWebApp, project_id: str) -> None:
            chapters_param = parse_qs(urlparse(self.path).query).get("chapters", [None])[0]
            chapter_ids = [c.strip() for c in chapters_param.split(",") if c.strip()] if chapters_param else None
            try:
                data, filename = active_app.download_book_audio(project_id, chapter_ids)
            except ValueError as exc:
                return self.send_error_json(exc)
            self.write_response(
                200,
                data,
                {
                    "Content-Type": "audio/mpeg",
                    "Content-Length": str(len(data)),
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-store",
                },
            )

        def serve_audio_file(self, active_app: NarratorWebApp, project_id: str, chapter_id: str) -> None:
            project_id = active_app.safe_project_id(project_id)
            chapter_id = active_app.safe_chapter_id(chapter_id)
            audio_path = active_app.store.paths(project_id).audio / f"{chapter_id}.mp3"
            if not audio_path.exists():
                self.write_response(404, b"", {})
                return
            file_size = audio_path.stat().st_size
            range_header = self.headers.get("Range", "")
            range_match = re.match(r"bytes=(\d*)-(\d*)", range_header) if range_header else None
            if range_match:
                start = int(range_match.group(1)) if range_match.group(1) else 0
                end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1
                with open(audio_path, "rb") as f:
                    f.seek(start)
                    data = f.read(length)
                self.write_response(
                    206,
                    data,
                    {
                        "Content-Type": "audio/mpeg",
                        "Content-Length": str(length),
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-store",
                    },
                )
            else:
                self.write_response(
                    200,
                    audio_path.read_bytes(),
                    {
                        "Content-Type": "audio/mpeg",
                        "Content-Length": str(file_size),
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-store",
                    },
                )

        def write_response(self, status: int, data: bytes, headers: dict[str, str]) -> None:
            try:
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if data:
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the audiobook narrator web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--projects-dir", type=Path, default=Path("projects"))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("NARRATION_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = NarratorWebApp(args.projects_dir)
    auth_service = SupabaseAuthService.from_env()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app, auth_service))
    print(f"Audiobook narrator UI running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
