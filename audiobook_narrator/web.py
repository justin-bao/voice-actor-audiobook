from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import shutil
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from audiobook_narrator.analyze import update_story_memory
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
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
    def __init__(self, projects_dir: Path) -> None:
        self.store = ProjectStore(projects_dir)

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

    def delete_project(self, project_id: str) -> dict:
        project_id = self.safe_project_id(project_id)
        root = self.store.paths(project_id).root
        if root.exists():
            shutil.rmtree(root)
        return {"ok": True, "project_id": project_id}

    def project_payload(self, project_id: str, chapter_id: str | None = None) -> dict:
        paths = self.store.paths(project_id)
        config = self.store.load_config(project_id)
        chapters = []
        for manifest_path in sorted(paths.source.glob("*.manifest.json")):
            chapters.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        selected = chapter_id or (chapters[0]["chapter_id"] if chapters else None)
        source_text = ""
        annotations = []
        annotated_text = ""
        if selected:
            source_path = paths.source / f"{selected}.txt"
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
            annotations = self.store.read_jsonl(paths.annotations / f"{selected}.jsonl")
            annotated_path = paths.source / f"{selected}.annotated.txt"
            if annotated_path.exists():
                annotated_text = annotated_path.read_text(encoding="utf-8")
        memory_path = paths.memory / "story.json"
        cast_path = paths.casts / "voices.json"
        return {
            "config": config.model_dump(mode="json"),
            "chapters": chapters,
            "selected_chapter_id": selected,
            "source_text": source_text,
            "memory": json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else None,
            "annotations": annotations,
            "annotated_text": annotated_text,
            "cast": json.loads(cast_path.read_text(encoding="utf-8")) if cast_path.exists() else None,
        }

    def save_chapter(self, project_id: str, body: dict) -> dict:
        paths = self.store.paths(project_id)
        chapter_id = self.safe_chapter_id(body["chapter_id"].strip())
        title = body.get("title", chapter_id).strip() or chapter_id
        source_path = paths.source / f"{chapter_id}.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(body.get("text", ""), encoding="utf-8")
        manifest = {
            "chapter_id": chapter_id,
            "title": title,
            "source_path": str(source_path),
            "char_count": len(body.get("text", "")),
        }
        self.store.write_json(paths.source / f"{chapter_id}.manifest.json", manifest)
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
        return {"ok": True, "manifest": manifest.model_dump(mode="json"), "text": text}

    def bulk_import(self, project_id: str, body: dict) -> dict:
        files = sorted(
            body.get("files", []),
            key=lambda row: natural_sort_key(str(row.get("filename", ""))),
        )
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

    def save_memory(self, project_id: str, body: dict) -> dict:
        memory = StoryMemory.model_validate(body)
        self.store.save_memory(project_id, memory)
        return {"ok": True, "memory": memory.model_dump(mode="json")}

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
        for path in [
            paths.annotations / f"{chapter_id}.jsonl",
            paths.source / f"{chapter_id}.annotated.txt",
            paths.scripts / f"{chapter_id}.ssml.xml",
        ]:
            if path.exists():
                path.unlink()
                removed.append(str(path))
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
        if step == "analyze":
            provider = get_llm_provider(not body.get("no_openai"))
            logger.info("Web step=analyze project=%s llm=%s", project_id, provider_summary(provider))
            with langfuse_observation(
                "audiobook-analyze",
                input={"project_id": project_id},
                metadata={"step": "analyze", "llm": provider_summary(provider)},
            ) as observation:
                memory = update_story_memory(self.store, project_id, provider)
                update_langfuse_observation(
                    observation,
                    output={
                        "chapters": len(memory.chapter_summaries),
                        "characters": len(memory.characters),
                    },
                )
                score_langfuse_current_trace(evaluate_analysis(memory))
            flush_langfuse()
            return {"ok": True, "memory": memory.model_dump(mode="json"), "llm": provider_summary(provider)}
        if step == "annotate":
            provider = get_llm_provider(not body.get("no_openai"))
            logger.info("Web step=annotate project=%s llm=%s", project_id, provider_summary(provider))
            with langfuse_observation(
                "audiobook-annotate",
                input={"project_id": project_id},
                metadata={"step": "annotate", "llm": provider_summary(provider)},
            ) as observation:
                annotated = annotate_project(self.store, project_id, provider)
                update_langfuse_observation(
                    observation,
                    output={"chapters": {key: len(value) for key, value in annotated.items()}},
                )
                passages = [passage for rows in annotated.values() for passage in rows]
                score_langfuse_current_trace(evaluate_annotations(passages))
            flush_langfuse()
            return {
                "ok": True,
                "chapters": {key: len(value) for key, value in annotated.items()},
                "llm": provider_summary(provider),
            }
        if step == "cast":
            cast = build_cast(self.store, project_id)
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
                ssml, audio = synthesize_chapter(
                    self.store, project_id, chapter_id, get_tts_provider(backend), extension
                )
                update_langfuse_observation(observation, output={"ssml": ssml, "output": audio})
            flush_langfuse()
            return {"ok": True, "ssml": ssml, "output": audio}
        raise ValueError(f"Unknown step: {step}")

    def elevenlabs_voices(self) -> dict:
        voices = ElevenLabsTTSProvider().list_voices()
        return {"voices": voices}

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


def make_handler(app: NarratorWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/projects":
                    return self.send_json(app.list_projects())
                if parsed.path.startswith("/api/projects/"):
                    project_id = unquote(parsed.path.split("/")[3])
                    chapter_id = parse_qs(parsed.query).get("chapter", [None])[0]
                    return self.send_json(app.project_payload(project_id, chapter_id))
                if parsed.path == "/api/elevenlabs/voices":
                    return self.send_json(app.elevenlabs_voices())
                return self.send_static(parsed.path)
            except Exception as exc:
                return self.send_error_json(exc)

        def do_POST(self) -> None:
            try:
                body = self.read_json()
                parts = decoded_path_parts(self.path)
                if parts == ["api", "projects"]:
                    return self.send_json(app.create_project(body))
                if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                    project_id = parts[2]
                    if len(parts) == 4 and parts[3] == "chapters":
                        return self.send_json(app.save_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "import":
                        return self.send_json(app.import_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "bulk-import":
                        return self.send_json(app.bulk_import(project_id, body))
                    if len(parts) == 4 and parts[3] == "memory":
                        return self.send_json(app.save_memory(project_id, body))
                    if len(parts) == 5 and parts[3] == "annotations":
                        return self.send_json(app.save_annotations(project_id, parts[4], body))
                    if len(parts) == 5 and parts[3] == "annotated-text":
                        return self.send_json(app.save_annotated_text(project_id, parts[4], body))
                    if len(parts) == 5 and parts[3] == "reset-annotations":
                        return self.send_json(app.reset_annotations(project_id, parts[4]))
                    if len(parts) == 5 and parts[3] == "delete-chapter":
                        return self.send_json(app.delete_chapter(project_id, parts[4]))
                    if len(parts) == 4 and parts[3] == "delete-project":
                        return self.send_json(app.delete_project(project_id))
                    if len(parts) == 4 and parts[3] == "cast":
                        return self.send_json(app.save_cast(project_id, body))
                    if len(parts) == 4 and parts[3] == "run":
                        return self.send_json(app.run_step(project_id, body["step"], body.get("chapter_id"), body))
                raise ValueError("Unknown API route.")
            except Exception as exc:
                return self.send_error_json(exc)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_error_json(self, exc: Exception) -> None:
            self.send_json({"ok": False, "error": str(exc)}, 500)

        def send_static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            path = (STATIC_DIR / relative).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists():
                self.send_response(404)
                self.end_headers()
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"Audiobook narrator UI running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
