from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from audiobook_narrator.analyze import update_story_memory
from audiobook_narrator.annotate import annotate_project
from audiobook_narrator.cast import build_cast
from audiobook_narrator.document_import import import_document_text, title_from_filename
from audiobook_narrator.ingest import ingest_chapter
from audiobook_narrator.models import Cast, Passage, StoryMemory
from audiobook_narrator.providers import ElevenLabsTTSProvider, get_llm_provider, get_tts_provider
from audiobook_narrator.storage import ProjectStore
from audiobook_narrator.synthesize import synthesize_chapter


STATIC_DIR = Path(__file__).resolve().parent / "frontend"


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
        project_id = body["project_id"].strip()
        title = body["title"].strip()
        language = body.get("language", "zh")
        config_path = self.store.paths(project_id).root / "project.json"
        if config_path.exists():
            return self.store.load_config(project_id).model_dump(mode="json")
        config = self.store.create_project(project_id, title, language)
        return config.model_dump(mode="json")

    def project_payload(self, project_id: str, chapter_id: str | None = None) -> dict:
        paths = self.store.paths(project_id)
        config = self.store.load_config(project_id)
        chapters = []
        for manifest_path in sorted(paths.source.glob("*.manifest.json")):
            chapters.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        selected = chapter_id or (chapters[0]["chapter_id"] if chapters else None)
        source_text = ""
        annotations = []
        if selected:
            source_path = paths.source / f"{selected}.txt"
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
            annotations = self.store.read_jsonl(paths.annotations / f"{selected}.jsonl")
        memory_path = paths.memory / "story.json"
        cast_path = paths.casts / "voices.json"
        return {
            "config": config.model_dump(mode="json"),
            "chapters": chapters,
            "selected_chapter_id": selected,
            "source_text": source_text,
            "memory": json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else None,
            "annotations": annotations,
            "cast": json.loads(cast_path.read_text(encoding="utf-8")) if cast_path.exists() else None,
        }

    def save_chapter(self, project_id: str, body: dict) -> dict:
        paths = self.store.paths(project_id)
        chapter_id = body["chapter_id"].strip()
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
        chapter_id = body.get("chapter_id") or Path(filename).stem
        title = body.get("title") or title_from_filename(filename)
        tmp = self.store.paths(project_id).root / "upload.tmp.txt"
        tmp.write_text(text, encoding="utf-8")
        try:
            manifest = ingest_chapter(self.store, project_id, tmp, title, chapter_id)
        finally:
            tmp.unlink(missing_ok=True)
        return {"ok": True, "manifest": manifest.model_dump(mode="json"), "text": text}

    def save_memory(self, project_id: str, body: dict) -> dict:
        memory = StoryMemory.model_validate(body)
        self.store.save_memory(project_id, memory)
        return {"ok": True, "memory": memory.model_dump(mode="json")}

    def save_annotations(self, project_id: str, chapter_id: str, body: dict) -> dict:
        passages = [Passage.model_validate(row) for row in body.get("annotations", [])]
        self.store.write_jsonl(self.store.paths(project_id).annotations / f"{chapter_id}.jsonl", passages)
        return {"ok": True, "count": len(passages)}

    def save_cast(self, project_id: str, body: dict) -> dict:
        cast = Cast.model_validate(body)
        self.store.write_json(self.store.paths(project_id).casts / "voices.json", cast)
        return {"ok": True, "cast": cast.model_dump(mode="json")}

    def run_step(self, project_id: str, step: str, chapter_id: str | None, body: dict) -> dict:
        if step == "analyze":
            memory = update_story_memory(self.store, project_id, get_llm_provider(not body.get("no_openai")))
            return {"ok": True, "memory": memory.model_dump(mode="json")}
        if step == "annotate":
            annotated = annotate_project(self.store, project_id, get_llm_provider(not body.get("no_openai")))
            return {"ok": True, "chapters": {key: len(value) for key, value in annotated.items()}}
        if step == "cast":
            cast = build_cast(self.store, project_id)
            return {"ok": True, "cast": cast.model_dump(mode="json")}
        if step == "synthesize":
            if not chapter_id:
                raise ValueError("chapter_id is required for synthesis.")
            backend = body.get("backend", "script")
            extension = ".mp3" if backend in {"elevenlabs", "openai"} else ".aiff" if backend == "macos_say" else ".txt"
            ssml, audio = synthesize_chapter(
                self.store, project_id, chapter_id, get_tts_provider(backend), extension
            )
            return {"ok": True, "ssml": ssml, "output": audio}
        raise ValueError(f"Unknown step: {step}")

    def elevenlabs_voices(self) -> dict:
        voices = ElevenLabsTTSProvider().list_voices()
        return {"voices": voices}


def make_handler(app: NarratorWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/projects":
                    return self.send_json(app.list_projects())
                if parsed.path.startswith("/api/projects/"):
                    project_id = parsed.path.split("/")[3]
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
                parts = urlparse(self.path).path.strip("/").split("/")
                if parts == ["api", "projects"]:
                    return self.send_json(app.create_project(body))
                if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                    project_id = parts[2]
                    if len(parts) == 4 and parts[3] == "chapters":
                        return self.send_json(app.save_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "import":
                        return self.send_json(app.import_chapter(project_id, body))
                    if len(parts) == 4 and parts[3] == "memory":
                        return self.send_json(app.save_memory(project_id, body))
                    if len(parts) == 5 and parts[3] == "annotations":
                        return self.send_json(app.save_annotations(project_id, parts[4], body))
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
    app = NarratorWebApp(args.projects_dir)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"Audiobook narrator UI running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
